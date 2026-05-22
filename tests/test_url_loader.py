"""Tests for the URL/remote loader."""

from __future__ import annotations

import os
import tarfile
import zipfile

import pytest

from gpt_repository_loader.url_loader import is_url, open_remote


def test_is_url_detects_https():
    assert is_url("https://github.com/owner/repo")
    assert is_url("git@github.com:owner/repo.git")
    assert is_url("git://example.com/repo.git")
    assert not is_url("/absolute/path/repo")
    assert not is_url("relative/path")


def test_open_remote_with_local_file_url(temp_dir):
    # Create a directory and load it via file:// scheme.
    target = os.path.join(temp_dir, "src")
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, "x.py"), "w", encoding="utf-8") as fh:
        fh.write("print('x')\n")
    with open_remote(f"file://{target}") as remote:
        assert remote.kind == "local"
        assert os.path.isfile(os.path.join(remote.path, "x.py"))


def test_open_remote_with_zip_archive(temp_dir):
    inner = os.path.join(temp_dir, "src")
    os.makedirs(inner)
    with open(os.path.join(inner, "main.py"), "w", encoding="utf-8") as fh:
        fh.write("print('hi')\n")
    archive = os.path.join(temp_dir, "repo.zip")
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(os.path.join(inner, "main.py"), arcname="src/main.py")
    url = "file://" + archive
    with open_remote(url) as remote:
        assert remote.kind == "archive"
        assert any(name == "main.py" for name in os.listdir(remote.path))


def test_open_remote_rejects_unknown_url(temp_dir):
    with pytest.raises(ValueError):
        with open_remote("totally-random-token") as _:
            pass


def test_archive_traversal_blocked(temp_dir):
    # Build a tar with a path that escapes the temp dir.
    archive = os.path.join(temp_dir, "evil.tar")
    inner = os.path.join(temp_dir, "x.txt")
    with open(inner, "w", encoding="utf-8") as fh:
        fh.write("hello")
    with tarfile.open(archive, "w") as tf:
        info = tarfile.TarInfo(name="../escape.txt")
        info.size = len(b"hello")
        with open(inner, "rb") as src:
            tf.addfile(info, src)
    with pytest.raises(RuntimeError):
        with open_remote("file://" + archive) as _:
            pass
