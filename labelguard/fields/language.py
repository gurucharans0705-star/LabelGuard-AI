"""Very small stop-word based language detector.

It is intentionally simple (no extra dependency) and only reports a language when
there is enough evidence. Short labels often give too little text to decide, in
which case the honest answer is "not detected".
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_STOPWORDS: dict[str, frozenset[str]] = {
    "English": frozenset("the and of to is are not use with for if by only this that from do or be".split()),
    "German": frozenset("der die das und oder nicht mit für von ist bei nur gebrauch verwenden nach".split()),
    "French": frozenset("le les et ou ne pas avec pour des est utiliser usage unique sur dans".split()),
    "Spanish": frozenset("el los las con para del uso usar único estéril fecha una".split()),
    "Italian": frozenset("il gli per non del uso monouso usare scadenza sterile della".split()),
    "Portuguese": frozenset("os as não com para uso usar estéril validade uma".split()),
}

MIN_TOTAL_HITS = 4  # fewer stop-word hits than this is not enough to decide
MIN_LANGUAGE_HITS = 3
MIN_SHARE = 0.2


@dataclass(frozen=True)
class LanguageGuess:
    name: str
    hits: int  # number of stop-word matches supporting this language
    share: float  # fraction of all stop-word matches (0-1)


def detect_languages(text: str) -> tuple[LanguageGuess, ...]:
    """Return likely languages (most likely first), or an empty tuple if undecidable."""
    words = re.findall(r"[^\W\d_]+", text.lower())
    hits = {name: sum(1 for word in words if word in vocabulary) for name, vocabulary in _STOPWORDS.items()}
    total = sum(hits.values())
    if total < MIN_TOTAL_HITS:
        return ()
    guesses = [
        LanguageGuess(name, count, round(count / total, 2))
        for name, count in hits.items()
        if count >= MIN_LANGUAGE_HITS and count / total >= MIN_SHARE
    ]
    return tuple(sorted(guesses, key=lambda guess: guess.hits, reverse=True))
