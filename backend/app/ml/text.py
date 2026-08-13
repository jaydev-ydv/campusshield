"""Text normalisation, as a picklable pipeline step.

## Why this lives in the backend and not in `ml/src`

The exported model artifact contains this class. joblib pickles by reference, so
whatever normalises text at training time must be importable, by the same name,
at serving time. Putting it here makes the runtime dependency one-directional:
the Flask application never imports the research package, and the export script —
a development tool — imports this.

## Why it is a pipeline step rather than a function call

Because train/serve skew is the classic way a model silently gets worse in
production: someone changes preprocessing on one side and the vectoriser is
suddenly seeing text that does not look like what it was fitted on. Baking the
normaliser into the sklearn pipeline means there is only one place it can happen,
and `pipeline.predict_proba([raw_text])` takes the raw narrative.

`tests/test_ml_serving.py` asserts this produces byte-identical output to
`ml/src/preprocess.py` across the whole synthetic corpus, so the two cannot drift
without a test failing.

## Deliberately light

No stemming, no stopword removal, no punctuation stripping. "would not stop" and
"stop" mean different things in a harassment report, and negation is exactly what
a stopword list removes.
"""

from __future__ import annotations

import re

from sklearn.base import BaseEstimator, TransformerMixin

URL_PATTERN = re.compile(r"https?://\S+|www\.\S+")
WHITESPACE = re.compile(r"\s+")
DIGITS = re.compile(r"\d+")


def normalise(
    text: str | None,
    *,
    lowercase: bool = True,
    strip_urls: bool = True,
    strip_digits: bool = False,
    min_token_length: int = 1,
) -> str:
    """Mirror of `ml.src.preprocess.preprocess`. Kept identical by test."""
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
        out = " ".join(token for token in out.split() if len(token) >= min_token_length)
    return out


class TextNormalizer(BaseEstimator, TransformerMixin):
    """Stateless sklearn transformer wrapping :func:`normalise`.

    Fitted on nothing, so it cannot leak anything from a training split into a
    test one. The options are stored as attributes rather than closed over, so
    they travel with the pickle and a loaded artifact normalises exactly as the
    trained one did.
    """

    def __init__(
        self,
        lowercase: bool = True,
        strip_urls: bool = True,
        strip_digits: bool = False,
        min_token_length: int = 1,
    ) -> None:
        self.lowercase = lowercase
        self.strip_urls = strip_urls
        self.strip_digits = strip_digits
        self.min_token_length = min_token_length

    def fit(self, X, y=None):  # noqa: N803 - sklearn's parameter name
        return self

    def transform(self, X):  # noqa: N803 - sklearn's parameter name
        return [
            normalise(
                text,
                lowercase=self.lowercase,
                strip_urls=self.strip_urls,
                strip_digits=self.strip_digits,
                min_token_length=self.min_token_length,
            )
            for text in X
        ]
