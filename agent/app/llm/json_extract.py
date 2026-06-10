"""Lenient JSON-object extractor for LLM text responses.

Models routinely wrap structured-output JSON in markdown code fences,
emit reasoning before the final object, or sprinkle prose around it.
Strict ``json.loads(response.text)`` breaks on all of these; this
helper finds the JSON object inside whatever the model returned.

Used by the interrupt arbiter and the diagnostic agent — both consume
a flat JSON-object response from a lightweight LLM call.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

# Flat JSON-object pattern: `{...}` with no nested braces. Both
# consumers (arbiter, diagnostic agent) have flat schemas — none of
# their fields is itself an object. A schema that adds nesting would
# need a balanced-brace parser instead.
_OBJECT_PATTERN = re.compile(r"\{[^{}]*\}")


def extract_json_object(text: Optional[str]) -> Dict[str, Any]:
    """Return the last JSON object found in ``text`` as a dict.

    Tolerates surrounding markdown fences, preamble reasoning, and
    other prose. Takes the *last* match — the convention for LLM
    output is that the model's final answer comes after any
    reasoning, and the last object is the committed decision.

    Raises:
        ValueError: input is empty/None or contains no JSON object.
        json.JSONDecodeError: a JSON-object-shaped substring was found
            but isn't valid JSON.
    """
    if not text:
        raise ValueError("empty input")
    matches = _OBJECT_PATTERN.findall(text)
    if not matches:
        raise ValueError("no JSON object found in response")
    return json.loads(matches[-1])
