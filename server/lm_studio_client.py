from __future__ import annotations

import logging
import re

from openai import OpenAI


logger = logging.getLogger("lm_studio")
CUP_WORDS = ("left", "middle", "right")
BANNED_PHRASES = (
    "possible line",
    "possible response",
    "here are",
    "here is",
    "i'll choose",
    "i will choose",
    "number between",
    "guess again",
    "cup guessing game",
)


class LMStudioClient:
    def __init__(self, base_url: str, model: str) -> None:
        logger.info("Initializing LM Studio client: base_url=%s model=%s", base_url, model)
        self.model = model
        self.client = OpenAI(base_url=base_url, api_key="lm-studio")

    def polish_for_pepper(self, message: str) -> str:
        logger.info("Requesting LM Studio phrasing for message: %s", message)
        try:
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You rewrite robot speech for a cup game. "
                            "Return exactly one short sentence. "
                            "Do not explain. Do not list options. Do not add quotes. "
                            "Do not choose a cup. Do not change facts, cup positions, "
                            "counts, or instructions."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            "Rewrite this exact message for Pepper to say. "
                            "Keep the meaning unchanged:\n%s" % message
                        ),
                    },
                ],
                temperature=0.1,
                max_tokens=48,
            )
            result = completion.choices[0].message.content.strip()
            logger.info("LM Studio phrasing received: %s", result)
            if self.is_safe_rewrite(message, result):
                return self.clean_rewrite(result)
            logger.warning("LM Studio phrasing rejected; using original message")
            return message
        except Exception as exc:
            logger.warning("LM Studio phrasing failed; using original message. Error: %s", exc)
            return message

    def clean_rewrite(self, result: str) -> str:
        return result.strip().strip("\"'")

    def is_safe_rewrite(self, original: str, result: str) -> bool:
        cleaned = self.clean_rewrite(result)
        lowered = cleaned.lower()
        original_lowered = original.lower()
        if not cleaned:
            return False
        if "\n" in cleaned or "\r" in cleaned:
            return False
        if len(cleaned) > 180:
            return False
        if cleaned.startswith(("*", "-", "1.", "2.")):
            return False
        if any(phrase in lowered for phrase in BANNED_PHRASES):
            return False
        original_cups = {cup for cup in CUP_WORDS if re.search(r"\b%s\b" % cup, original_lowered)}
        result_cups = {cup for cup in CUP_WORDS if re.search(r"\b%s\b" % cup, lowered)}
        if original_cups and original_cups != result_cups:
            return False
        return True
