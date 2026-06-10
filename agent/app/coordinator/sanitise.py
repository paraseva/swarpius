from __future__ import annotations

import json
import re
from typing import Optional

# ── Chat text sanitisation ───────────────────────────────────────

_CHAT_LEAK_KEYS = (
    "awaiting_user_response",
    "selected_skill",
    "tool_parameters",
    "problem_description",
    "detailed_information",
)
_CHAT_LEAK_MARKER_PATTERN = re.compile(
    rf'\\?"?(?:{"|".join(_CHAT_LEAK_KEYS)})\\?"?\s*(?::|=|>)',
    re.IGNORECASE,
)
_CHAT_RESPONSE_JSON_PATTERN = re.compile(
    r'\\?"?chat_response\\?"?\s*:\s*\\?"(?P<value>(?:[^"\\]|\\.)*)\\?"?',
    re.IGNORECASE,
)


def sanitise_agent_chat_text(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None

    cleaned = text.strip()
    if not cleaned:
        return cleaned

    cleaned = cleaned.replace('\\"', '"').replace("\\n", "\n")

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            chat_value = parsed.get("chat_response")
            if isinstance(chat_value, str) and chat_value.strip():
                return chat_value.strip()
    except Exception:  # noqa: BLE001
        pass

    marker_match = _CHAT_LEAK_MARKER_PATTERN.search(cleaned)
    if marker_match:
        prefix = cleaned[: marker_match.start()].rstrip(" \t\r\n,;:{[<\"'")
        if prefix:
            return prefix
        extracted = _CHAT_RESPONSE_JSON_PATTERN.search(cleaned)
        if extracted:
            value = extracted.group("value")
            return value.replace('\\"', '"').replace("\\n", "\n").strip()

    return cleaned


_SUMMARY_PATTERN = re.compile(
    r"<summary>\s*(.*?)\s*</summary>",
    re.DOTALL,
)
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
# Paired tag block, e.g. <list>...</list>, <extended_info>...</extended_info>.
# Non-greedy + DOTALL; iterate to handle same-name nesting (multi-disc lists).
_BLOCK_TAG_PATTERN = re.compile(
    r"<(\w+)\b[^>]*>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)


_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FA6F"  # chess symbols
    "\U0001FA70-\U0001FAFF"  # symbols extended-A
    "\U00002702-\U000027B0"  # dingbats
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0000200D"             # zero-width joiner
    "\U000020E3"             # combining enclosing keycap
    "\U00002600-\U000026FF"  # misc symbols
    "\U0000231A-\U0000231B"  # watch, hourglass
    "\U00002934-\U00002935"  # arrows
    "\U000025AA-\U000025AB"  # squares
    "\U000025FB-\U000025FE"  # squares
    "\U00002B05-\U00002B07"  # arrows
    "\U00002B1B-\U00002B1C"  # squares
    "\U00002B50"             # star
    "\U00002B55"             # circle
    "\U00003030"             # wavy dash
    "\U000000A9"             # copyright
    "\U000000AE"             # registered
    "\U00002122"             # trademark
    "]+",
    flags=re.UNICODE,
)

# Markdown patterns that aren't speakable
_MD_BOLD_ITALIC = re.compile(r"\*{1,3}(.+?)\*{1,3}")
_MD_HEADER = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_CODE_BLOCK = re.compile(r"```[\s\S]*?```")
_MD_INLINE_CODE = re.compile(r"`([^`]+)`")
_MD_BULLET = re.compile(r"^[-*]\s+", re.MULTILINE)
_MD_NUMBERED = re.compile(r"^\d+[.)]\s+", re.MULTILINE)
_MD_BLOCKQUOTE = re.compile(r"^>\s?", re.MULTILINE)
_MD_HORIZONTAL_RULE = re.compile(r"^[-*_]{3,}$", re.MULTILINE)
_ARROW_SYMBOL = re.compile(r"\s*→\s*")

# Smart truncation thresholds (matching frontend)
_TTS_MAX_FULL_SPEAK_CHARS = 320
_TTS_MAX_FULL_SPEAK_LIST_LINES = 3
_TTS_FIRST_SENTENCE = re.compile(r".+?[.!?](\s|$)")


def _is_likely_long_list(text: str) -> bool:
    lines = text.split("\n")
    list_lines = sum(1 for line in lines if re.match(r"^\d+[).\s]|^[-*]\s", line.strip()))
    return list_lines > _TTS_MAX_FULL_SPEAK_LIST_LINES


# Innermost paired tag — same-tag negative lookahead ensures the
# non-greedy body doesn't span across a nested opener of the same
# tag (multi-disc <list><list>... and any future same-tag nesting).
# Different-tag nesting resolves naturally across iterations.
# ``<summary>`` is excluded so it survives as a child marker for the
# block renderer to consume.
_BLOCK_TAG_PATTERN_FOR_CLI = re.compile(
    r"<(?!summary\b)(?P<tag>\w+)\b[^>]*>"
    r"(?P<body>(?:(?!<(?P=tag)\b).)*?)"
    r"</(?P=tag)>",
    re.DOTALL,
)


def render_block_tags_for_cli(text: Optional[str]) -> Optional[str]:
    """Render HTML-style block tags for CLI display.

    Any ``<tag>...</tag>`` block (``<extended_info>``, ``<list>``,
    and any future paired tag) is stripped of its wrapping tags;
    if it contains a ``<summary>...</summary>`` child, the summary
    text becomes a bold Rich header above the body — matching the
    collapsible-widget treatment the WS frontend uses. Iterates so
    cross-tag and same-tag nesting both resolve: innermost blocks
    render first, then their rendered text becomes the body of the
    outer block. Orphan / self-closing tags left over from
    malformed markup are stripped at the end.

    Returns Rich-markup-bearing text — callers feed this into a
    ``rich.console.Console.print`` (or a ``rich.panel.Panel`` body),
    not into plain stdout, so the markup gets interpreted.
    """
    if text is None:
        return None
    if not text:
        return text

    def _replace_block(match: re.Match) -> str:
        raw = match.group("body").strip()
        summary_match = _SUMMARY_PATTERN.search(raw)
        summary_text = ""
        if summary_match:
            summary_text = summary_match.group(1).strip()
            raw = _SUMMARY_PATTERN.sub("", raw, count=1).strip()
        parts: list[str] = []
        if summary_text:
            parts.append(f"[bold]▸ {summary_text}[/bold]")
        if raw:
            parts.append(raw)
        if not parts:
            return ""
        return "\n\n" + "\n".join(parts) + "\n\n"

    # Bounded iteration — eight levels is more than any realistic
    # nest (multi-disc is two). Hitting the bound means input is
    # malformed; leave residual tags rather than spin forever.
    for _ in range(8):
        new_text, n = _BLOCK_TAG_PATTERN_FOR_CLI.subn(_replace_block, text)
        if n == 0:
            break
        text = new_text

    # Strip any remaining tag-shaped fragments (orphan opens, stray
    # closes, free-floating <summary>, future self-closing tags).
    text = _HTML_TAG_PATTERN.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def sanitise_for_tts(text: Optional[str]) -> Optional[str]:
    """Strip emojis, markdown formatting, and other non-speakable content for TTS.

    For long responses or list-heavy output, returns only the first sentence.
    Returns None if there is nothing speakable.
    """
    if not text:
        return None

    cleaned = text

    # Strip any <tag>...</tag> block entirely (not speakable: extended_info,
    # list, summary, future display tags). Iterate to converge through
    # same-name nesting (e.g. multi-disc <list><list></list></list>).
    for _ in range(8):
        new_cleaned = _BLOCK_TAG_PATTERN.sub("", cleaned)
        if new_cleaned == cleaned:
            break
        cleaned = new_cleaned
    # Strip any remaining orphan tags (self-closing, unmatched close, etc.)
    cleaned = _HTML_TAG_PATTERN.sub("", cleaned)

    # Remove code blocks entirely (not speakable)
    cleaned = _MD_CODE_BLOCK.sub("", cleaned)

    # Horizontal rules → blank line (before bullet stripping)
    cleaned = _MD_HORIZONTAL_RULE.sub("", cleaned)

    cleaned = _MD_BOLD_ITALIC.sub(r"\1", cleaned)
    cleaned = _MD_HEADER.sub("", cleaned)
    cleaned = _MD_LINK.sub(r"\1", cleaned)
    cleaned = _MD_INLINE_CODE.sub(r"\1", cleaned)
    cleaned = _MD_BLOCKQUOTE.sub("", cleaned)
    cleaned = _MD_BULLET.sub("", cleaned)
    cleaned = _MD_NUMBERED.sub("", cleaned)

    # Arrow symbol → comma (common in alias listings)
    cleaned = _ARROW_SYMBOL.sub(", ", cleaned)

    cleaned = _EMOJI_PATTERN.sub("", cleaned)

    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    # Check for long lists before final cleanup (markers already stripped)
    is_long_list = _is_likely_long_list(text)

    cleaned = cleaned.strip()
    if not cleaned:
        return None

    # Smart truncation: long or listy responses → first sentence only
    if len(cleaned) > _TTS_MAX_FULL_SPEAK_CHARS or is_long_list:
        match = _TTS_FIRST_SENTENCE.match(cleaned)
        if match:
            return match.group(0).strip()
        return None

    return cleaned
