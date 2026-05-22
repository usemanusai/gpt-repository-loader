"""Preamble management.

A *preamble* is the free-form text emitted at the top of every bundle before
the first file section. The default preamble describes the file-section layout
and the ``--END--`` terminator so that LLMs reading the bundle understand the
format without prior context.

Three subsystems are implemented here:

* :func:`load_preamble` reads a custom preamble from disk and returns its text.
* :func:`generate_default_preamble` returns the canonical default preamble used
  when no custom file is supplied, with optional ``--with-stats`` extensions.
* :func:`init_preamble_file` writes a starter preamble file to disk - this is
  the feature requested in issue #49 ("add logic to create preamble file").
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from .constants import DEFAULT_PREAMBLE


@dataclass
class PreambleConfig:
    """Configuration for preamble generation."""

    path: Optional[str] = None
    text: Optional[str] = None
    include_stats: bool = False
    extra_instructions: Optional[str] = None


def load_preamble(path: str) -> str:
    """Read ``path`` and return its content as the preamble text."""
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def generate_default_preamble(
    *,
    include_stats: bool = False,
    file_count: Optional[int] = None,
    token_estimate: Optional[int] = None,
    extra_instructions: Optional[str] = None,
) -> str:
    """Return the canonical default preamble, optionally with stats appended."""
    parts = [DEFAULT_PREAMBLE]
    if include_stats:
        stats = []
        if file_count is not None:
            stats.append(f"Files: {file_count}")
        if token_estimate is not None:
            stats.append(f"Approximate token count: {token_estimate}")
        if stats:
            parts.append("Bundle stats: " + ", ".join(stats) + ".")
    if extra_instructions:
        parts.append(extra_instructions.strip())
    return "\n\n".join(parts)


def resolve_preamble(config: PreambleConfig) -> str:
    """Resolve a :class:`PreambleConfig` to its final preamble text."""
    if config.text is not None:
        return config.text
    if config.path is not None:
        return load_preamble(config.path)
    return generate_default_preamble(
        include_stats=config.include_stats,
        extra_instructions=config.extra_instructions,
    )


def init_preamble_file(path: str, *, overwrite: bool = False) -> str:
    """Create a starter preamble file at ``path`` (issue #49).

    Returns the resolved absolute path of the file that was written. Raises
    :class:`FileExistsError` if the file already exists and ``overwrite`` is
    ``False``.
    """
    abs_path = os.path.abspath(path)
    if os.path.exists(abs_path) and not overwrite:
        raise FileExistsError(
            f"Refusing to overwrite existing preamble at {abs_path}. "
            "Re-run with --force to replace it."
        )
    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
    content = (
        "# Custom gpt-repository-loader preamble\n"
        "#\n"
        "# This text is prepended verbatim to every bundle produced from this\n"
        "# repository. Tailor the instructions below so that the language model\n"
        "# understands the project conventions, expected response format and any\n"
        "# domain-specific terminology before reading the source.\n"
        "\n"
        f"{DEFAULT_PREAMBLE}\n"
        "\n"
        "Additional project-specific guidance goes here.\n"
    )
    with open(abs_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return abs_path
