"""Small reusable display pieces."""
from __future__ import annotations

from collections.abc import Iterable

import streamlit as st

from labelguard.ingestion import Note


def format_size(size_bytes: int) -> str:
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def render_notes(notes: Iterable[Note], title: str) -> None:
    """Show processing notes: warnings prominently, informational notes quietly."""
    for note in notes:
        if note.level == "warning":
            st.warning(f"**{title}:** {note.message}")
        else:
            st.caption(f"{title}: {note.message}")
