"""Neutralise untrusted text (label contents, file names, typed names) before it is displayed or exported.

Text read from an uploaded label is DATA. If it were passed to a Markdown renderer as it is, a label could contain
``![x](https://attacker.example/p.png)`` and make the reviewer's browser contact that server just by opening a finding,
or contain a clickable link. If it were written to a CSV as it is, a cell such as ``=HYPERLINK(...)`` would be run as a
formula when the file is opened in a spreadsheet. These two functions are the one place where that is handled.

No business logic and no Streamlit here, so the rules can be tested on their own.
"""
from __future__ import annotations

import re
from typing import Any

# Characters that start Markdown structure (links, images, HTML, code, emphasis, tables, maths). A backslash before
# any of them makes a Markdown renderer show the character itself.
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]<>~$|&!#])")
# Bare web addresses, "www." names and e-mail addresses become links by themselves ("autolinks") and need no brackets.
# A Markdown backslash does not stop that (the renderer links the text after escapes are resolved), and a zero-width
# space placed INSIDE the address does not either (the renderer accepts it as part of the domain, and browsers drop it
# when they look the host up, so the link would still work). What does stop it is breaking the part that starts the
# pattern: the first letter of the scheme ("h<zwsp>ttps://"), of "www", and the character before "@".
# The text looks the same to a reader, but it no longer matches as a link.
_ZWSP = "\u200b"
_URL_SCHEME = re.compile(r"(?i)\b([a-z])([a-z0-9+.\-]{1,31}://)")
_WWW = re.compile(r"(?i)\b(w)(ww\.)")
_AT = re.compile(r"@")

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def escape_markdown(value: Any) -> str:
    """Return `value` as one line of text that a Markdown renderer shows literally (no links, images or HTML).

    The text looks unchanged to a reader: the added backslashes and zero-width spaces are not displayed. Line breaks
    become spaces so that text cannot start a heading, list or quote of its own.
    """
    if value is None:
        return ""
    text = " ".join(str(value).split())
    text = _MARKDOWN_SPECIAL.sub(r"\\\1", text)
    text = _URL_SCHEME.sub(lambda m: m.group(1) + _ZWSP + m.group(2), text)
    text = _WWW.sub(lambda m: m.group(1) + _ZWSP + m.group(2), text)
    return _AT.sub(_ZWSP + "@", text)


def csv_cell(value: Any) -> Any:
    """Make one CSV cell safe to open in a spreadsheet: text that would start a formula gets a leading apostrophe.

    A leading ``'`` is the usual convention: spreadsheets show the text as it is and do not run it. Numbers and other
    non-text values are returned unchanged.
    """
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value


def safe_csv(frame) -> str:
    """CSV text of a pandas DataFrame with every text cell passed through `csv_cell` (index not written)."""
    return frame.map(csv_cell).to_csv(index=False)
