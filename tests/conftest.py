"""Shared pytest fixtures for the gpt-repository-loader test suite."""

from __future__ import annotations

import os
import shutil
import tempfile

import pytest


@pytest.fixture
def temp_dir():
    """Yield a fresh temp directory that is removed after the test."""
    path = tempfile.mkdtemp(prefix="gptrepo-test-")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def tiny_repo(temp_dir):
    """Build a tiny repository tree on disk and return its path.

    Layout::

        repo/
        ├── .gptignore               (ignores *.txt and .gptignore itself)
        ├── README.md
        ├── main.py
        ├── docs/
        │   └── notes.txt            (ignored)
        ├── pkg/
        │   ├── __init__.py
        │   └── util.py
        └── binary.png               (binary - skipped by binary detector)
    """
    repo = os.path.join(temp_dir, "repo")
    os.makedirs(os.path.join(repo, "docs"), exist_ok=True)
    os.makedirs(os.path.join(repo, "pkg"), exist_ok=True)

    with open(os.path.join(repo, ".gptignore"), "w", encoding="utf-8") as fh:
        fh.write("*.txt\n.gptignore\n")
    with open(os.path.join(repo, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("# Tiny Repo\n")
    with open(os.path.join(repo, "main.py"), "w", encoding="utf-8") as fh:
        fh.write("print('hi')\n")
    with open(os.path.join(repo, "docs", "notes.txt"), "w", encoding="utf-8") as fh:
        fh.write("ignored notes\n")
    with open(os.path.join(repo, "pkg", "__init__.py"), "w", encoding="utf-8") as fh:
        fh.write("\n")
    with open(os.path.join(repo, "pkg", "util.py"), "w", encoding="utf-8") as fh:
        fh.write("def util():\n    return 42\n")
    # PNG header + a few NUL bytes -> binary detector flips on this.
    with open(os.path.join(repo, "binary.png"), "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    return repo
