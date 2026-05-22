"""GitPython integration (issue #32) for richer repository introspection.

When ``GitPython`` is installed the bundler can:

* Consult ``git check-ignore`` to honour any ignore source git knows about
  (global excludes, ``info/exclude``, etc.) which the in-tree pattern engine
  alone cannot see.
* Determine the active branch, HEAD commit and the working-tree status so the
  preamble can record exact provenance.
* Enumerate only tracked files when the user passes ``--tracked-only``.

When GitPython is *not* installed the loader continues to work via a pure
filesystem walk; this module simply exposes ``HAS_GITPYTHON = False`` so that
callers can branch accordingly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

try:  # pragma: no cover - exercised in integration tests
    import git  # type: ignore
    HAS_GITPYTHON = True
except Exception:  # pragma: no cover
    git = None  # type: ignore
    HAS_GITPYTHON = False


@dataclass
class RepoInfo:
    """Provenance information for the repository being bundled."""

    path: str
    is_repo: bool
    head_sha: Optional[str] = None
    branch: Optional[str] = None
    is_dirty: bool = False
    remote_url: Optional[str] = None


def describe_repo(path: str) -> RepoInfo:
    """Return :class:`RepoInfo` for ``path``."""
    if not HAS_GITPYTHON:
        return RepoInfo(path=path, is_repo=os.path.isdir(os.path.join(path, ".git")))
    try:
        repo = git.Repo(path)
    except Exception:
        return RepoInfo(path=path, is_repo=False)
    try:
        head_sha = repo.head.commit.hexsha
    except Exception:
        head_sha = None
    try:
        branch = repo.active_branch.name
    except Exception:
        branch = None
    try:
        is_dirty = repo.is_dirty(untracked_files=True)
    except Exception:
        is_dirty = False
    remote_url: Optional[str] = None
    try:
        if repo.remotes:
            remote_url = next(iter(repo.remotes)).url
    except Exception:
        remote_url = None
    return RepoInfo(
        path=path,
        is_repo=True,
        head_sha=head_sha,
        branch=branch,
        is_dirty=is_dirty,
        remote_url=remote_url,
    )


def tracked_files(path: str) -> Optional[List[str]]:
    """Return the list of tracked files (relative paths) or ``None`` when
    GitPython is unavailable or the directory is not a repo."""
    if not HAS_GITPYTHON:
        return None
    try:
        repo = git.Repo(path)
    except Exception:
        return None
    try:
        ls = repo.git.ls_files()
    except Exception:
        return None
    return [line for line in ls.splitlines() if line.strip()]


def git_check_ignore(path: str, candidates: Sequence[str]) -> List[str]:
    """Return the subset of ``candidates`` that ``git check-ignore`` agrees to
    treat as ignored. Returns an empty list when GitPython is unavailable.
    """
    if not HAS_GITPYTHON or not candidates:
        return []
    try:
        repo = git.Repo(path)
    except Exception:
        return []
    try:
        out = repo.git.check_ignore("--stdin", istream="\n".join(candidates))
    except Exception:
        return []
    return [line for line in out.splitlines() if line]


def clone_repo(
    url: str, destination: str, *, depth: Optional[int] = None,
    branch: Optional[str] = None, single_branch: bool = True,
) -> str:
    """Clone ``url`` into ``destination`` using GitPython.

    Returns the destination path. Raises :class:`RuntimeError` if GitPython is
    not installed.
    """
    if not HAS_GITPYTHON:
        raise RuntimeError(
            "GitPython is required to clone remote repositories. Install with "
            "`pip install gpt-repository-loader[git]`."
        )
    multi_kwargs = {}
    if depth is not None:
        multi_kwargs["depth"] = depth
    if branch is not None:
        multi_kwargs["branch"] = branch
    if single_branch:
        multi_kwargs["single_branch"] = True
    git.Repo.clone_from(url, destination, **multi_kwargs)
    return destination


def diff_files(path: str, *, against: str = "HEAD") -> Iterable[str]:
    """Yield the relative paths of files that differ from ``against``."""
    if not HAS_GITPYTHON:
        return []
    try:
        repo = git.Repo(path)
        return [item.a_path for item in repo.index.diff(against)]
    except Exception:
        return []
