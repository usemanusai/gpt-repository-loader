"""High-level orchestration: walk a repository and produce a bundle.

This module contains the central :class:`BundleOptions` configuration object
and three entry points:

* :func:`process_repository` - back-compatible function with the exact signature
  used by the original ``gpt_repository_loader.py`` script. Existing tests and
  third-party callers continue to work unchanged.
* :func:`bundle_repository` - the rich pipeline used by the new CLI and Web UI.
  Returns a :class:`BundleResult` with the rendered bundle text, per-file
  statistics, the list of ignored files (with reasons) and token counts.
* :func:`stream_repository` - generator variant that yields one :class:`FileEntry`
  at a time and is used by the chunking pipeline so it does not need to hold
  the entire bundle in memory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Sequence, TextIO

from .binary import detect_binary
from .chunking import FileSection, chunk_sections
from .compression import encode_content, encoding_line
from .constants import (
    ENCODING_BASE64,
    ENCODING_GZIP_BASE64,
    ENCODING_RAW,
    END_MARKER,
    FILE_DELIMITER,
)
from .git_integration import describe_repo, git_check_ignore, tracked_files
from .ignore import (
    IgnoreDecision,
    compile_patterns,
    load_ignore_files,
    package_fallback_gptignore,
)
from .preamble import PreambleConfig, resolve_preamble
from .tokens import TokenCounter


@dataclass
class FileEntry:
    """A file that has been considered by the bundler."""

    abs_path: str
    rel_path: str
    included: bool
    decision: Optional[IgnoreDecision] = None
    reason: str = ""
    encoding: str = ENCODING_RAW
    content: str = ""
    binary_reason: Optional[str] = None
    token_estimate: int = 0
    size_bytes: int = 0


@dataclass
class BundleResult:
    """Result of a bundle run."""

    text: str
    files: List[FileEntry] = field(default_factory=list)
    ignored: List[FileEntry] = field(default_factory=list)
    preamble: str = ""
    token_count: int = 0
    token_method: str = "fallback"
    discovered_ignore_files: List[str] = field(default_factory=list)
    repo_info: object = None
    warnings: List[str] = field(default_factory=list)
    chunks: List[str] = field(default_factory=list)

    @property
    def included_count(self) -> int:
        return len(self.files)

    @property
    def ignored_count(self) -> int:
        return len(self.ignored)


@dataclass
class BundleOptions:
    """Options for the bundle pipeline."""

    repo_path: str
    preamble: PreambleConfig = field(default_factory=PreambleConfig)
    ignore_lines: List[str] = field(default_factory=list)
    include_gitignore: bool = True
    include_gptignore: bool = True
    skip_binary: bool = True
    follow_symlinks: bool = False
    tracked_only: bool = False
    encoding_mode: str = ENCODING_RAW
    token_limit: int = 0
    chunk_when_over_limit: bool = False
    model: Optional[str] = None
    use_git_check_ignore: bool = False
    sort_paths: bool = True
    extra_excludes: List[str] = field(default_factory=list)
    extra_includes: List[str] = field(default_factory=list)
    package_fallback_path: Optional[str] = None


def _iter_candidate_files(
    repo_path: str, *, follow_symlinks: bool, sort_paths: bool
) -> Iterator[str]:
    for root, dirs, files in os.walk(repo_path, followlinks=follow_symlinks):
        if sort_paths:
            dirs.sort()
            files.sort()
        for name in files:
            yield os.path.join(root, name)


def _build_matcher(opts: BundleOptions) -> tuple:
    matcher, discovered = load_ignore_files(
        opts.repo_path,
        extra_patterns=list(opts.ignore_lines) + list(opts.extra_excludes),
        include_gitignore=opts.include_gitignore,
        include_gptignore=opts.include_gptignore,
        package_fallback=opts.package_fallback_path or package_fallback_gptignore(),
    )
    # ``--include`` patterns are added as negation entries so that they override
    # earlier matches just like a ``!`` line in a gitignore file would.
    if opts.extra_includes:
        matcher.extend(
            compile_patterns(
                [f"!{pattern}" for pattern in opts.extra_includes],
                source="cli:--include",
            )
        )
    return matcher, discovered


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="ignore") as fh:
        return fh.read()


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _content_collides_with_format(content: str) -> bool:
    """Return ``True`` when ``content`` would corrupt the text bundle format.

    Plain bundle output uses ``----`` on its own line as the file delimiter
    and ``--END--`` on its own line as the terminator. A file that contains
    either of those lines verbatim (for example a README documenting the
    format) would be mis-parsed if emitted as raw text. The bundler detects
    the collision and transparently upgrades the file to base64 encoding so
    that the reverse loader can still reconstruct it byte for byte.
    """
    for line in content.splitlines():
        stripped = line.rstrip()
        if stripped == FILE_DELIMITER or stripped == END_MARKER:
            return True
    return False


def _safe_encoding(requested: str, content: str) -> str:
    """Pick an encoding that keeps the bundle parseable.

    ``raw`` encoding is upgraded to ``base64`` when ``content`` contains a
    line that collides with the delimiter or end marker. ``base64`` and
    ``gzip+base64`` are returned unchanged because both already escape the
    payload.
    """
    if requested in {ENCODING_BASE64, ENCODING_GZIP_BASE64}:
        return requested
    if _content_collides_with_format(content):
        return ENCODING_BASE64
    return ENCODING_RAW


def _resolve_tracked(opts: BundleOptions) -> Optional[set]:
    if not opts.tracked_only:
        return None
    files = tracked_files(opts.repo_path)
    if files is None:
        return None
    return {f.replace("\\", "/") for f in files}


def stream_repository(opts: BundleOptions) -> Iterator[FileEntry]:
    """Yield :class:`FileEntry` objects for every file considered.

    Both included and excluded files are yielded so that callers can build
    ignored-file reports.
    """
    matcher, _ = _build_matcher(opts)
    repo_path = os.path.abspath(opts.repo_path)
    tracked = _resolve_tracked(opts)
    git_ignore_cache: List[str] = []

    candidates = list(_iter_candidate_files(
        repo_path, follow_symlinks=opts.follow_symlinks, sort_paths=opts.sort_paths
    ))
    rel_candidates = [
        os.path.relpath(path, repo_path).replace("\\", "/") for path in candidates
    ]

    if opts.use_git_check_ignore and rel_candidates:
        git_ignore_cache = git_check_ignore(repo_path, rel_candidates)
    git_ignore_set = set(git_ignore_cache)

    for abs_path, rel_path in zip(candidates, rel_candidates):
        if tracked is not None and rel_path not in tracked:
            yield FileEntry(
                abs_path=abs_path,
                rel_path=rel_path,
                included=False,
                reason="not tracked by git (tracked_only=True)",
                size_bytes=_file_size(abs_path),
            )
            continue

        decision = matcher.decide(rel_path, is_dir=False)
        if decision.ignored:
            yield FileEntry(
                abs_path=abs_path,
                rel_path=rel_path,
                included=False,
                decision=decision,
                reason=decision.reason,
                size_bytes=_file_size(abs_path),
            )
            continue

        if rel_path in git_ignore_set:
            yield FileEntry(
                abs_path=abs_path,
                rel_path=rel_path,
                included=False,
                reason="ignored by git check-ignore (global or info/exclude)",
                size_bytes=_file_size(abs_path),
            )
            continue

        if opts.skip_binary:
            bd = detect_binary(abs_path)
            if bd.is_binary:
                yield FileEntry(
                    abs_path=abs_path,
                    rel_path=rel_path,
                    included=False,
                    reason=f"binary: {bd.reason}",
                    binary_reason=bd.reason,
                    size_bytes=_file_size(abs_path),
                )
                continue

        try:
            text = _read_text(abs_path)
        except OSError as exc:
            yield FileEntry(
                abs_path=abs_path,
                rel_path=rel_path,
                included=False,
                reason=f"unreadable: {exc}",
                size_bytes=_file_size(abs_path),
            )
            continue

        encoded = encode_content(text, mode=_safe_encoding(opts.encoding_mode, text))
        yield FileEntry(
            abs_path=abs_path,
            rel_path=rel_path,
            included=True,
            decision=decision,
            reason=decision.reason if decision.pattern else "no ignore pattern matched",
            encoding=encoded.encoding,
            content=encoded.text,
            size_bytes=_file_size(abs_path),
        )


def _render_section(entry: FileEntry) -> str:
    enc_meta = encoding_line(entry.encoding)
    if enc_meta is None:
        return f"{FILE_DELIMITER}\n{entry.rel_path}\n{entry.content}\n"
    return f"{FILE_DELIMITER}\n{entry.rel_path}\n{enc_meta}\n{entry.content}\n"


def bundle_repository(opts: BundleOptions) -> BundleResult:
    """Run the full bundle pipeline and return a :class:`BundleResult`."""
    matcher_info: tuple = _build_matcher(opts)
    _matcher, discovered = matcher_info
    counter = TokenCounter(model=opts.model)

    files: List[FileEntry] = []
    ignored: List[FileEntry] = []
    sections: List[FileSection] = []
    warnings: List[str] = []

    for entry in stream_repository(opts):
        if entry.included:
            entry.token_estimate = counter.count(_render_section(entry)).tokens
            files.append(entry)
            sections.append(
                FileSection(
                    rel_path=entry.rel_path,
                    body=entry.content,
                    encoding=entry.encoding,
                )
            )
        else:
            ignored.append(entry)

    repo_info = describe_repo(opts.repo_path)

    preamble_text = resolve_preamble(opts.preamble)
    body_parts: List[str] = []
    if preamble_text:
        body_parts.append(preamble_text.rstrip("\n") + "\n")
    for entry in files:
        body_parts.append(_render_section(entry))
    body_parts.append(END_MARKER)
    bundle_text = "".join(body_parts)

    total_tokens = counter.count(bundle_text)
    if opts.token_limit and total_tokens.tokens > opts.token_limit:
        warnings.append(
            f"Bundle exceeds token limit: {total_tokens.tokens} > {opts.token_limit} "
            f"({total_tokens.method} backend)"
        )

    chunks: List[str] = []
    if opts.chunk_when_over_limit and opts.token_limit and total_tokens.tokens > opts.token_limit:
        chunk_objs = chunk_sections(
            sections, counter=counter, limit=opts.token_limit, delimiter=FILE_DELIMITER
        )
        for idx, chunk in enumerate(chunk_objs, start=1):
            header = (
                f"{preamble_text.rstrip(chr(10))}\n\n"
                f"[Chunk {idx} of {len(chunk_objs)}]\n"
            ) if preamble_text else f"[Chunk {idx} of {len(chunk_objs)}]\n"
            chunks.append(header + chunk.render(FILE_DELIMITER) + END_MARKER)

    return BundleResult(
        text=bundle_text,
        files=files,
        ignored=ignored,
        preamble=preamble_text,
        token_count=total_tokens.tokens,
        token_method=total_tokens.method,
        discovered_ignore_files=discovered,
        repo_info=repo_info,
        warnings=warnings,
        chunks=chunks,
    )


# Legacy API ----------------------------------------------------------------


def process_repository(
    repo_path: str, ignore_list: Sequence[str], output_file: TextIO
) -> None:
    """Backward-compatible single-pass walker matching the original module API.

    Parameters preserve the exact signature used by the original script and
    third-party callers. The function writes file sections in the historical
    layout (no preamble, no terminator - those are the caller's responsibility,
    just like in the legacy script) using forward-slash-normalised relative
    paths. Ignore patterns are evaluated with :func:`should_ignore` from
    :mod:`gpt_repository_loader.ignore` so that legacy semantics are preserved.
    """
    from .ignore import should_ignore  # local import avoids cycles
    for root, _dirs, files in os.walk(repo_path):
        files.sort()
        for file in files:
            file_path = os.path.join(root, file)
            relative_file_path = os.path.relpath(file_path, repo_path)
            # Normalise to forward slashes so that bundles produced on Windows
            # match the same layout LLMs (and our test fixtures) expect.
            normalized = relative_file_path.replace(os.sep, "/")
            if not should_ignore(relative_file_path, list(ignore_list)):
                with open(file_path, errors="ignore") as fh:
                    contents = fh.read()
                output_file.write("-" * 4 + "\n")
                output_file.write(f"{normalized}\n")
                output_file.write(f"{contents}\n")
