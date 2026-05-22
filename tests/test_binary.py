"""Tests for binary file detection."""

from __future__ import annotations

import os

from gpt_repository_loader.binary import detect_binary, is_binary


def test_detects_text_file(temp_dir):
    path = os.path.join(temp_dir, "hello.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("print('hello')\n")
    result = detect_binary(path)
    assert result.is_binary is False
    assert "text" in result.reason.lower()


def test_extension_based_binary_short_circuits(temp_dir):
    path = os.path.join(temp_dir, "weird.exe")
    # Write plain text but with a binary extension.
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("hello")
    assert is_binary(path)


def test_magic_byte_detection(temp_dir):
    path = os.path.join(temp_dir, "image")
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    result = detect_binary(path)
    assert result.is_binary
    assert result.mime == "image/png"


def test_nul_byte_heuristic(temp_dir):
    path = os.path.join(temp_dir, "data")
    with open(path, "wb") as fh:
        fh.write(b"hello\x00world")
    result = detect_binary(path)
    assert result.is_binary


def test_utf8_text_with_high_bytes(temp_dir):
    path = os.path.join(temp_dir, "utf8.txt")
    with open(path, "wb") as fh:
        fh.write("naïve café 文字".encode())
    assert not is_binary(path)


def test_disable_extension_layer(temp_dir):
    path = os.path.join(temp_dir, "fake.exe")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("plain text")
    assert detect_binary(path, use_extension=False).is_binary is False


def test_unreadable_file_marked_binary(temp_dir):
    missing = os.path.join(temp_dir, "missing.bin")
    result = detect_binary(missing, use_extension=False)
    assert result.is_binary
    assert "unreadable" in result.reason.lower()
