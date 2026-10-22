"""Text normalization that removes extraction noise without changing meaning.

Label text contains symbols that matter (degree signs, micro signs, plus/minus,
registered marks). That is why this module deliberately does NOT use NFKC
normalization, which would silently turn the micro sign into a Greek letter and
superscripts into plain digits. It only fixes things that are never meaningful.
"""
from __future__ import annotations

import re
import unicodedata

_LIGATURES = str.maketrans({
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
})
# Non-breaking and exotic spaces become plain spaces.
_SPACES = dict.fromkeys(map(ord, "     　\t"), " ")
# Zero-width characters and soft hyphens are invisible and never meaningful.
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MULTI_SPACE = re.compile(r" {2,}")
_MULTI_BLANK_LINES = re.compile(r"\n{3,}")


def normalize_text(text: str) -> str:
    """Clean multi-line text: keep line structure and all meaningful characters."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = text.translate(_LIGATURES).translate(_SPACES).translate(_INVISIBLE)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", text)
    lines = [_MULTI_SPACE.sub(" ", line).strip() for line in text.split("\n")]
    return _MULTI_BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def normalize_line(text: str) -> str:
    """Clean a single line of text (newlines become spaces)."""
    return normalize_text(text.replace("\n", " "))
