"""Deterministic text preprocessing.

Deliberately light. Aggressive normalisation — stemming, stopword removal,
punctuation stripping — discards signal that matters here: "would not stop" and
"stop" mean different things in a harassment report, and negation is exactly what
stopword lists remove.

Nothing here is fitted on data, so it cannot leak. The TF-IDF vocabulary is
fitted, and that happens on the training split only — see `baseline.py`.
"""

from __future__ import annotations

import re

URL_PATTERN = re.compile(r"https?://\S+|www\.\S+")
WHITESPACE = re.compile(r"\s+")
DIGITS = re.compile(r"\d+")


def preprocess(text: str, *, lowercase: bool = True, strip_urls: bool = True,
               strip_digits: bool = False, min_token_length: int = 1) -> str:
    if text is None:
        return ""
    out = text
    if strip_urls:
        out = URL_PATTERN.sub(" ", out)
    if strip_digits:
        out = DIGITS.sub(" ", out)
    if lowercase:
        out = out.lower()
    out = WHITESPACE.sub(" ", out).strip()
    if min_token_length > 1:
        out = " ".join(t for t in out.split() if len(t) >= min_token_length)
    return out


def preprocess_all(texts: list[str], **kwargs) -> list[str]:
    return [preprocess(t, **kwargs) for t in texts]
