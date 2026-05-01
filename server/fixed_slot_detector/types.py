from dataclasses import dataclass
from typing import Tuple


@dataclass
class CupDetection:
    color: str
    cx: float
    cy: float
    bbox: Tuple[int, int, int, int]
    area: int
    aspect_ratio: float
    row: str
    score: float
