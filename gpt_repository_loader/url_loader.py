"""URL / remote-repository loading (issue #36).

A handful of URL shapes are supported:

* ``http(s)://github.com/<owner>/<repo>(.git)?(/tree/<branch>)?`` and similar
  patterns from GitLab/Bitbucket - cloned via :mod:`git_integration`.
* ``git@github.com:<owner>/<repo>.git`` - cloned via :mod:`git_integration`.
* ``https://example.com/file.zip`` or ``file:///path/to/archive.zip`` - the
  archive is downloaded, validated and extracted into a temporary directory.

In every case the loader returns a :class:`RemoteRepository` whose ``path``
attribute points at a temp directory that should be processed exactly like a
local clone. The directory is removed automatically via the context manager
interface.
"""

from __future__ import annotations

import os
import re
import shutil
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, Optional

from .git_integration import HAS_GITPYTHON, clone_repo

_GIT_HOST_PATTERN = re.compile(
    r"^(?:https?://|git@)?(?:www\.)?"
    r"(?P<host>github\.com|gitlab\.com|bitbucket\.org|codeberg\.org)"
    r"[:/](?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?"
    r"(?:/tree/(?P<branch>[A-Za-z0-9_./-]+))?/?$"
)

_RAW_GIT_PATTERN = re.compile(
    r"^(?:https?|git|ssh)://[^/]+/.+\.git$"
)

_ARCHIVE_EXTENSIONS = (
    ".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz",
)


@dataclass
class RemoteRepository:
    """A repository fetched from a URL that lives in a temporary directory."""

    url: str
    path: str
    kind: str
    cleanup_dirs: list = field(default_factory=list)

    def cleanup(self) -> None:
        for directory in self.cleanup_dirs:
            shutil.rmtree(directory, ignore_errors=True)


def _file_url_to_path(url: str) -> str:
    """Convert a ``file://`` URL into an OS-native filesystem path.

    Handles three common shapes:

    * POSIX: ``file:///abs/path``        -> ``/abs/path``
    * Windows proper: ``file:///C:/x``   -> ``C:\\x``
    * Windows malformed: ``file://C:\\x`` -> ``C:\\x`` (we tolerate this because
      ``pathlib.Path.as_uri`` was only standardised for Windows in 3.13 and
      many call sites still emit the malformed form).

    Non-file URLs are returned unchanged so callers can chain this through
    classification helpers without first checking the scheme.
    """
    if not url.lower().startswith("file:"):
        return url
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "file":
        return url
    netloc = parsed.netloc
    path = urllib.parse.unquote(parsed.path)
    if os.name == "nt":
        # Tolerate the malformed ``file://C:\foo`` form by merging netloc
        # back into the path.
        if netloc and len(netloc) >= 2 and netloc[1] == ":":
            return netloc + path
        # Proper ``file:///C:/foo`` form: drop the leading slash before the
        # drive letter so we end up with ``C:/foo``.
        if len(path) >= 3 and path[0] == "/" and path[2] == ":":
            return path[1:].replace("/", os.sep)
        return path.replace("/", os.sep)
    # POSIX: leading slash is already part of the absolute path.
    return path


def _is_archive_path(path: str) -> bool:
    lower = path.lower()
    return any(lower.endswith(ext) for ext in _ARCHIVE_EXTENSIONS)


def _classify_url(url: str) -> str:
    if url.lower().startswith("file:"):
        local_path = _file_url_to_path(url)
        if _is_archive_path(local_path):
            return "archive"
        if os.path.isdir(local_path):
            return "local"
        if os.path.isfile(local_path):
            return "archive"  # non-archive single file - extractor will reject
        raise ValueError(f"file:// URL does not resolve to a path: {url!r}")
    parsed = urllib.parse.urlparse(url)
    if _is_archive_path(parsed.path):
        return "archive"
    if _RAW_GIT_PATTERN.match(url) or url.startswith("git@") or url.endswith(".git"):
        return "git"
    if _GIT_HOST_PATTERN.match(url):
        return "git-host"
    if parsed.scheme == "" and os.path.isdir(parsed.path):
        return "local"
    raise ValueError(f"Cannot determine how to load URL: {url!r}")


def _normalize_git_url(url: str) -> tuple:
    """Return ``(clone_url, branch)`` for a host-style URL."""
    m = _GIT_HOST_PATTERN.match(url)
    if not m:
        return url, None
    host = m.group("host")
    owner = m.group("owner")
    repo = m.group("repo")
    branch = m.group("branch")
    clone_url = f"https://{host}/{owner}/{repo}.git"
    return clone_url, branch


