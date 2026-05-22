"""Tests for the URL/remote loader."""

from __future__ import annotations

import os
import tarfile
import zipfile
from pathlib import Path

import pytest

from gpt_repository_loader.url_loader import is_url, open_remote


def test_is_url_detects_https():
    assert is_url("https://github.com/owner/repo")
    assert is_url("git@github.com:owner/repo.git")
    assert is_url("git://example.com/repo.git")
    assert not is_url("/absolute/path/repo")
    assert not is_url("relative/path")


def test_open_remote_with_local_file_url(temp_dir):
    # Create a directory and load it via file:// scheme. Path.as_uri() works
    # cross-platform: 'file:///abs/path' on POSIX and 'file:///C:/...' on
    # Windows.
    target = os.path.join(temp_dir, "src")
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, "x.py"), "w", encoding="utf-8") as fh:
        fh.write("print('x')\n")
    url = Path(target).as_uri()
    with open_remote(url) as remote:
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
    url = Path(archive).as_uri()
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
        with open_remote(Path(archive).as_uri()) as _:
            pass


def test_file_url_to_path_windows_malformed(monkeypatch):
    """The Windows ``file://C:\\path`` form (lacking the third slash) must be
    tolerated because pathlib only learned to emit the proper form in 3.13."""
    from gpt_repository_loader.url_loader import _file_url_to_path

    monkeypatch.setattr(os, "name", "nt")
    url = "file://C:\\Users\\runner\\AppData\\Local\\Temp\\gptrepo\\src"
    out = _file_url_to_path(url)
    assert out.startswith("C:") and "gptrepo" in out


def test_file_url_to_path_windows_proper(monkeypatch):
    from gpt_repository_loader.url_loader import _file_url_to_path

    monkeypatch.setattr(os, "name", "nt")
    out = _file_url_to_path("file:///C:/proper/path")
    assert out.startswith("C:") and "proper" in out


def test_file_url_to_path_posix(monkeypatch):
    from gpt_repository_loader.url_loader import _file_url_to_path

    monkeypatch.setattr(os, "name", "posix")
    assert _file_url_to_path("file:///tmp/repo") == "/tmp/repo"
