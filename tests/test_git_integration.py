"""Tests for the optional GitPython integration."""

from __future__ import annotations

import os

import pytest

from gpt_repository_loader.git_integration import (
    HAS_GITPYTHON,
    describe_repo,
    tracked_files,
)


@pytest.mark.skipif(not HAS_GITPYTHON, reason="GitPython not installed")
def test_describe_repo_for_non_repo(temp_dir):
    info = describe_repo(temp_dir)
    assert info.is_repo is False


@pytest.mark.skipif(not HAS_GITPYTHON, reason="GitPython not installed")
def test_describe_repo_for_real_repo(temp_dir):
    import git

    repo = git.Repo.init(temp_dir)
    file_path = os.path.join(temp_dir, "hello.py")
    with open(file_path, "w", encoding="utf-8") as fh:
        fh.write("print('hi')\n")
    repo.index.add([file_path])
    actor = git.Actor("Test", "test@example.com")
    repo.index.commit("initial", author=actor, committer=actor)
    info = describe_repo(temp_dir)
    assert info.is_repo is True
    assert info.head_sha


@pytest.mark.skipif(not HAS_GITPYTHON, reason="GitPython not installed")
def test_tracked_files_lists_committed(temp_dir):
    import git

    repo = git.Repo.init(temp_dir)
    file_path = os.path.join(temp_dir, "a.py")
    with open(file_path, "w", encoding="utf-8") as fh:
        fh.write("a")
    repo.index.add([file_path])
    actor = git.Actor("Test", "test@example.com")
    repo.index.commit("initial", author=actor, committer=actor)
    listing = tracked_files(temp_dir)
    assert listing is not None
    assert "a.py" in listing
