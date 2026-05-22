"""Reverse loader: turn a bundle back into a directory tree (issue #30).

The reverse loader parses a text bundle produced by :mod:`gpt_repository_loader.core`
(or a hand-crafted bundle that follows the same format) and writes every file
section back to disk. Every path is validated against directory-traversal
attacks: a file that resolves outside the destination root is refused, even if
it was emitted by a benign-looking bundle.

Encodings are detected via the ``# encoding:`` metadata line and decoded with
:func:`gpt_repository_loader.compression.decode_content`. Files emitted with
the legacy ``raw`` encoding (no metadata line) are written verbatim.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from .compression import decode_content
from .constants import ENCODING_PREFIX, ENCODING_RAW, END_MARKER, FILE_DELIMITER

_PART_SUFFIX = re.compile(r"\s+\(part\s+(\d+)\s+of\s+(\d+)\)\s*$")


@dataclass
class ReverseFile:
    """A single file extracted from a bundle."""

    rel_path: str
    content: str
    encoding: str = ENCODING_RAW
    part_index: Optional[int] = None
    part_count: Optional[int] = None


@dataclass
class ReverseResult:
    """Result of running :func:`parse_bundle`."""

    preamble: str
    files: List[ReverseFile]


def _strip_part_suffix(path: str) -> Tuple[str, Optional[int], Optional[int]]:
    m = _PART_SUFFIX.search(path)
    if not m:
        return path, None, None
    cleaned = path[: m.start()].rstrip()
    return cleaned, int(m.group(1)), int(m.group(2))


def parse_bundle(text: str) -> ReverseResult:
    """Parse ``text`` as a bundle and return the contained files.

    The bundle layout produced by :mod:`gpt_repository_loader.core` is::

        <preamble>\\n----\\n<path1>\\n<content1>\\n----\\n<path2>\\n<content2>\\n--END--

    Each section is rendered as ``"{delim}\\n{path}\\n{content}\\n"`` so the
    final ``\\n`` of every section is the writer's trailer rather than part of
    the file content. When two sections are concatenated, the trailer ``\\n``
    of section i is consumed by the ``\\n----\\n`` separator that introduces
    section i+1; the content's *own* trailing newline (if any) is what
    survives on the section's last line. For the final section the writer's
    trailer ``\\n`` survives and is followed by the ``--END--`` marker. The
    parser therefore preserves all whitespace inside ``<content>`` verbatim and
    only strips the ``\\n--END--`` epilogue from the last section.
    """
    normalized = text.replace("\r\n", "\n")
    preamble_chunk, separator, body = normalized.partition(f"\n{FILE_DELIMITER}\n")
    if not separator:
        # No file sections at all - the whole text is preamble.
        return ReverseResult(preamble=normalized, files=[])
    preamble = preamble_chunk
    sections_raw = body.split(f"\n{FILE_DELIMITER}\n")
    files: List[ReverseFile] = []
    pending_parts: dict = {}
    last_index = len(sections_raw) - 1
    for idx, raw_section in enumerate(sections_raw):
        if not raw_section.strip():
            continue
        first_newline = raw_section.find("\n")
        if first_newline < 0:
            path = raw_section.strip()
            rest = ""
        else:
            path = raw_section[:first_newline]
            rest = raw_section[first_newline + 1 :]
        is_last = idx == last_index
        if is_last:
            # Locate the END marker. We accept either ``\n--END--`` or a
            # bare trailing ``--END--`` if the user hand-edited the bundle.
            end_with_nl = f"\n{END_MARKER}"
            if rest.endswith(end_with_nl):
                rest = rest[: -len(end_with_nl)]
            elif rest.endswith(END_MARKER):
                rest = rest[: -len(END_MARKER)]
        encoding = ENCODING_RAW
        if rest.startswith(ENCODING_PREFIX):
            nl = rest.find("\n")
            if nl < 0:
                encoding = rest[len(ENCODING_PREFIX) :].strip()
                rest = ""
            else:
                encoding = rest[len(ENCODING_PREFIX) : nl].strip()
                rest = rest[nl + 1 :]
        try:
            decoded = decode_content(rest, encoding)
        except ValueError:
            # Unknown encoding - fall back to raw so the bundle is still
            # reconstructable, but flag the situation via an empty encoding.
            decoded = rest
            encoding = ENCODING_RAW
        cleaned_path, part_index, part_count = _strip_part_suffix(path)
        rf = ReverseFile(
            rel_path=cleaned_path,
            content=decoded,
            encoding=encoding,
            part_index=part_index,
            part_count=part_count,
        )
        if part_index is None or part_count is None:
            files.append(rf)
            continue
        key = (cleaned_path, part_count)
        existing = pending_parts.get(key)
        if existing is None:
            existing = ReverseFile(
                rel_path=cleaned_path,
                content="",
                encoding=encoding,
                part_index=None,
                part_count=part_count,
            )
            pending_parts[key] = existing
        existing.content += decoded
    files.extend(pending_parts.values())
    return ReverseResult(preamble=preamble, files=files)


def _safe_join(root: str, rel_path: str) -> str:
    """Join ``root`` with ``rel_path`` while rejecting traversal attacks."""
    normalized = os.path.normpath(rel_path)
    if normalized.startswith("..") or os.path.isabs(normalized):
        raise ValueError(f"Refusing unsafe path in bundle: {rel_path!r}")
    target = os.path.normpath(os.path.join(root, normalized))
    if os.path.commonpath([os.path.abspath(root), os.path.abspath(target)]) != os.path.abspath(root):
        raise ValueError(f"Refusing path that escapes destination: {rel_path!r}")
    return target


def unbundle(
    text: str,
    destination: str,
    *,
    overwrite: bool = False,
    include: Optional[Iterable[str]] = None,
) -> List[str]:
    """Extract a bundle into ``destination``.

    Parameters
    ----------
    text:
        The bundle text to parse.
    destination:
        Directory to write into. Created (recursively) if missing.
    overwrite:
        When ``False`` (default) existing files are preserved and a
        :class:`FileExistsError` is raised on collision. When ``True`` existing
        files are silently replaced.
    include:
        Optional iterable of relative paths to extract. When supplied, all
        other files in the bundle are skipped.

    Returns the list of files that were written.
    """
    result = parse_bundle(text)
    os.makedirs(destination, exist_ok=True)
    written: List[str] = []
    include_set = {p.replace("\\", "/") for p in include} if include else None
    for rf in result.files:
        normalized_path = rf.rel_path.replace("\\", "/")
        if include_set is not None and normalized_path not in include_set:
            continue
        target = _safe_join(destination, rf.rel_path)
        if os.path.exists(target) and not overwrite:
            raise FileExistsError(
                f"Refusing to overwrite existing file: {target}. Re-run with "
                "overwrite=True or remove the file."
            )
        os.makedirs(os.path.dirname(target) or destination, exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(rf.content)
        written.append(target)
    return written
