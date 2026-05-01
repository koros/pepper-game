from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

try:
    from fixed_slot_detector.config import PipelineConfig
    from fixed_slot_detector.detector import PepperCupDetector
except ModuleNotFoundError:
    from server.fixed_slot_detector.config import PipelineConfig
    from server.fixed_slot_detector.detector import PepperCupDetector


logger = logging.getLogger("vision")
COLOR_RANGES = {
    "red": [((0, 70, 50), (10, 255, 255)), ((170, 70, 50), (180, 255, 255))],
    "orange": [((11, 80, 60), (24, 255, 255))],
    "yellow": [((25, 70, 70), (35, 255, 255))],
    "green": [((36, 50, 50), (85, 255, 255))],
    "purple": [((131, 45, 45), (169, 255, 255))],
}
DETECTOR_COLOR_MAP = {
    "pink": "purple",
    "mint": "green",
}
PEPPER_CAMERA_INDEX = 0
PEPPER_CAMERA_RESOLUTION_CODE = 2
PEPPER_CAMERA_FPS = 5
PEPPER_CAMERA_COLOR_SPACE = 11


class VisionService:
    def __init__(
        self,
        pc_camera_index: int = 0,
        preview_enabled: bool = True,
        live_preview_enabled: bool = False,
        live_analysis_interval: float = 2.0,
        periodic_check_enabled: bool = False,
        periodic_check_interval: float = 7.0,
        save_dir: str = "captured_frames",
        window_name: str = "Pepper Cup Game Vision",
    ) -> None:
        logger.info(
            "Initializing vision service: pc_camera_index=%s preview_enabled=%s live_preview_enabled=%s live_analysis_interval=%s periodic_check_enabled=%s periodic_check_interval=%s save_dir=%s window_name=%s",
            pc_camera_index,
            preview_enabled,
            live_preview_enabled,
            live_analysis_interval,
            periodic_check_enabled,
            periodic_check_interval,
            save_dir,
            window_name,
        )
        self.pc_camera_index = pc_camera_index
        self.preview_enabled = preview_enabled
        self.live_preview_enabled = live_preview_enabled
        self.live_analysis_interval = live_analysis_interval
        self.periodic_check_enabled = periodic_check_enabled
        self.periodic_check_interval = periodic_check_interval
        self.next_periodic_check_at = 0.0
        self.save_dir = Path(save_dir)
        self.window_name = window_name
        self.frame_count = 0
        self.last_frame: Optional[np.ndarray] = None
        self.latest_live_frame: Optional[np.ndarray] = None
        self.latest_live_frame_at = 0.0
        self.preview_lock = threading.Lock()
        self.camera_lock = threading.Lock()
        self.preview_running = False
        self.live_running = False
        self.detector = PepperCupDetector(PipelineConfig())

        if self.preview_enabled:
            self.save_dir.mkdir(parents=True, exist_ok=True)
            self.preview_running = True
            preview_thread = threading.Thread(target=self.preview_loop, name="vision-preview")
            preview_thread.daemon = True
            preview_thread.start()
            logger.info("Vision preview thread started")
            if self.live_preview_enabled:
                self.live_running = True
                live_thread = threading.Thread(target=self.live_camera_loop, name="vision-live-camera")
                live_thread.daemon = True
                live_thread.start()
                logger.info("Vision live camera thread started")

    def decode_pepper_rgb(self, width: int, height: int, data: bytes) -> np.ndarray:
        logger.debug("Decoding Pepper RGB frame: width=%s height=%s bytes=%s", width, height, len(data))
        rgb_frame = np.frombuffer(data, dtype=np.uint8).reshape((height, width, 3))
        return cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR)

    def capture_pc_frame(self) -> Optional[np.ndarray]:
        if self.live_running:
            frame = self.wait_for_latest_live_frame()
            if frame is not None:
                logger.info("Using latest live PC camera frame for cup check: shape=%s", frame.shape)
                return frame
            logger.warning("No recent live PC camera frame available for cup check")
            return None

        logger.info("Opening PC camera index %s", self.pc_camera_index)
        with self.camera_lock:
            camera = cv2.VideoCapture(self.pc_camera_index)
            try:
                ok, frame = camera.read()
                if not ok:
                    logger.warning("PC camera capture failed")
                    return None
                logger.info("PC camera frame captured: shape=%s", frame.shape)
                return frame
            finally:
                camera.release()
                logger.info("PC camera released")

    def wait_for_latest_live_frame(self, max_age_seconds: float = 2.0, timeout_seconds: float = 1.5) -> Optional[np.ndarray]:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            with self.camera_lock:
                if self.latest_live_frame is not None and time.time() - self.latest_live_frame_at <= max_age_seconds:
                    return self.latest_live_frame.copy()
            time.sleep(0.05)
        return None

    def remember_live_frame(self, frame: np.ndarray) -> None:
        with self.camera_lock:
            self.latest_live_frame = frame.copy()
            self.latest_live_frame_at = time.time()

    def read_live_camera_frame(self, camera) -> Optional[np.ndarray]:
        with self.camera_lock:
            ok, frame = camera.read()
            if not ok:
                return None
            self.latest_live_frame = frame.copy()
            self.latest_live_frame_at = time.time()
            return frame

    def detect_cups(self, frame: np.ndarray, source: str = "camera") -> Dict[str, object]:
        logger.info("Running fixed-slot cup detection on frame shape=%s source=%s", frame.shape, source)
        height, width = frame.shape[:2]
        result = self.detect_cups_fixed_slots(frame)
        top = result["rows"]["top"]
        bottom = result["rows"]["bottom"]
        all_cups = result["cups"]
        logger.info("Detected top row: %s", top["sequence"])
        logger.info("Detected bottom row: %s", bottom["sequence"])
        self.log_detected_board(top["sequence"], bottom["sequence"])
        display_frame = result["annotated_frame"]
        self.draw_text_overlay(
            display_frame,
            self.preview_header_text(source, width, height),
            (12, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (140, 255, 160),
            thickness=1,
        )
        self.show_and_handle_keys(display_frame)
        return {
            "cup_count": len(all_cups),
            "source": source,
            "frame_width": width,
            "frame_height": height,
            "roi": result["roi"],
            "divider_y": result["divider_y"],
            "top_row": top["sequence"],
            "bottom_row": bottom["sequence"],
            "sequence": top["sequence"],
            "cups": all_cups,
            "rows": {
                "top": top,
                "bottom": bottom,
            },
        }

    def detect_cups_fixed_slots(self, frame: np.ndarray) -> Dict[str, object]:
        roi = self.detector.detect_board_roi(frame)
        x, y, w, h = roi
        roi_bgr = frame[y : y + h, x : x + w].copy()
        divider_y = self.detector.estimate_divider_y(roi_bgr)
        detections = self.detector.extract_cups_from_roi(roi_bgr, divider_y)

        annotated_roi = self.detector.annotate_roi(roi_bgr, detections, divider_y)
        annotated_frame = frame.copy()
        annotated_frame[y : y + h, x : x + w] = annotated_roi
        cv2.rectangle(annotated_frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

        rows = {
            "top": {"cup_count": 0, "boxes": [], "cups": [], "sequence": []},
            "bottom": {"cup_count": 0, "boxes": [], "cups": [], "sequence": []},
        }
        cups = []
        for detection in self.detector.order_row(detections, "upper") + self.detector.order_row(detections, "lower"):
            row = "top" if detection.row == "upper" else "bottom"
            local_x, local_y, box_w, box_h = detection.bbox
            box = (int(x + local_x), int(y + local_y), int(box_w), int(box_h))
            color = self.normalize_detector_color(detection.color)
            cup = {
                "box": box,
                "color": color,
                "raw_color": detection.color,
                "row": row,
                "score": float(detection.score),
            }
            cups.append(cup)
            rows[row]["boxes"].append(box)
            rows[row]["cups"].append(cup)
            rows[row]["sequence"].append(color)

        for row in rows.values():
            row["cup_count"] = len(row["cups"])

        return {
            "roi": roi,
            "divider_y": divider_y,
            "cups": cups,
            "rows": rows,
            "annotated_frame": annotated_frame,
        }

    def normalize_detector_color(self, color: str) -> str:
        return DETECTOR_COLOR_MAP.get(color, color)

    def log_detected_board(self, top_row: List[str], bottom_row: List[str]) -> None:
        slot_count = max(len(top_row), len(bottom_row), self.detector.config.expected_cups_per_row)
        top_cells = self.format_board_cells(top_row, slot_count)
        bottom_cells = self.format_board_cells(bottom_row, slot_count)
        divider = "|%s|" % "|".join(["-" * 10 for _ in range(slot_count)])
        banner = "=" * max(len(top_cells) + 9, 58)
        logger.warning(
            "\n%s\nCUP BOARD DETECTED\nTOP    %s\n       %s\nBOTTOM %s\n%s",
            banner,
            top_cells,
            divider,
            bottom_cells,
            banner,
        )

    def format_board_cells(self, row: List[str], slot_count: int) -> str:
        cells = []
        for index in range(slot_count):
            value = row[index] if index < len(row) else "?"
            cells.append((" " + str(value).upper() + " ").center(10))
        return "|%s|" % "|".join(cells)

    def detect_row(self, frame: np.ndarray, y_offset: int, row_name: str) -> Dict[str, object]:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        color_mask = np.zeros(frame.shape[:2], dtype=np.uint8)
        for ranges in COLOR_RANGES.values():
            for lower, upper in ranges:
                current = cv2.inRange(hsv, np.array(lower, dtype=np.uint8), np.array(upper, dtype=np.uint8))
                color_mask = cv2.bitwise_or(color_mask, current)

        kernel = np.ones((5, 5), np.uint8)
        color_mask = cv2.morphologyEx(color_mask, cv2.MORPH_OPEN, kernel)
        color_mask = cv2.morphologyEx(color_mask, cv2.MORPH_CLOSE, kernel)
        contours, _hierarchy = cv2.findContours(
            color_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        cup_candidates = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < 500:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            aspect = float(w) / float(h or 1)
            if 0.35 <= aspect <= 1.8 and h >= 25:
                cup_candidates.append((int(x), int(y + y_offset), int(w), int(h)))

        cup_candidates = sorted(cup_candidates, key=lambda item: item[0])
        detected = []
        for box in cup_candidates:
            color = self.detect_cup_color(frame, box, y_offset)
            if color:
                detected.append({"box": box, "color": color, "row": row_name})

        sequence = [cup["color"] for cup in detected]
        logger.info("%s row candidates: count=%s boxes=%s sequence=%s", row_name, len(cup_candidates), cup_candidates, sequence)
        return {
            "cup_count": len(detected),
            "boxes": cup_candidates,
            "cups": detected,
            "sequence": sequence,
        }

    def detect_cup_color(self, frame: np.ndarray, box: Tuple[int, int, int, int], y_offset: int = 0) -> Optional[str]:
        x, y, w, h = box
        local_y = y - y_offset
        roi = frame[local_y : local_y + h, x : x + w]
        if roi.size == 0:
            return None

        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        best_color = None
        best_score = 0
        for color, ranges in COLOR_RANGES.items():
            mask = None
            for lower, upper in ranges:
                current = cv2.inRange(hsv, np.array(lower, dtype=np.uint8), np.array(upper, dtype=np.uint8))
                mask = current if mask is None else cv2.bitwise_or(mask, current)
            score = int(cv2.countNonZero(mask))
            if score > best_score:
                best_color = color
                best_score = score

        min_pixels = max(int(w * h * 0.08), 20)
        if best_score < min_pixels:
            logger.info("Cup box %s had no confident color match; best=%s score=%s", box, best_color, best_score)
            return None
        logger.info("Cup box %s classified as %s score=%s", box, best_color, best_score)
        return best_color

    def draw_detections(
        self,
        frame: np.ndarray,
        cups: List[Dict[str, object]],
        split_y: Optional[int] = None,
        source: str = "camera",
    ) -> np.ndarray:
        display_frame = frame.copy()
        for index, cup in enumerate(cups, start=1):
            x, y, w, h = cup["box"]
            color = str(cup["color"])
            row = str(cup.get("row", "row"))
            cv2.rectangle(display_frame, (x, y), (x + w, y + h), (80, 230, 120), 1)
            self.draw_text_overlay(
                display_frame,
                "%s %s %s" % (row, index, color),
                (x, max(20, y - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.42,
                (130, 255, 150),
                thickness=1,
            )
        self.draw_text_overlay(
            display_frame,
            self.preview_header_text(source, frame.shape[1], frame.shape[0]),
            (12, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (140, 255, 160),
            thickness=1,
        )
        return display_frame

    def draw_text_overlay(
        self,
        frame: np.ndarray,
        text: str,
        origin: tuple,
        font_face: int,
        font_scale: float,
        text_color: tuple,
        thickness: int = 2,
        padding: int = 5,
        background_color: tuple = (0, 0, 0),
        background_alpha: float = 0.38,
    ) -> None:
        x, y = origin
        text_size, baseline = cv2.getTextSize(text, font_face, font_scale, thickness)
        text_width, text_height = text_size

        left = max(0, x - padding)
        top = max(0, y - text_height - padding)
        right = min(frame.shape[1], x + text_width + padding)
        bottom = min(frame.shape[0], y + baseline + padding)

        if right > left and bottom > top:
            roi = frame[top:bottom, left:right]
            backing = np.full(roi.shape, background_color, dtype=np.uint8)
            cv2.addWeighted(backing, background_alpha, roi, 1.0 - background_alpha, 0, roi)

        shadow_origin = (min(frame.shape[1] - 1, x + 1), min(frame.shape[0] - 1, y + 1))
        cv2.putText(frame, text, shadow_origin, font_face, font_scale, (0, 0, 0), thickness + 1, cv2.LINE_AA)
        cv2.putText(frame, text, origin, font_face, font_scale, text_color, thickness, cv2.LINE_AA)

    def show_and_handle_keys(self, frame: np.ndarray) -> None:
        with self.preview_lock:
            self.last_frame = frame.copy()

    def preview_loop(self) -> None:
        logger.info("Creating vision preview window: %s", self.window_name)
        cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
        placeholder = np.zeros((360, 640, 3), dtype=np.uint8)
        self.draw_text_overlay(
            placeholder,
            "Waiting for camera frame",
            (36, 184),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (255, 255, 255),
            thickness=2,
        )
        try:
            while self.preview_running:
                with self.preview_lock:
                    frame = self.last_frame.copy() if self.last_frame is not None else placeholder.copy()

                cv2.imshow(self.window_name, frame)
                key = cv2.waitKey(50) & 0xFF
                if key == ord("s"):
                    self.save_frame(frame)
                elif key == ord("q"):
                    logger.info("Vision preview disabled by user pressing q")
                    self.preview_enabled = False
                    self.preview_running = False
                time.sleep(0.01)
        finally:
            cv2.destroyWindow(self.window_name)

    def live_camera_loop(self) -> None:
        logger.info("Opening live PC camera preview index %s", self.pc_camera_index)
        camera = cv2.VideoCapture(self.pc_camera_index)
        next_analysis_at = 0.0
        try:
            while self.live_running and self.preview_enabled:
                frame = self.read_live_camera_frame(camera)
                if frame is None:
                    logger.warning("Live PC camera preview read failed")
                    time.sleep(0.5)
                    continue
                now = time.time()
                if now >= next_analysis_at:
                    if self.periodic_check_enabled:
                        logger.warning("Periodic dev vision check from PC webcam")
                        self.detect_cups(frame, source="pc-live-periodic")
                        next_analysis_at = now + self.periodic_check_interval
                    else:
                        self.detect_cups(frame, source="pc-live")
                        next_analysis_at = now + self.live_analysis_interval
                else:
                    self.show_and_handle_keys(self.draw_preview_frame(frame, source="pc-live"))
                time.sleep(0.2)
        finally:
            camera.release()
            logger.info("Live PC camera preview released")

    def draw_preview_frame(self, frame: np.ndarray, source: str = "camera") -> np.ndarray:
        display_frame = frame.copy()
        self.draw_text_overlay(
            display_frame,
            self.preview_header_text(source, frame.shape[1], frame.shape[0]),
            (12, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (140, 255, 160),
            thickness=1,
        )
        return display_frame

    def preview_header_text(self, source: str, width: int, height: int) -> str:
        if source.startswith("pepper"):
            return (
                "%s %sx%s | cam=%s res=%s fps=%s color=%s | s save"
                % (
                    source,
                    width,
                    height,
                    PEPPER_CAMERA_INDEX,
                    PEPPER_CAMERA_RESOLUTION_CODE,
                    PEPPER_CAMERA_FPS,
                    PEPPER_CAMERA_COLOR_SPACE,
                )
            )
        return "%s %sx%s | s save" % (source, width, height)

    def save_frame(self, frame: np.ndarray) -> None:
        filename = self.save_dir / ("pepper_cup_frame_%s.jpg" % self.frame_count)
        cv2.imwrite(str(filename), frame)
        logger.info("Saved vision frame: %s", filename)
        self.frame_count += 1

    def stop(self) -> None:
        logger.info("Stopping vision preview")
        self.live_running = False
        self.preview_running = False
        self.preview_enabled = False

    def check_pepper_frame(self, width: int, height: int, data: bytes) -> Dict[str, object]:
        logger.info("Checking Pepper camera frame")
        return self.detect_cups(self.decode_pepper_rgb(width, height, data), source="pepper")

    def preview_pepper_frame(self, width: int, height: int, data: bytes) -> None:
        frame = self.decode_pepper_rgb(width, height, data)
        if self.should_run_periodic_check():
            logger.warning("Periodic dev vision check from Pepper preview stream")
            self.detect_cups(frame, source="pepper-periodic")
            return
        self.show_and_handle_keys(self.draw_preview_frame(frame, source="pepper-preview"))

    def should_run_periodic_check(self) -> bool:
        if not self.periodic_check_enabled:
            return False
        now = time.time()
        if now < self.next_periodic_check_at:
            return False
        self.next_periodic_check_at = now + self.periodic_check_interval
        return True

    def check_pc_camera(self) -> Dict[str, object]:
        logger.info("Checking PC camera frame")
        frame = self.capture_pc_frame()
        if frame is None:
            logger.warning("Returning zero cups because PC camera is unavailable")
            return {"cup_count": 0, "boxes": [], "error": "PC camera unavailable"}
        return self.detect_cups(frame, source="pc")
