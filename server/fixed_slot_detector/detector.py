from __future__ import annotations

from typing import Dict, List, Optional, Tuple
from itertools import permutations

import cv2
import numpy as np

from .config import PipelineConfig
from .types import CupDetection


DRAW_COLORS = {
    "pink": (255, 0, 255),
    "yellow": (0, 255, 255),
    "blue": (255, 120, 0),
    "mint": (120, 255, 120),
}


class PepperCupDetector:
    def __init__(self, config: PipelineConfig):
        self.config = config

    def cardboard_mask(self, bgr: np.ndarray) -> np.ndarray:
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        lower = np.array([5, 20, 40], dtype=np.uint8)
        upper = np.array([30, 210, 240], dtype=np.uint8)
        mask = cv2.inRange(hsv, lower, upper)
        kernel = np.ones((9, 9), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
        return mask

    def largest_component_bbox(self, mask: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
        best = None
        best_area = 0
        for i in range(1, num_labels):
            x, y, w, h, area = stats[i]
            if area >= self.config.min_board_area and area > best_area:
                best_area = area
                best = (int(x), int(y), int(w), int(h))
        return best

    def detect_board_roi(self, bgr: np.ndarray) -> Tuple[int, int, int, int]:
        if self.config.use_manual_board_roi:
            return self.config.manual_board_roi

        bbox = self.largest_component_bbox(self.cardboard_mask(bgr))
        if bbox is not None:
            return bbox

        h, w = bgr.shape[:2]
        return (int(w * 0.22), int(h * 0.22), int(w * 0.56), int(h * 0.58))

    def estimate_divider_y(self, roi_bgr: np.ndarray) -> int:
        if self.config.use_manual_divider:
            return self.config.manual_divider_y

        gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)
        sobel_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
        horizontal_strength = np.mean(np.abs(sobel_y), axis=1)

        h = roi_bgr.shape[0]
        low = int(h * 0.25)
        high = int(h * 0.75)
        return low + int(np.argmax(horizontal_strength[low:high]))

    def build_row_band_masks(self, roi_shape: Tuple[int, int, int], divider_y: int) -> Dict[str, np.ndarray]:
        h, w = roi_shape[:2]
        x0 = int(w * self.config.inner_left_margin_ratio)
        x1 = int(w * (1.0 - self.config.inner_right_margin_ratio))

        upper_y0 = int(h * self.config.inner_top_margin_ratio)
        upper_y1 = max(upper_y0 + 1, divider_y - int(h * self.config.divider_upper_gap_ratio))
        lower_y0 = min(h - 1, divider_y + int(h * self.config.divider_lower_gap_ratio))
        lower_y1 = int(h * (1.0 - self.config.inner_bottom_margin_ratio))

        upper_mask = np.zeros((h, w), dtype=np.uint8)
        lower_mask = np.zeros((h, w), dtype=np.uint8)
        upper_mask[upper_y0:upper_y1, x0:x1] = 255
        lower_mask[lower_y0:lower_y1, x0:x1] = 255
        return {"upper": upper_mask, "lower": lower_mask}

    def build_color_mask(self, hsv: np.ndarray, color_name: str) -> np.ndarray:
        ranges = self.config.hsv_ranges[color_name]
        mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
        for low, high in ranges:
            mask |= cv2.inRange(hsv, np.array(low, dtype=np.uint8), np.array(high, dtype=np.uint8))
        kernel = np.ones((7, 7), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        return mask

    def connected_regions(self, mask: np.ndarray) -> List[dict]:
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
        regions: List[dict] = []
        for i in range(1, num_labels):
            x, y, w, h, area = stats[i]
            if area < self.config.min_color_area:
                continue
            cx, cy = centroids[i]
            regions.append(
                {
                    "bbox": (int(x), int(y), int(w), int(h)),
                    "center": (float(cx), float(cy)),
                    "area": int(area),
                    "aspect_ratio": float(h) / max(float(w), 1.0),
                }
            )
        return regions

    @staticmethod
    def candidate_score(region: dict) -> float:
        area = region["area"]
        aspect = region["aspect_ratio"]
        aspect_bonus = min(max(aspect, 0.2), 2.5)
        return float(area) * aspect_bonus

    def best_region_for_color_in_row(self, hsv: np.ndarray, row_mask: np.ndarray, color_name: str) -> Optional[dict]:
        color_mask = self.build_color_mask(hsv, color_name)
        masked = cv2.bitwise_and(color_mask, row_mask)
        regions = self.connected_regions(masked)
        if not regions:
            return None
        regions = sorted(regions, key=self.candidate_score, reverse=True)
        best = regions[0].copy()
        best["score"] = self.candidate_score(best)
        return best

    def row_band_bounds(self, roi_shape: Tuple[int, int, int], divider_y: int) -> Dict[str, Tuple[int, int, int, int]]:
        """Return the searchable rectangle for each row as (x0, y0, x1, y1)."""
        h, w = roi_shape[:2]
        x0 = int(w * self.config.inner_left_margin_ratio)
        x1 = int(w * (1.0 - self.config.inner_right_margin_ratio))

        upper_y0 = int(h * self.config.inner_top_margin_ratio)
        upper_y1 = max(upper_y0 + 1, divider_y - int(h * self.config.divider_upper_gap_ratio))
        lower_y0 = min(h - 1, divider_y + int(h * self.config.divider_lower_gap_ratio))
        lower_y1 = int(h * (1.0 - self.config.inner_bottom_margin_ratio))

        return {
            "upper": (x0, upper_y0, x1, upper_y1),
            "lower": (x0, lower_y0, x1, lower_y1),
        }

    def dominant_color_in_slot(self, hsv: np.ndarray, slot_mask: np.ndarray) -> Optional[Tuple[str, int, Tuple[int, int, int, int]]]:
        """Classify one fixed slot by the color with the largest masked area."""
        best_color: Optional[str] = None
        best_pixels = 0
        best_bbox = (0, 0, 0, 0)

        for color_name in self.config.color_names:
            color_mask = self.build_color_mask(hsv, color_name)
            masked = cv2.bitwise_and(color_mask, slot_mask)
            pixels = int(cv2.countNonZero(masked))
            if pixels <= best_pixels:
                continue

            ys, xs = np.where(masked > 0)
            if len(xs) == 0 or len(ys) == 0:
                continue

            best_color = color_name
            best_pixels = pixels
            best_bbox = (
                int(xs.min()),
                int(ys.min()),
                int(xs.max() - xs.min() + 1),
                int(ys.max() - ys.min() + 1),
            )

        if best_color is None or best_pixels < self.config.min_slot_color_pixels:
            return None
        return best_color, best_pixels, best_bbox

    def extract_cups_by_fixed_slots(self, roi_bgr: np.ndarray, divider_y: int) -> List[CupDetection]:
        """
        Game-specific detector.

        The game always has exactly 4 cup positions per row and one cup of
        each known colour. This method:
        1. splits each row into 4 fixed slots,
        2. scores every colour inside every slot,
        3. chooses the best left-to-right colour assignment while using each
           colour only once per row.

        The unique-colour assignment is important for Pepper frames because
        the cardboard can look yellow. Without this, the first mint cup or the
        rightmost mint cup can be incorrectly classified as yellow.
        """
        hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)
        bounds = self.row_band_bounds(roi_bgr.shape, divider_y)
        detections: List[CupDetection] = []
        colors = list(self.config.color_names)

        for row_name in ["upper", "lower"]:
            x0, y0, x1, y1 = bounds[row_name]
            row_w = max(x1 - x0, 1)
            slot_w = row_w / float(self.config.slot_count)
            pad = int(slot_w * self.config.slot_padding_ratio)

            slot_rects: List[Tuple[int, int, int, int]] = []
            score_matrix: List[List[int]] = []
            bbox_matrix: List[List[Tuple[int, int, int, int]]] = []

            for slot_idx in range(self.config.slot_count):
                sx0 = int(x0 + slot_idx * slot_w) + pad
                sx1 = int(x0 + (slot_idx + 1) * slot_w) - pad
                sx0 = max(x0, min(sx0, x1 - 1))
                sx1 = max(sx0 + 1, min(sx1, x1))
                slot_rects.append((sx0, y0, sx1, y1))

                slot_mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
                slot_mask[y0:y1, sx0:sx1] = 255

                slot_scores: List[int] = []
                slot_bboxes: List[Tuple[int, int, int, int]] = []

                for color_name in colors:
                    color_mask = self.build_color_mask(hsv, color_name)
                    masked = cv2.bitwise_and(color_mask, slot_mask)
                    pixels = int(cv2.countNonZero(masked))

                    ys, xs = np.where(masked > 0)
                    if len(xs) > 0 and len(ys) > 0:
                        bbox = (
                            int(xs.min()),
                            int(ys.min()),
                            int(xs.max() - xs.min() + 1),
                            int(ys.max() - ys.min() + 1),
                        )
                    else:
                        bbox = (sx0, y0, sx1 - sx0, y1 - y0)

                    slot_scores.append(pixels)
                    slot_bboxes.append(bbox)

                score_matrix.append(slot_scores)
                bbox_matrix.append(slot_bboxes)

            # Find the best assignment where each known colour appears once.
            # For 4 cups this is only 24 permutations, so it is simple and fast.
            best_perm: Optional[Tuple[int, ...]] = None
            best_total = -1
            for perm in permutations(range(len(colors)), self.config.slot_count):
                total = sum(score_matrix[slot_idx][color_idx] for slot_idx, color_idx in enumerate(perm))
                if total > best_total:
                    best_total = total
                    best_perm = perm

            if best_perm is None:
                continue

            for slot_idx, color_idx in enumerate(best_perm):
                pixels = score_matrix[slot_idx][color_idx]
                if pixels < self.config.min_slot_color_pixels:
                    continue

                sx0, sy0, sx1, sy1 = slot_rects[slot_idx]
                detections.append(
                    CupDetection(
                        color=colors[color_idx],
                        cx=float((sx0 + sx1) / 2.0),
                        cy=float((sy0 + sy1) / 2.0),
                        bbox=bbox_matrix[slot_idx][color_idx],
                        area=int(pixels),
                        aspect_ratio=float((sy1 - sy0) / max((sx1 - sx0), 1)),
                        row=row_name,
                        score=float(pixels),
                    )
                )

        return detections

    def extract_cups_from_roi(self, roi_bgr: np.ndarray, divider_y: int) -> List[CupDetection]:
        if self.config.use_fixed_slots:
            return self.extract_cups_by_fixed_slots(roi_bgr, divider_y)

        hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)
        row_masks = self.build_row_band_masks(roi_bgr.shape, divider_y)
        detections: List[CupDetection] = []

        for row_name in ["upper", "lower"]:
            row_mask = row_masks[row_name]
            for color_name in self.config.color_names:
                best_region = self.best_region_for_color_in_row(hsv, row_mask, color_name)
                if best_region is None:
                    continue
                cx, cy = best_region["center"]
                detections.append(
                    CupDetection(
                        color=color_name,
                        cx=float(cx),
                        cy=float(cy),
                        bbox=best_region["bbox"],
                        area=int(best_region["area"]),
                        aspect_ratio=float(best_region["aspect_ratio"]),
                        row=row_name,
                        score=float(best_region["score"]),
                    )
                )
        return detections

    @staticmethod
    def order_row(detections: List[CupDetection], row_name: str) -> List[CupDetection]:
        row_items = [d for d in detections if d.row == row_name]
        row_items.sort(key=lambda d: d.cx)
        return row_items

    def row_sequence(self, detections: List[CupDetection], row_name: str) -> List[str]:
        return [d.color for d in self.order_row(detections, row_name)]

    def annotate_roi(self, roi_bgr: np.ndarray, detections: List[CupDetection], divider_y: int) -> np.ndarray:
        out = roi_bgr.copy()
        row_masks = self.build_row_band_masks(roi_bgr.shape, divider_y)

        for row_name, color in [("upper", (80, 230, 120)), ("lower", (80, 230, 120))]:
            mask = row_masks[row_name]
            ys, xs = np.where(mask > 0)
            if len(xs) > 0 and len(ys) > 0:
                cv2.rectangle(out, (int(xs.min()), int(ys.min())), (int(xs.max()), int(ys.max())), color, 1)

        for d in detections:
            x, y, w, h = d.bbox
            draw_color = tuple(int(channel) for channel in DRAW_COLORS[d.color])
            cv2.rectangle(out, (x, y), (x + w, y + h), draw_color, 2)
            cv2.circle(out, (int(d.cx), int(d.cy)), 4, draw_color, -1)
            label = f"{d.row[:1].upper()} {d.color}"
            self.draw_soft_label(out, label, (x, max(18, y - 8)), text_color=(130, 255, 150))

        upper_seq = self.row_sequence(detections, "upper")
        lower_seq = self.row_sequence(detections, "lower")
        match = (
            upper_seq == lower_seq
            and len(upper_seq) == self.config.expected_cups_per_row
            and len(lower_seq) == self.config.expected_cups_per_row
        )

        self.draw_soft_label(out, f"top: {', '.join(upper_seq) or '?'}", (14, 22), text_color=(140, 255, 160))
        self.draw_soft_label(out, f"bottom: {', '.join(lower_seq) or '?'}", (14, 46), text_color=(140, 255, 160))
        self.draw_soft_label(out, f"match: {match}", (14, 70), text_color=(140, 255, 160))
        return out

    @staticmethod
    def draw_soft_label(
        frame: np.ndarray,
        text: str,
        origin: Tuple[int, int],
        text_color: Tuple[int, int, int] = (140, 255, 160),
    ) -> None:
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.45
        thickness = 1
        padding = 4
        x, y = origin
        (text_w, text_h), baseline = cv2.getTextSize(text, font, font_scale, thickness)
        left = max(0, x - padding)
        top = max(0, y - text_h - padding)
        right = min(frame.shape[1], x + text_w + padding)
        bottom = min(frame.shape[0], y + baseline + padding)
        if right > left and bottom > top:
            roi = frame[top:bottom, left:right]
            backing = np.zeros(roi.shape, dtype=np.uint8)
            cv2.addWeighted(backing, 0.35, roi, 0.65, 0, roi)
        shadow = (min(frame.shape[1] - 1, x + 1), min(frame.shape[0] - 1, y + 1))
        cv2.putText(frame, text, shadow, font, font_scale, (0, 0, 0), thickness + 1, cv2.LINE_AA)
        cv2.putText(frame, text, origin, font, font_scale, text_color, thickness, cv2.LINE_AA)
