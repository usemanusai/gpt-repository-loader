"""Tests for token counting (:mod:`gpt_repository_loader.tokens`)."""

from __future__ import annotations

from gpt_repository_loader.tokens import (
    TokenCounter,
    model_context_size,
    warn_if_over_limit,
)


def test_fallback_estimator_when_tiktoken_missing(monkeypatch):
    import gpt_repository_loader.tokens as tokens_mod

    monkeypatch.setattr(tokens_mod, "tiktoken", None)
    counter = TokenCounter(model="gpt-4")
    result = counter.count("a" * 8)
    assert result.method == "fallback"
    assert result.tokens == 2  # ceil(8/4) = 2


def test_tiktoken_path_when_available():
    counter = TokenCounter(model="gpt-4")
    # The accurate counter must return at least 1 token for non-empty input.
    out = counter.count("Hello world")
    assert out.tokens >= 1


def test_count_each():
    counter = TokenCounter()
    counts = counter.count_each(["abcd", "efghij"])
    assert len(counts) == 2
    assert all(c.tokens >= 1 for c in counts)


def test_model_context_size_known_model():
    assert model_context_size("gpt-4") == 8192
    assert model_context_size("gpt-4-32k") == 32768
    assert model_context_size("gpt-4o") == 128000


def test_model_context_size_unknown_model():
    assert model_context_size("totally-not-a-model") is None


def test_warn_if_over_limit_returns_message():
    counter = TokenCounter()
    msg = warn_if_over_limit(counter, "x" * 1000, limit=10)
    assert msg is not None
    assert "exceed" in msg.lower()


def test_warn_if_over_limit_returns_none_when_under():
    counter = TokenCounter()
    msg = warn_if_over_limit(counter, "x", limit=10_000)
    assert msg is None
