from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np


logger = logging.getLogger("vision")
COLOR_RANGES = {
    "red": [((0, 70, 50), (10, 255, 255)), ((170, 70, 50), (180, 255, 255))],
    "orange": [((11, 80, 60), (24, 255, 255))],
    "yellow": [((25, 70, 70), (35, 255, 255))],
    "green": [((36, 50, 50), (85, 255, 255))],
    "blue": [((86, 50, 50), (130, 255, 255))],
    "purple": [((131, 45, 45), (169, 255, 255))],
}


class VisionService:
    def __init__(
        self,
        pc_camera_index: int = 0,
        preview_enabled: bool = True,
        live_preview_enabled: bool = False,
        live_analysis_interval: float = 2.0,
        save_dir: str = "captured_frames",
        window_name: str = "Pepper Cup Game Vision",
    ) -> None:
        logger.info(
            "Initializing vision service: pc_camera_index=%s preview_enabled=%s live_preview_enabled=%s live_analysis_interval=%s save_dir=%s window_name=%s",
            pc_camera_index,
            preview_enabled,
            live_preview_enabled,
            live_analysis_interval,
            save_dir,
            window_name,
        )
        self.pc_camera_index = pc_camera_index
        self.preview_enabled = preview_enabled
        self.live_preview_enabled = live_preview_enabled
        self.live_analysis_interval = live_analysis_interval
        self.save_dir = Path(save_dir)
        self.window_name = window_name
        self.frame_count = 0
        self.last_frame: Optional[np.ndarray] = None
        self.preview_lock = threading.Lock()
        self.preview_running = False
        self.live_running = False

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

    def decode_bgr(self, width: int, height: int, data: bytes) -> np.ndarray:
        logger.debug("Decoding BGR frame: width=%s height=%s bytes=%s", width, height, len(data))
        return np.frombuffer(data, dtype=np.uint8).reshape((height, width, 3))

    def capture_pc_frame(self) -> Optional[np.ndarray]:
        logger.info("Opening PC camera index %s", self.pc_camera_index)
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

    def detect_cups(self, frame: np.ndarray, source: str = "camera") -> Dict[str, object]:
        logger.info("Running two-row cup detection on frame shape=%s source=%s", frame.shape, source)
        height = frame.shape[0]
        width = frame.shape[1]
        split_y = height // 2
        top = self.detect_row(frame[:split_y, :], 0, "top")
        bottom = self.detect_row(frame[split_y:, :], split_y, "bottom")
        all_cups = top["cups"] + bottom["cups"]
        logger.info("Detected top row: %s", top["sequence"])
        logger.info("Detected bottom row: %s", bottom["sequence"])
        display_frame = self.draw_detections(frame, all_cups, split_y, source)
        self.show_and_handle_keys(display_frame)
        return {
            "cup_count": len(all_cups),
            "source": source,
            "frame_width": width,
            "frame_height": height,
            "top_row": top["sequence"],
            "bottom_row": bottom["sequence"],
            "sequence": top["sequence"],
            "cups": all_cups,
            "rows": {
                "top": top,
                "bottom": bottom,
            },
        }

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
        if split_y is not None:
            cv2.line(display_frame, (0, split_y), (display_frame.shape[1], split_y), (255, 255, 255), 1)
        for index, cup in enumerate(cups, start=1):
            x, y, w, h = cup["box"]
            color = str(cup["color"])
            row = str(cup.get("row", "row"))
            cv2.rectangle(display_frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
            self.draw_text_overlay(
                display_frame,
                "%s %s %s" % (row, index, color),
                (x, max(20, y - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (0, 255, 0),
                thickness=2,
            )
        self.draw_text_overlay(
            display_frame,
            "%s %sx%s | Press s to save current frame" % (source, frame.shape[1], frame.shape[0]),
            (12, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (255, 255, 255),
            thickness=2,
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
        padding: int = 6,
        background_color: tuple = (0, 0, 0),
        background_alpha: float = 0.72,
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
        cv2.putText(frame, text, shadow_origin, font_face, font_scale, (0, 0, 0), thickness + 1)
        cv2.putText(frame, text, origin, font_face, font_scale, text_color, thickness)

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
                ok, frame = camera.read()
                if not ok:
                    logger.warning("Live PC camera preview read failed")
                    time.sleep(0.5)
                    continue
                now = time.time()
                if now >= next_analysis_at:
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
        cv2.line(
            display_frame,
            (0, display_frame.shape[0] // 2),
            (display_frame.shape[1], display_frame.shape[0] // 2),
            (255, 255, 255),
            1,
        )
        self.draw_text_overlay(
            display_frame,
            "%s %sx%s | Press s to save current frame" % (source, frame.shape[1], frame.shape[0]),
            (12, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (255, 255, 255),
            thickness=2,
        )
        return display_frame

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
        return self.detect_cups(self.decode_bgr(width, height, data), source="pepper")

    def preview_pepper_frame(self, width: int, height: int, data: bytes) -> None:
        frame = self.decode_bgr(width, height, data)
        self.show_and_handle_keys(self.draw_preview_frame(frame, source="pepper-preview"))

    def check_pc_camera(self) -> Dict[str, object]:
        logger.info("Checking PC camera frame")
        frame = self.capture_pc_frame()
        if frame is None:
            logger.warning("Returning zero cups because PC camera is unavailable")
            return {"cup_count": 0, "boxes": [], "error": "PC camera unavailable"}
        return self.detect_cups(frame, source="pc")
