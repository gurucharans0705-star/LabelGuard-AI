"""Shared visual styling: a restrained blue-and-white look."""
from __future__ import annotations

import html

import streamlit as st

_CSS = """
<style>
.block-container { padding-top: 1.6rem; max-width: 1200px; }
.lg-hero { background: #1F4E79; color: #fff; border-radius: 10px; padding: 1.1rem 1.5rem; margin-bottom: 1.2rem; }
.lg-hero h1 { color: #fff; margin: 0; padding: 0; font-size: 1.6rem; }
.lg-hero p { color: #D6E4F2; margin: 0.25rem 0 0 0; }
[data-testid="stMetric"] { background: #F3F7FC; border: 1px solid #D6E2F0; border-radius: 8px; padding: 0.6rem 0.9rem; }
.lg-badge { display: inline-block; padding: 1px 10px; border-radius: 12px; font-size: 0.8rem; font-weight: 600; }
.lg-ok { background: #E3F1E7; color: #1B5E3A; }
.lg-warn { background: #FFF1D6; color: #7A4B00; }
.lg-bad { background: #FDE3E3; color: #8A1F1F; }
.lg-info { background: #E1EBF7; color: #1F4E79; }
.lg-diff { border: 1px solid #D6E2F0; border-left: 4px solid #1F4E79; border-radius: 6px; padding: 0.5rem 0.8rem; margin-bottom: 0.5rem; font-size: 0.92rem; }
.lg-diff .lg-row { margin: 0.1rem 0; }
.lg-del { background: #FBD5D5; text-decoration: line-through; padding: 0 2px; border-radius: 3px; }
.lg-ins { background: #D2EBD8; padding: 0 2px; border-radius: 3px; }
.lg-muted { color: #5B6B7D; }
</style>
"""


def apply_style() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def page_header(title: str, subtitle: str = "") -> None:
    st.markdown(
        f'<div class="lg-hero"><h1>{html.escape(title)}</h1><p>{html.escape(subtitle)}</p></div>',
        unsafe_allow_html=True,
    )


def badge(text: str, kind: str = "info") -> str:
    """Return HTML for a small status badge. kind: ok, warn, bad or info."""
    return f'<span class="lg-badge lg-{kind}">{html.escape(text)}</span>'
