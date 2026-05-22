"""Tests for clipboard backend discovery and copy_to_clipboard."""

from __future__ import annotations

import pytest

from gpt_repository_loader import clipboard


def test_clipboard_unavailable_raises(monkeypatch):
    monkeypatch.setattr(clipboard, "_try_pyperclip", lambda: None)
    monkeypatch.setattr(clipboard, "_try_subprocess", lambda cmd: None)
    with pytest.raises(clipboard.ClipboardUnavailableError):
        clipboard.copy_to_clipboard("test")


def test_clipboard_available_uses_pyperclip(monkeypatch):
    captured = {}

    def fake_pyperclip():
        def _copy(text):
            captured["text"] = text
        return _copy

    monkeypatch.setattr(clipboard, "_try_pyperclip", fake_pyperclip)
    backend = clipboard.copy_to_clipboard("hello")
    assert backend == "pyperclip"
    assert captured["text"] == "hello"


def test_clipboard_available_function_returns_bool(monkeypatch):
    monkeypatch.setattr(clipboard, "_try_pyperclip", lambda: None)
    monkeypatch.setattr(clipboard, "_try_subprocess", lambda cmd: None)
    assert clipboard.clipboard_available() is False


def test_clipboard_subprocess_backend(monkeypatch):
    monkeypatch.setattr(clipboard, "_try_pyperclip", lambda: None)
    monkeypatch.setattr(clipboard.sys, "platform", "linux")

    captured = {}

    def fake_try_subprocess(cmd):
        if cmd[0] == "xclip":
            def _copy(text):
                captured["xclip"] = text
            return _copy
        return None

    monkeypatch.setattr(clipboard, "_try_subprocess", fake_try_subprocess)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    backend = clipboard.copy_to_clipboard("payload")
    assert backend == "xclip"
    assert captured["xclip"] == "payload"
