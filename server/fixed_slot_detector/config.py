from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class PipelineConfig:
    color_names: List[str] = field(default_factory=lambda: ["pink", "yellow", "blue", "mint"])
    hsv_ranges: Dict[str, List[Tuple[Tuple[int, int, int], Tuple[int, int, int]]]] = field(
        default_factory=lambda: {
            "pink": [((145, 50, 60), (179, 255, 255))],
            "yellow": [((18, 90, 120), (40, 255, 255))],
            "blue": [((90, 40, 30), (130, 255, 255))],
            "mint": [((45, 15, 90), (95, 190, 255))],
        }
    )
    expected_cups_per_row: int = 4

    min_board_area: int = 40000
    inner_left_margin_ratio: float = 0.08
    inner_right_margin_ratio: float = 0.08
    inner_top_margin_ratio: float = 0.03
    inner_bottom_margin_ratio: float = 0.03
    divider_upper_gap_ratio: float = 0.18
    divider_lower_gap_ratio: float = 0.03
    min_color_area: int = 3000

    # Fixed-slot mode is better for the Pepper game because the board always
    # contains exactly 4 cup positions per row. It prevents missed edge cups
    # and avoids extra false-positive blobs from the background.
    use_fixed_slots: bool = True
    slot_count: int = 4
    slot_padding_ratio: float = 0.08
    min_slot_color_pixels: int = 250

    frame_stride: int = 1

    use_manual_board_roi: bool = False
    manual_board_roi: Tuple[int, int, int, int] = (476, 445, 1231, 1097)
    use_manual_divider: bool = False
    manual_divider_y: int = 526


DEFAULT_CONFIG = PipelineConfig()
