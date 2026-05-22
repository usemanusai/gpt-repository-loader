"""Token counting utilities.

Token counting is needed in three places:

* The token-limit warning issued at the end of a bundle run (issue #26).
* The token-limit enforcement flag that aborts (or chunks) when the bundle
  would exceed the configured limit (issue #41).
* The chunking pipeline that splits oversized bundles into multiple files
  (issue #35).

The preferred backend is OpenAI's :mod:`tiktoken`, which is shipped as the
``tokens`` extra. When tiktoken is not available we fall back to a deterministic
character-based estimator (one token per four characters of input, rounded up).
The estimator over-counts on natural language and under-counts on dense source
code; for accurate counts the user is expected to install ``tiktoken``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, List, Optional

from .constants import MODEL_CONTEXT_SIZES

try:  # pragma: no cover - exercised in integration tests
    import tiktoken  # type: ignore
except Exception:  # pragma: no cover
    tiktoken = None  # type: ignore


#: Approximate characters per token for the fallback estimator.
FALLBACK_CHARS_PER_TOKEN = 4

#: tiktoken encoding name to use when a model name is not supplied.
DEFAULT_ENCODING = "cl100k_base"


@dataclass
class TokenCount:
    """Result of counting tokens in a piece of text."""

    tokens: int
    method: str
    model: Optional[str] = None


class TokenCounter:
    """Reusable token counter that caches the encoding lookup.

    Constructing a counter is relatively expensive when tiktoken is available
    because it loads vocabulary tables. The class keeps a single encoding alive
    so that callers iterating over thousands of files only pay this cost once.
    """

    def __init__(self, model: Optional[str] = None, encoding: Optional[str] = None):
        self.model = model
        self.encoding_name = encoding or DEFAULT_ENCODING
        self._encoder = None
        if tiktoken is not None:
            try:
                if model is not None:
                    self._encoder = tiktoken.encoding_for_model(model)
                else:
                    self._encoder = tiktoken.get_encoding(self.encoding_name)
            except KeyError:
                # Unknown model name - fall back to the default encoding.
                try:
                    self._encoder = tiktoken.get_encoding(self.encoding_name)
                except Exception:
                    self._encoder = None
            except Exception:
                self._encoder = None

    @property
    def backend(self) -> str:
        return "tiktoken" if self._encoder is not None else "fallback"

    def count(self, text: str) -> TokenCount:
        """Count tokens in ``text``."""
        if self._encoder is not None:
            try:
                tokens = len(self._encoder.encode(text, disallowed_special=()))
                return TokenCount(tokens=tokens, method="tiktoken", model=self.model)
            except Exception:
                # Defensive: ``encode`` should not raise for arbitrary text but
                # downstream tiktoken versions sometimes throw on special tokens.
                pass
        tokens = math.ceil(len(text) / FALLBACK_CHARS_PER_TOKEN)
        return TokenCount(tokens=tokens, method="fallback", model=self.model)

    def count_each(self, parts: Iterable[str]) -> List[TokenCount]:
        return [self.count(part) for part in parts]


def model_context_size(model: str) -> Optional[int]:
    """Return the documented context size for ``model``, if known."""
    if not model:
        return None
    if model in MODEL_CONTEXT_SIZES:
        return MODEL_CONTEXT_SIZES[model]
    # Approximate match: strip optional date suffix (e.g. ``gpt-4-2024-08-06``).
    base = model.rsplit("-", 1)[0]
    return MODEL_CONTEXT_SIZES.get(base)


def warn_if_over_limit(counter: TokenCounter, text: str, limit: int) -> Optional[str]:
    """Return a human readable warning string when ``text`` exceeds ``limit``.

    The warning text is intentionally formatted to be easy to grep for in CI
    logs and includes the backend name so users debugging surprising counts can
    tell whether they are looking at an accurate tiktoken count or the fallback
    estimator.
    """
    if limit <= 0:
        return None
    count = counter.count(text)
    if count.tokens > limit:
        return (
            f"WARNING: bundle is {count.tokens} tokens "
            f"({count.method} backend), exceeding limit of {limit}."
        )
    return None
