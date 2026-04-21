"""Small utility helpers used across the core package."""

import re
import json
from typing import Optional


def _extract_json(text: str) -> Optional[dict]:
    """Strip <think> blocks and extract the first JSON object in `text`.

    Returns None on failure.
    """
    try:
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        return json.loads(text[start:end])
    except Exception:
        return None
