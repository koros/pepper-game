from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional


DEFAULT_COLORS = ["red", "blue", "orange", "yellow", "green", "purple"]
DEFAULT_TARGET = ["red", "blue", "orange", "yellow"]
COLOR_ALIASES = {
    "centre": "center",
    "cyan": "blue",
    "gold": "yellow",
    "golden": "yellow",
    "violet": "purple",
}
logger = logging.getLogger("cup_game")


@dataclass
class CupGame:
    target_sequence: List[str] = field(default_factory=lambda: list(DEFAULT_TARGET))
    allowed_colors: List[str] = field(default_factory=lambda: list(DEFAULT_COLORS))
    max_attempts: int = 8
    attempt_number: int = 0
    finished: bool = False
    history: List[Dict[str, object]] = field(default_factory=list)

    def start_round(self) -> Dict[str, object]:
        self.attempt_number = 0
        self.finished = False
        self.history = []
        logger.info("New sequence game started: target=%s max_attempts=%s", self.target_sequence, self.max_attempts)
        return {
            "round": "1",
            "target_length": len(self.target_sequence),
            "allowed_colors": list(self.allowed_colors),
            "max_attempts": self.max_attempts,
            "instruction": (
                "Arrange the top row of cups to match the hidden bottom row."
            )
        }

    def set_target_sequence(self, target_sequence: List[str]) -> None:
        normalized = [color.lower() for color in target_sequence]
        if len(normalized) == len(self.target_sequence) and all(color in self.allowed_colors for color in normalized):
            self.target_sequence = normalized
            logger.info("Target sequence updated from detected bottom row: %s", self.target_sequence)

    def classify_command(self, text: str) -> str:
        lowered = text.lower()
        if re.search(r"\b(ready|check|done|evaluate)\b", lowered):
            return "check"
        if re.search(r"\b(help|clue|assist|assistance)\b", lowered):
            return "hint"
        return "sequence"

    def parse_sequence(self, text: str) -> Optional[List[str]]:
        logger.info("Parsing color sequence from text: %s", text)
        lowered = text.lower()
        for alias, color in COLOR_ALIASES.items():
            lowered = re.sub(r"\b%s\b" % re.escape(alias), color, lowered)

        sequence = []
        for token in re.findall(r"[a-z]+", lowered):
            if token in self.allowed_colors:
                sequence.append(token)

        if not sequence:
            logger.info("No allowed color words found in text")
            return None
        logger.info("Parsed sequence: %s", sequence)
        return sequence

    def evaluate_sequence(self, player_sequence: List[str], target_sequence: Optional[List[str]] = None) -> Dict[str, object]:
        normalized = [color.lower() for color in player_sequence]
        target = [color.lower() for color in (target_sequence or self.target_sequence)]
        logger.info("Evaluating player sequence: %s", normalized)

        if len(normalized) != len(target):
            logger.info(
                "Sequence length mismatch: expected=%s actual=%s",
                len(target),
                len(normalized),
            )
            return {
                "status": "needs_sequence",
                "reply": "I need exactly %s cups in the top row." % len(target),
                "attempt": self.attempt_number,
                "target_length": len(target),
                "player_sequence": normalized,
            }

        unknown = [color for color in normalized if color not in self.allowed_colors]
        if unknown:
            logger.info("Sequence contains unknown colors: %s", unknown)
            return {
                "status": "needs_sequence",
                "reply": "I heard an unknown color. Please use: %s." % ", ".join(self.allowed_colors),
                "attempt": self.attempt_number,
                "target_length": len(self.target_sequence),
                "player_sequence": normalized,
            }

        exact_matches = sum(
            1 for player, expected in zip(normalized, target) if player == expected
        )
        target_counts = Counter(target)
        player_counts = Counter(normalized)
        color_matches = sum(min(player_counts[color], target_counts[color]) for color in player_counts)
        partial_matches = color_matches - exact_matches

        self.attempt_number += 1
        won = exact_matches == len(target)
        attempts_left = max(self.max_attempts - self.attempt_number, 0)
        self.finished = won or attempts_left == 0
        status = "won" if won else "lost" if self.finished else "continue"

        if won:
            reply = "Correct. You matched the full row in %s attempt%s." % (
                self.attempt_number,
                "" if self.attempt_number == 1 else "s",
            )
        elif self.finished:
            reply = (
                "Game over. You got %s correct. The hidden row was %s."
            ) % (exact_matches, ", ".join(target))
        else:
            reply = "You got %s correct." % exact_matches

        outcome = {
            "status": status,
            "reply": reply,
            "attempt": self.attempt_number,
            "attempts_left": attempts_left,
            "exact_matches": exact_matches,
            "partial_matches": partial_matches,
            "player_sequence": normalized,
            "target_length": len(target),
            "finished": self.finished,
            "is_correct": won,
        }
        self.history.append(outcome)
        logger.info("Sequence outcome: %s", outcome)
        return outcome

    def hint(self) -> Dict[str, object]:
        if not self.history:
            return {
                "status": "hint",
                "reply": "Try arranging the top row, then say ready.",
                "attempt": self.attempt_number,
            }

        last = self.history[-1]
        player_sequence = list(last.get("player_sequence", []))
        for index, (player, target) in enumerate(zip(player_sequence, self.target_sequence), start=1):
            if player == target:
                return {
                    "status": "hint",
                    "reply": "Hint. Position %s is correct." % index,
                    "attempt": self.attempt_number,
                    "position": index,
                    "color": player,
                }

        if player_sequence:
            return {
                "status": "hint",
                "reply": "Hint. None of the cups are in the correct position yet.",
                "attempt": self.attempt_number,
            }
        return {
            "status": "hint",
            "reply": "Try arranging the top row, then say ready.",
            "attempt": self.attempt_number,
        }

    def full_feedback(self, player_sequence: List[str]) -> Dict[str, object]:
        outcome = self.evaluate_sequence(player_sequence)
        exact_matches = int(outcome.get("exact_matches", 0))
        partial_matches = int(outcome.get("partial_matches", 0))
        outcome["reply"] = (
            "You got %s correct position%s and %s correct color%s in the wrong place."
        ) % (
            exact_matches,
            "" if exact_matches == 1 else "s",
            partial_matches,
            "" if partial_matches == 1 else "s",
        )
        return outcome

    def check_guess(self, text: str) -> Dict[str, object]:
        sequence = self.parse_sequence(text)
        if not sequence:
            return {
                "status": "needs_sequence",
                "reply": "Please tell me the cup colors from left to right.",
                "attempt": self.attempt_number,
                "target_length": len(self.target_sequence),
            }
        return self.evaluate_sequence(sequence)
