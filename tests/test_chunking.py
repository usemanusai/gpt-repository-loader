"""Tests for the chunking pipeline."""

from __future__ import annotations

from gpt_repository_loader.chunking import (
    FileSection,
    chunk_sections,
    split_oversized_section,
)
from gpt_repository_loader.constants import FILE_DELIMITER
from gpt_repository_loader.tokens import TokenCounter


def test_small_input_single_chunk():
    counter = TokenCounter()
    sections = [FileSection("a.py", "print('a')\n")]
    chunks = chunk_sections(sections, counter=counter, limit=100, delimiter=FILE_DELIMITER)
    assert len(chunks) == 1
    assert chunks[0].sections == sections


def test_chunks_split_on_file_boundaries():
    counter = TokenCounter()
    sections = [FileSection(f"f{idx}.py", "print('x')\n" * 5) for idx in range(8)]
    chunks = chunk_sections(sections, counter=counter, limit=30, delimiter=FILE_DELIMITER)
    assert len(chunks) > 1
    all_sections = [s for c in chunks for s in c.sections]
    assert all_sections == sections


def test_oversized_single_file_is_split_into_parts():
    counter = TokenCounter()
    body = (f"line {i}\n" for i in range(500))
    big = FileSection("big.py", "".join(body))
    parts = split_oversized_section(big, counter=counter, limit=80, delimiter=FILE_DELIMITER)
    assert len(parts) > 1
    assert all("(part" in part.rel_path for part in parts)
    # Reassembled content must equal original (with newline joins).
    rebuilt = "".join(p.body if i == len(parts) - 1 else p.body + "\n" for i, p in enumerate(parts))
    # Trim and compare token contents to confirm no data lost.
    assert rebuilt.replace("\n", "") == big.body.replace("\n", "")


def test_zero_limit_disables_splitting():
    counter = TokenCounter()
    sections = [FileSection("a", "x" * 10000)]
    chunks = chunk_sections(sections, counter=counter, limit=0, delimiter=FILE_DELIMITER)
    assert len(chunks) == 1
