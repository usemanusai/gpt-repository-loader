"""Tests for the preamble module."""

from __future__ import annotations

import os

import pytest

from gpt_repository_loader.preamble import (
    PreambleConfig,
    generate_default_preamble,
    init_preamble_file,
    load_preamble,
    resolve_preamble,
)


def test_default_preamble_contains_end_marker_explanation():
    text = generate_default_preamble()
    assert "--END--" in text
    assert "----" in text


def test_default_preamble_with_stats():
    text = generate_default_preamble(include_stats=True, file_count=4, token_estimate=128)
    assert "Files: 4" in text
    assert "Approximate token count: 128" in text


def test_resolve_preamble_path_takes_precedence(temp_dir):
    path = os.path.join(temp_dir, "preamble.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("CUSTOM PREAMBLE")
    assert resolve_preamble(PreambleConfig(path=path)) == "CUSTOM PREAMBLE"


def test_resolve_preamble_text_takes_precedence_over_path(temp_dir):
    path = os.path.join(temp_dir, "preamble.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("from file")
    out = resolve_preamble(PreambleConfig(text="from text", path=path))
    assert out == "from text"


def test_init_preamble_creates_file(temp_dir):
    path = os.path.join(temp_dir, ".gpt-preamble")
    written = init_preamble_file(path)
    assert os.path.exists(written)
    with open(written, encoding="utf-8") as fh:
        content = fh.read()
    assert "--END--" in content


def test_init_preamble_refuses_overwrite(temp_dir):
    path = os.path.join(temp_dir, ".gpt-preamble")
    init_preamble_file(path)
    with pytest.raises(FileExistsError):
        init_preamble_file(path)


def test_init_preamble_force_flag(temp_dir):
    path = os.path.join(temp_dir, ".gpt-preamble")
    init_preamble_file(path)
    init_preamble_file(path, overwrite=True)


def test_load_preamble_reads_file(temp_dir):
    path = os.path.join(temp_dir, "p.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("Hello preamble.")
    assert load_preamble(path) == "Hello preamble."