def _download_to_tempfile(url: str) -> str:
    """Download ``url`` to a temporary file and return its path."""
    suffix = ""
    parsed = urllib.parse.urlparse(url)
    candidate = parsed.path if not url.lower().startswith("file:") else _file_url_to_path(url)
    for ext in _ARCHIVE_EXTENSIONS:
        if candidate.lower().endswith(ext):
            suffix = ext
            break
    fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="gptrepo-download-")
    os.close(fd)
    if url.lower().startswith("file:"):
        # Bypass urllib's quirky Windows file:// handling and copy directly.
        src = _file_url_to_path(url)
        with open(src, "rb") as response, open(tmp_path, "wb") as out:
            shutil.copyfileobj(response, out)
    else:
        with urllib.request.urlopen(url) as response, open(tmp_path, "wb") as out:
            shutil.copyfileobj(response, out)
    return tmp_path


def _extract_archive(archive_path: str) -> str:
    """Extract an archive into a temporary directory and return that path."""
    extract_dir = tempfile.mkdtemp(prefix="gptrepo-archive-")
    extract_dir_abs = os.path.abspath(extract_dir)
    if archive_path.lower().endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zf:
            for name in zf.namelist():
                normalized = os.path.normpath(name)
                if os.path.isabs(normalized) or normalized.startswith(".."):
                    raise RuntimeError(
                        f"Refusing to extract path outside target dir: {name}"
                    )
            zf.extractall(extract_dir)
    elif tarfile.is_tarfile(archive_path):
        with tarfile.open(archive_path) as tf:
            # Guard against absolute / parent traversal in tarballs.
            for member in tf.getmembers():
                normalized = member.name.replace("\\", "/").lstrip("/")
                resolved = os.path.abspath(os.path.join(extract_dir_abs, normalized))
                if resolved != extract_dir_abs and not resolved.startswith(
                    extract_dir_abs + os.sep
                ):
                    raise RuntimeError(
                        f"Refusing to extract path outside target dir: {member.name}"
                    )
            tf.extractall(extract_dir)
    else:
        raise RuntimeError(f"Unsupported archive type: {archive_path}")
    # If the archive contains a single top-level directory, promote it.
    entries = [e for e in os.listdir(extract_dir) if not e.startswith(".")]
    if len(entries) == 1:
        inner = os.path.join(extract_dir, entries[0])
        if os.path.isdir(inner):
            return inner
    return extract_dir


@contextmanager
def open_remote(
    url: str,
    *,
    depth: Optional[int] = None,
    branch: Optional[str] = None,
) -> Iterator[RemoteRepository]:
    """Context manager that returns a :class:`RemoteRepository` for ``url``.

    The temporary directory the loader returns is removed when the context exits
    regardless of whether the body raised. The classification logic mirrors the
    behaviour documented in the module-level docstring.
    """
    kind = _classify_url(url)
    cleanup_dirs: list = []
    try:
        if kind == "local":
            local_path = _file_url_to_path(url) if url.lower().startswith("file:") else url
            yield RemoteRepository(url=url, path=local_path, kind="local")
            return

        if kind == "archive":
            archive_path = _download_to_tempfile(url)
            try:
                extracted = _extract_archive(archive_path)
            finally:
                try:
                    os.unlink(archive_path)
                except OSError:
                    pass
            cleanup_dirs.append(extracted)
            yield RemoteRepository(url=url, path=extracted, kind="archive",
                                   cleanup_dirs=cleanup_dirs)
            return

        if kind in {"git", "git-host"}:
            if not HAS_GITPYTHON:
                raise RuntimeError(
                    "GitPython is required to load git URLs. Install with "
                    "`pip install gpt-repository-loader[git]`."
                )
            clone_url, host_branch = (
                _normalize_git_url(url) if kind == "git-host" else (url, None)
            )
            target = tempfile.mkdtemp(prefix="gptrepo-clone-")
            cleanup_dirs.append(target)
            clone_repo(
                clone_url,
                target,
                depth=depth,
                branch=branch or host_branch,
            )
            yield RemoteRepository(
                url=url, path=target, kind=kind, cleanup_dirs=cleanup_dirs
            )
            return

        raise ValueError(f"Unsupported URL kind: {kind}")
    finally:
        for d in cleanup_dirs:
            shutil.rmtree(d, ignore_errors=True)


def is_url(spec: str) -> bool:
    """Return ``True`` if ``spec`` looks like a URL the remote loader handles."""
    if "://" in spec:
        return True
    if spec.startswith("git@"):
        return True
    if _GIT_HOST_PATTERN.match(spec):
        return True
    return False
