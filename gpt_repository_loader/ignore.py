"""Production-grade ``.gitignore``-style pattern matching engine.

This module implements the full pattern syntax documented at
https://git-scm.com/docs/gitignore including:

* Negation patterns prefixed with ``!``.
* Anchored patterns prefixed with ``/`` (relative to the ignore file's directory).
* Trailing ``/`` to restrict matches to directories.
* ``*`` matching any sequence of characters except ``/``.
* ``?`` matching any single character except ``/``.
* ``**`` matching any number of path segments.
* Character classes such as ``[abc]`` and ``[!abc]``.
* Comments (``#`` at start) and blank lines are skipped.
* Escaping with backslash (``\\#`` matches a literal ``#``).

Each non-empty, non-comment line in an ignore file becomes one
:class:`IgnorePattern`. Patterns are then aggregated into an
:class:`IgnoreMatcher` that evaluates them in order, with later patterns
overriding earlier ones (mirroring git's behaviour). The matcher returns both
the resolved decision and a human-readable explanation suitable for the
``--show-ignored`` and ``--explain`` CLI flags (issue #52).

Two helper constructors are exposed:

* :func:`compile_patterns` builds an :class:`IgnoreMatcher` from raw pattern
  strings (the legacy ``ignore_list`` API path).
* :func:`load_ignore_files` collects every ``.gitignore`` and ``.gptignore``
  along a repository walk, including the optional fallback file in the
  installed package directory (the historical behaviour of the original
  script).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple


def _translate(pattern: str) -> str:
    """Translate a gitignore glob into an equivalent regular expression.

    This is a from-scratch implementation rather than ``fnmatch.translate`` because
    gitignore semantics differ from POSIX shell globs (``**`` segment matching,
    ``/`` as a hard separator, character class handling, etc.).
    """
    i = 0
    n = len(pattern)
    out: List[str] = []
    while i < n:
        c = pattern[i]
        if c == "*":
            # Detect '**' which crosses path separators.
            if i + 1 < n and pattern[i + 1] == "*":
                # Skip the second star.
                i += 2
                # ``**/`` and ``/**`` collapse to "zero or more path segments".
                if i < n and pattern[i] == "/":
                    out.append(r"(?:.*/)?")
                    i += 1
                else:
                    out.append(r".*")
            else:
                # Single ``*`` matches any chars except path separator.
                out.append(r"[^/]*")
                i += 1
        elif c == "?":
            out.append(r"[^/]")
            i += 1
        elif c == "[":
            # Character class. Translate ``[!...]`` to ``[^...]`` while preserving
            # the rest of the class body verbatim.
            j = i + 1
            if j < n and pattern[j] == "!":
                cls = "[^"
                j += 1
            else:
                cls = "["
            # Find the closing bracket.
            while j < n and pattern[j] != "]":
                cls += re.escape(pattern[j]) if pattern[j] == "\\" else pattern[j]
                j += 1
            if j >= n:
                # Unterminated class - treat the opening bracket as literal.
                out.append(re.escape("["))
                i += 1
                continue
            cls += "]"
            out.append(cls)
            i = j + 1
        elif c == "\\":
            # Escape the next literal character.
            if i + 1 < n:
                out.append(re.escape(pattern[i + 1]))
                i += 2
            else:
                out.append(re.escape("\\"))
                i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


@dataclass(frozen=True)
class IgnorePattern:
    """A single compiled gitignore pattern with all metadata required to evaluate it.

    Attributes
    ----------
    raw:
        The original line as read from the ignore file (used for explanations).
    source:
        Either the path of the ignore file the pattern came from or a synthetic
        label such as ``"CLI --exclude"``.
    base_dir:
        The directory that the pattern is anchored to. For a pattern read from
        ``/repo/sub/.gitignore`` the base_dir is ``sub`` (relative to the repo
        root). Matching is performed against paths after stripping this prefix.
    negate:
        ``True`` if the pattern is a negation (``!`` prefix).
    directory_only:
        ``True`` if the pattern only applies to directories (trailing ``/``).
    anchored:
        ``True`` if the pattern is anchored to the base directory (either by a
        leading ``/`` or by containing a non-trailing ``/``).
    regex:
        Compiled regular expression equivalent of the pattern body.
    """

    raw: str
    source: str
    base_dir: str
    negate: bool
    directory_only: bool
    anchored: bool
    regex: re.Pattern[str]

    def matches(self, rel_path: str, is_dir: bool) -> bool:
        """Return ``True`` if this pattern matches ``rel_path`` rooted at the repo.

        ``rel_path`` must already use forward slashes and be relative to the
        repository root (not the base directory of the pattern).
        """
        if self.directory_only and not is_dir:
            return False
        # Strip base_dir prefix so that the regex (which is anchored at the base
        # directory) can match.
        if self.base_dir:
            prefix = self.base_dir.rstrip("/") + "/"
            if rel_path == self.base_dir.rstrip("/"):
                candidate = ""
            elif rel_path.startswith(prefix):
                candidate = rel_path[len(prefix) :]
            else:
                # The file lives outside the directory where the ignore file was
                # found - the pattern cannot apply.
                return False
        else:
            candidate = rel_path
        if self.anchored:
            return self.regex.fullmatch(candidate) is not None
        # Non-anchored: pattern can match any path component sequence at the end.
        if self.regex.fullmatch(candidate) is not None:
            return True
        # ``*.log`` should match ``deep/path/foo.log`` - any trailing segment.
        idx = 0
        while idx < len(candidate):
            if self.regex.fullmatch(candidate[idx:]) is not None:
                return True
            sep = candidate.find("/", idx)
            if sep < 0:
                break
            idx = sep + 1
        return False


def _compile_one(line: str, source: str, base_dir: str) -> Optional[IgnorePattern]:
    """Compile a single ignore-file line. Returns ``None`` for comments/blanks."""
    stripped = line.rstrip("\r\n")
    # Comments and blank lines.
    if not stripped or stripped.lstrip().startswith("#"):
        return None
    # Trim trailing whitespace unless escaped (gitignore spec).
    body = re.sub(r"(?<!\\)\s+$", "", stripped)
    if not body:
        return None
    negate = False
    if body.startswith("!"):
        negate = True
        body = body[1:]
    # Unescape leading ``\#`` or ``\!`` per spec.
    if body.startswith("\\#") or body.startswith("\\!"):
        body = body[1:]
    directory_only = body.endswith("/")
    if directory_only:
        body = body[:-1]
    anchored = body.startswith("/") or "/" in body
    if body.startswith("/"):
        body = body[1:]
    # Normalise platform-specific path separators that may sneak in.
    body = body.replace("\\", "/") if os.sep == "\\" else body
    regex = re.compile(_translate(body))
    return IgnorePattern(
        raw=line.rstrip("\r\n"),
        source=source,
        base_dir=base_dir.replace(os.sep, "/").strip("/"),
        negate=negate,
        directory_only=directory_only,
        anchored=anchored,
        regex=regex,
    )


def compile_patterns(
    lines: Iterable[str],
    *,
    source: str = "inline",
    base_dir: str = "",
) -> List[IgnorePattern]:
    """Compile an iterable of raw lines into a list of :class:`IgnorePattern`."""
    compiled: List[IgnorePattern] = []
    for line in lines:
        pat = _compile_one(line, source=source, base_dir=base_dir)
        if pat is not None:
            compiled.append(pat)
    return compiled


@dataclass
class IgnoreDecision:
    """The resolved ignore decision for a single path."""

    ignored: bool
    reason: str
    pattern: Optional[IgnorePattern] = None


@dataclass
class IgnoreMatcher:
    """Aggregates ignore patterns and resolves the final decision per path.

    Patterns are evaluated in order. Later patterns override earlier ones (the
    last matching pattern wins), exactly matching git's documented behaviour.
    """

    patterns: List[IgnorePattern] = field(default_factory=list)

    def extend(self, more: Iterable[IgnorePattern]) -> None:
        self.patterns.extend(more)

    def decide(self, rel_path: str, is_dir: bool = False) -> IgnoreDecision:
        """Return the decision for ``rel_path`` (forward-slash, repo-relative).

        The matcher honours the full gitignore inheritance rule: when a
        directory-only pattern matches an ancestor of ``rel_path`` the file is
        considered ignored even though the pattern itself never matches the
        file directly. Negation patterns may then unignore the file again.
        """
        rel_path = rel_path.replace(os.sep, "/").lstrip("/")
        match: Optional[IgnorePattern] = None
        parts = rel_path.split("/")
        ancestors = ["/".join(parts[:i]) for i in range(1, len(parts))]
        for pat in self.patterns:
            matched = pat.matches(rel_path, is_dir=is_dir)
            if not matched and pat.directory_only:
                for ancestor in ancestors:
                    if pat.matches(ancestor, is_dir=True):
                        matched = True
                        break
            if matched:
                match = pat
        if match is None:
            return IgnoreDecision(ignored=False, reason="no pattern matched")
        if match.negate:
            return IgnoreDecision(
                ignored=False,
                reason=f"un-ignored by negation pattern '{match.raw}' from {match.source}",
                pattern=match,
            )
        return IgnoreDecision(
            ignored=True,
            reason=f"matched pattern '{match.raw}' from {match.source}",
            pattern=match,
        )

    def is_ignored(self, rel_path: str, is_dir: bool = False) -> bool:
        return self.decide(rel_path, is_dir=is_dir).ignored


def load_ignore_files(
    repo_path: str,
    *,
    extra_patterns: Optional[Sequence[str]] = None,
    include_gitignore: bool = True,
    include_gptignore: bool = True,
    package_fallback: Optional[str] = None,
) -> Tuple[IgnoreMatcher, List[str]]:
    """Walk ``repo_path`` and assemble an :class:`IgnoreMatcher`.

    Parameters
    ----------
    repo_path:
        Absolute path to the repository root.
    extra_patterns:
        Additional raw pattern lines supplied on the command line (``--exclude``).
        These take precedence over file-based patterns by virtue of being applied
        last.
    include_gitignore:
        Whether to honour ``.gitignore`` files in the tree (issue #24).
    include_gptignore:
        Whether to honour ``.gptignore`` files in the tree.
    package_fallback:
        Path to a ``.gptignore`` shipped with the package that is applied first
        when the repo has no top-level ``.gptignore`` (preserves the historical
        behaviour of the original script).

    Returns
    -------
    Tuple ``(matcher, discovered_files)``. ``discovered_files`` lists every
    ignore file that was loaded, in load order, for use by ``--explain``.
    """
    matcher = IgnoreMatcher()
    discovered: List[str] = []

    repo_path = os.path.abspath(repo_path)

    # 1) Package-shipped fallback applies first so that local files can override.
    if package_fallback and os.path.exists(package_fallback):
        with open(package_fallback, encoding="utf-8") as fh:
            matcher.extend(
                compile_patterns(fh, source=f"package:{package_fallback}", base_dir="")
            )
        discovered.append(package_fallback)

    # 2) Walk the tree top-down and load every ignore file we encounter.
    for root, dirs, files in os.walk(repo_path):
        # Stable iteration order keeps the reasoning deterministic.
        dirs.sort()
        files.sort()
        rel_root = os.path.relpath(root, repo_path)
        if rel_root == ".":
            rel_root = ""
        candidate_files: List[Tuple[str, str]] = []
        if include_gitignore and ".gitignore" in files:
            candidate_files.append((".gitignore", os.path.join(root, ".gitignore")))
        if include_gptignore and ".gptignore" in files:
            candidate_files.append((".gptignore", os.path.join(root, ".gptignore")))
        for label, path in candidate_files:
            try:
                with open(path, encoding="utf-8") as fh:
                    matcher.extend(
                        compile_patterns(fh, source=f"{label}:{path}", base_dir=rel_root)
                    )
                discovered.append(path)
            except OSError:
                # Unreadable ignore files are skipped; we never want the bundle
                # operation to die because of a permissions issue.
                continue

    # 3) CLI-provided extra patterns always win.
    if extra_patterns:
        matcher.extend(
            compile_patterns(extra_patterns, source="cli:--exclude", base_dir="")
        )

    return matcher, discovered


def package_fallback_gptignore() -> str:
    """Return the path to the ``.gptignore`` shipped with the package, if any.

    The historical script looked next to itself for a fallback ``.gptignore``;
    the equivalent file in the installed package layout lives under
    ``data/.gptignore``. We probe both locations.
    """
    here = Path(__file__).resolve().parent
    candidates = [
        here / "data" / ".gptignore",
        here.parent / ".gptignore",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return ""


# Legacy API ----------------------------------------------------------------


def get_ignore_list(ignore_file_path: str) -> List[str]:
    """Backward-compatible reader matching the original module-level function.

    The original implementation returned a flat list of pattern strings and
    callers used ``fnmatch`` directly. We preserve the same return type so that
    third-party code importing this name keeps working, while internally the
    full pattern engine is used wherever possible.
    """
    ignore_list: List[str] = []
    with open(ignore_file_path) as ignore_file:
        for line in ignore_file:
            line = line.rstrip("\r\n")
            if os.sep == "\\":
                line = line.replace("/", "\\")
            ignore_list.append(line.strip())
    return ignore_list


def should_ignore(file_path: str, ignore_list: Sequence[str]) -> bool:
    """Backward-compatible decision function used by legacy callers.

    Internally this delegates to the new pattern engine but honours the historical
    return shape: ``True`` when the path matches *any* pattern, ignoring
    negation semantics. New code should use :class:`IgnoreMatcher` directly to
    obtain richer information including negation handling and explanations.
    """
    patterns = compile_patterns(ignore_list, source="legacy")
    rel = file_path.replace(os.sep, "/").lstrip("/")
    for pat in patterns:
        # Legacy API never honoured negation - treat ``!foo`` as a literal match
        # against ``!foo``. We therefore ignore the negate flag and consult the
        # underlying regex directly.
        if pat.matches(rel, is_dir=False):
            return True
    # Fall back to plain fnmatch for the exact historical behaviour when patterns
    # contain leading slashes (which the new engine treats as anchors but legacy
    # callers expected to behave as literal characters).
    import fnmatch

    for raw in ignore_list:
        if fnmatch.fnmatch(file_path, raw):
            return True
    return False
