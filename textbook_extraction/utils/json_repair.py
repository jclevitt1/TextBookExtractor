"""Extract and repair JSON from Claude responses."""
import json
import re
from typing import Optional


def extract_json(text: str) -> Optional[dict]:
    """Extract JSON from Claude's response, handling markdown wrapping.

    Three-tier extraction:
    1. Direct parse
    2. Extract from markdown code block
    3. Find largest JSON object (first { to last })
    """
    # Tier 1: Direct parse
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # Tier 2: Markdown code block
    for pattern in [r'```json\s*(.*?)```', r'```\s*(.*?)```']:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                continue

    # Tier 3: First { to last }
    first_brace = text.find('{')
    last_brace = text.rfind('}')
    if first_brace != -1 and last_brace > first_brace:
        try:
            return json.loads(text[first_brace:last_brace + 1])
        except json.JSONDecodeError:
            pass

    return None
