"""Chunking strategy for bundles that exceed a configured token budget.

Issue #35 reports that ``output.txt`` is frequently too long to paste into a
single LLM prompt. The chunker addresses this by partitioning the file sections
produced by :mod:`gpt_repository_loader.core` into groups whose token counts
each fit beneath the configured limit. Boundaries are placed between file
sections whenever possible. If an individual file exceeds the limit on its own
the file is broken into multiple parts each labelled with ``(part N of M)`` in
the file path so a reader can still reconstruct it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .constants import ENCODING_PREFIX, ENCODING_RAW
from .tokens import TokenCounter


@dataclass
class FileSection:
    """A single file section ready to be emitted into a bundle."""

    rel_path: str
    body: str
    encoding: str = ENCODING_RAW

    def render(self, delimiter: str) -> str:
        if self.encoding == ENCODING_RAW:
            return f"{delimiter}\n{self.rel_path}\n{self.body}\n"
        return (
            f"{delimiter}\n{self.rel_path}\n"
            f"{ENCODING_PREFIX} {self.encoding}\n{self.body}\n"
        )


@dataclass
class Chunk:
    """A group of file sections that collectively fit a token budget."""

    sections: List[FileSection] = field(default_factory=list)
    tokens: int = 0

    def render(self, delimiter: str) -> str:
        return "".join(section.render(delimiter) for section in self.sections)


def split_oversized_section(
    section: FileSection, *, counter: TokenCounter, limit: int, delimiter: str
) -> List[FileSection]:
    """Break a single file that exceeds ``limit`` into multiple smaller parts.

    Splitting is performed on line boundaries to preserve readability. The
    function deterministically produces 1-of-N labelled parts.
    """
    rendered = section.render(delimiter)
    if counter.count(rendered).tokens <= limit:
        return [section]
    lines = section.body.splitlines(keepends=True)
    if not lines:
        return [section]
    parts: List[List[str]] = [[]]
    parts_tokens: List[int] = [0]
    overhead = counter.count(f"{delimiter}\n{section.rel_path} (part 999 of 999)\n\n").tokens
    available = max(1, limit - overhead)
    current_tokens = 0
    for line in lines:
        line_tokens = counter.count(line).tokens
        if current_tokens + line_tokens > available and parts[-1]:
            parts.append([])
            parts_tokens.append(0)
            current_tokens = 0
        parts[-1].append(line)
        current_tokens += line_tokens
        parts_tokens[-1] = current_tokens
    total = len(parts)
    return [
        FileSection(
            rel_path=f"{section.rel_path} (part {idx + 1} of {total})",
            body="".join(chunk_lines).rstrip("\n"),
            encoding=section.encoding,
        )
        for idx, chunk_lines in enumerate(parts)
    ]


def chunk_sections(
    sections: List[FileSection],
    *,
    counter: TokenCounter,
    limit: int,
    delimiter: str,
) -> List[Chunk]:
    """Partition ``sections`` into chunks each below ``limit`` tokens."""
    if limit <= 0:
        return [Chunk(sections=list(sections),
                     tokens=sum(counter.count(s.render(delimiter)).tokens for s in sections))]
    chunks: List[Chunk] = [Chunk()]
    for section in sections:
        rendered = section.render(delimiter)
        section_tokens = counter.count(rendered).tokens
        if section_tokens > limit:
            for piece in split_oversized_section(
                section, counter=counter, limit=limit, delimiter=delimiter
            ):
                _append(chunks, piece, counter=counter, limit=limit, delimiter=delimiter)
        else:
            _append(chunks, section, counter=counter, limit=limit, delimiter=delimiter)
    return chunks


def _append(
    chunks: List[Chunk],
    section: FileSection,
    *,
    counter: TokenCounter,
    limit: int,
    delimiter: str,
) -> None:
    section_tokens = counter.count(section.render(delimiter)).tokens
    current = chunks[-1]
    if current.tokens + section_tokens > limit and current.sections:
        chunks.append(Chunk())
        current = chunks[-1]
    current.sections.append(section)
    current.tokens += section_tokens
