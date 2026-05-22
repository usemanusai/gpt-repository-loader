"""Production CLI for ``gpt-repository-loader``.

The CLI exposes the historical single-positional invocation (kept for backward
compatibility with the ``python gpt_repository_loader.py /path`` flow that the
README has shipped since the project began) as well as four explicit
subcommands:

* ``bundle`` (default) - convert a repo (local path or remote URL) into a
  bundle.
* ``unbundle`` - reverse a bundle back into a directory tree.
* ``init-preamble`` - drop a starter ``.gpt-preamble`` file next to a repo.
* ``serve`` - launch the Flask Web UI.

Every flag is documented in ``--help``; the implementation favours explicit
error messages and exit codes so it can be reliably scripted from CI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional, Sequence

from .clipboard import ClipboardUnavailableError, copy_to_clipboard
from .compression import write_zip_bundle
from .constants import (
    DEFAULT_OUTPUT_FILENAME,
    DEFAULT_TOKEN_LIMIT,
    ENCODING_BASE64,
    ENCODING_GZIP_BASE64,
    ENCODING_RAW,
)
from .core import BundleOptions, bundle_repository
from .preamble import PreambleConfig, init_preamble_file
from .reverse import unbundle
from .tokens import model_context_size
from .url_loader import is_url, open_remote


def _build_bundle_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "repo",
        help="Path to a local repository, or a URL (https/ssh/git/file://) "
             "pointing at a remote repository or downloadable archive.",
    )
    parser.add_argument(
        "-o", "--output",
        default=DEFAULT_OUTPUT_FILENAME,
        help="Path of the bundle file to write (default: output.txt).",
    )
    parser.add_argument(
        "-p", "--preamble",
        help="Path to a file whose contents are emitted as the bundle preamble.",
    )
    parser.add_argument(
        "--preamble-stats",
        action="store_true",
        help="Append file/token statistics to the default preamble.",
    )
    parser.add_argument(
        "-e", "--exclude",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Additional .gitignore-style pattern to exclude. May be repeated.",
    )
    parser.add_argument(
        "-i", "--include",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Additional .gitignore-style pattern to forcibly include "
             "(equivalent to a negation pattern). May be repeated.",
    )
    parser.add_argument(
        "--no-gitignore",
        dest="include_gitignore",
        action="store_false",
        help="Do not load .gitignore files (only .gptignore is honoured).",
    )
    parser.add_argument(
        "--no-gptignore",
        dest="include_gptignore",
        action="store_false",
        help="Do not load .gptignore files.",
    )
    parser.add_argument(
        "--include-binary",
        dest="skip_binary",
        action="store_false",
        help="Include binary files (default: skip via heuristic detection).",
    )
    parser.add_argument(
        "--follow-symlinks",
        action="store_true",
        help="Follow symbolic links while walking the repository.",
    )
    parser.add_argument(
        "--tracked-only",
        action="store_true",
        help="Only include files that are tracked by git (requires GitPython).",
    )
    parser.add_argument(
        "--use-git-check-ignore",
        action="store_true",
        help="Also honour 'git check-ignore' (global excludes, info/exclude).",
    )
    parser.add_argument(
        "--encoding-mode",
        choices=[ENCODING_RAW, ENCODING_BASE64, ENCODING_GZIP_BASE64],
        default=ENCODING_RAW,
        help="Per-file content encoding. 'gzip+base64' enables --compress.",
    )
    parser.add_argument(
        "--compress",
        action="store_const",
        const=ENCODING_GZIP_BASE64,
        dest="encoding_mode",
        help="Shortcut for --encoding-mode gzip+base64.",
    )
    parser.add_argument(
        "--format",
        choices=["text", "zip", "json"],
        default="text",
        help="Output format. 'zip' produces a ZIP containing bundle + raw files. "
             "'json' emits a JSON document with files+metadata.",
    )
    parser.add_argument(
        "--token-limit",
        type=int,
        default=0,
        help=(
            "Warn (and optionally chunk) when the bundle exceeds this many "
            "tokens. Pass 0 to disable. Defaults to 0; when a --model is "
            f"supplied the model's context window is used unless explicitly "
            f"overridden (default fallback: {DEFAULT_TOKEN_LIMIT})."
        ),
    )
    parser.add_argument(
        "--chunk",
        dest="chunk_when_over_limit",
        action="store_true",
        help="When the bundle exceeds --token-limit, split it into multiple "
             "files named <output>.chunkNN.<ext>.",
    )
    parser.add_argument(
        "--model",
        help="Optional model name (e.g. gpt-4, gpt-4o). When provided, the "
             "default --token-limit is set to the model's context window and "
             "the model is passed to tiktoken for accurate counting.",
    )
    parser.add_argument(
        "--clipboard",
        action="store_true",
        help="Copy the bundle text to the system clipboard.",
    )
    parser.add_argument(
        "--show-ignored",
        action="store_true",
        help="Print a report of which files were excluded and why.",
    )
    parser.add_argument(
        "--json-report",
        metavar="PATH",
        help="Write a machine-readable JSON report (file lists, token counts, "
             "warnings, ignore decisions) to PATH.",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress non-error output.",
    )
    parser.add_argument(
        "--no-sort",
        dest="sort_paths",
        action="store_false",
        help="Preserve filesystem-defined walk order instead of sorting paths.",
    )
    parser.add_argument(
        "--clone-depth",
        type=int,
        default=None,
        help="For remote URLs, perform a shallow clone with this depth.",
    )
    parser.add_argument(
        "--branch",
        help="For remote git URLs, clone this branch instead of the default.",
    )


def _build_unbundle_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("bundle", help="Path to the bundle file.")
    parser.add_argument(
        "-d", "--destination",
        default="unbundled",
        help="Destination directory (default: ./unbundled).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing files instead of refusing.",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="If provided, only extract these relative paths. May be repeated.",
    )


def _build_init_preamble_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "path",
        nargs="?",
        default=".gpt-preamble",
        help="Output path for the new preamble file (default: .gpt-preamble).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing preamble file.",
    )


def _build_serve_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Host interface to bind (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port", type=int, default=5050,
        help="Port to listen on (default: 5050).",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Enable Flask debug mode.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gpt-repository-loader",
        description="Convert a Git repository into a single LLM-friendly bundle.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--version", action="store_true",
        help="Print the version and exit.",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    bundle = sub.add_parser(
        "bundle",
        help="Produce a bundle from a repository (default).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    _build_bundle_parser(bundle)

    unb = sub.add_parser(
        "unbundle",
        help="Reverse a bundle back into a directory tree.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    _build_unbundle_parser(unb)

    init = sub.add_parser(
        "init-preamble",
        help="Create a starter preamble file.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    _build_init_preamble_parser(init)

    serve = sub.add_parser(
        "serve",
        help="Launch the Flask-based Web UI.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    _build_serve_parser(serve)

    return parser


def _normalize_argv(argv: Sequence[str]) -> List[str]:
    """Make the historical ``script <path> [-p ...] [-o ...]`` form work.

    When the first non-flag argument is not one of the registered subcommands,
    we inject ``bundle`` at the front so argparse routes correctly. This keeps
    every README example from the original repo working unchanged while still
    allowing the modern subcommand style.
    """
    if not argv:
        return ["bundle"]
    known = {"bundle", "unbundle", "init-preamble", "serve"}
    first = argv[0]
    if first in known or first in {"-h", "--help", "--version"}:
        return list(argv)
    return ["bundle", *argv]


def _emit(msg: str, quiet: bool, file=None) -> None:
    if quiet:
        return
    print(msg, file=file if file is not None else sys.stdout)


def _resolve_token_limit(args: argparse.Namespace) -> int:
    if args.token_limit:
        return args.token_limit
    if args.model:
        size = model_context_size(args.model)
        if size is not None:
            # Leave headroom for the user's prompt and the model's response.
            return max(1, int(size * 0.85))
    return 0


def _format_ignore_report(result) -> str:
    lines = ["Ignored files report:"]
    if not result.ignored:
        lines.append("  (no files ignored)")
        return "\n".join(lines)
    for entry in result.ignored:
        lines.append(f"  - {entry.rel_path}: {entry.reason}")
    return "\n".join(lines)


def _write_chunked(output_path: str, chunks: Sequence[str]) -> List[str]:
    base, ext = os.path.splitext(output_path)
    paths = []
    width = max(2, len(str(len(chunks))))
    for idx, chunk in enumerate(chunks, start=1):
        suffix = f".chunk{idx:0{width}d}{ext or '.txt'}"
        path = f"{base}{suffix}"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(chunk)
        paths.append(path)
    return paths


def _bundle_command(args: argparse.Namespace) -> int:
    token_limit = _resolve_token_limit(args)
    preamble_config = PreambleConfig(
        path=args.preamble,
        include_stats=args.preamble_stats,
    )
    opts_kwargs = {
        "preamble": preamble_config,
        "include_gitignore": args.include_gitignore,
        "include_gptignore": args.include_gptignore,
        "skip_binary": args.skip_binary,
        "follow_symlinks": args.follow_symlinks,
        "tracked_only": args.tracked_only,
        "encoding_mode": args.encoding_mode,
        "token_limit": token_limit,
        "chunk_when_over_limit": args.chunk_when_over_limit,
        "model": args.model,
        "use_git_check_ignore": args.use_git_check_ignore,
        "sort_paths": args.sort_paths,
        "extra_excludes": list(args.exclude or []),
        "extra_includes": list(args.include or []),
    }

    if is_url(args.repo):
        with open_remote(
            args.repo, depth=args.clone_depth, branch=args.branch
        ) as remote:
            opts = BundleOptions(repo_path=remote.path, **opts_kwargs)
            result = bundle_repository(opts)
            return _finalize_bundle(args, opts, result, token_limit)
    else:
        opts = BundleOptions(repo_path=args.repo, **opts_kwargs)
        result = bundle_repository(opts)
        return _finalize_bundle(args, opts, result, token_limit)


def _finalize_bundle(
    args: argparse.Namespace,
    opts: BundleOptions,
    result,
    token_limit: int,
) -> int:
    quiet = args.quiet
    if args.format == "json":
        payload = {
            "preamble": result.preamble,
            "files": [
                {
                    "path": entry.rel_path,
                    "encoding": entry.encoding,
                    "content": entry.content,
                    "tokens": entry.token_estimate,
                    "size_bytes": entry.size_bytes,
                }
                for entry in result.files
            ],
            "ignored": [
                {"path": entry.rel_path, "reason": entry.reason}
                for entry in result.ignored
            ],
            "token_count": result.token_count,
            "token_method": result.token_method,
            "warnings": result.warnings,
        }
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
    elif args.format == "zip":
        raw_files = []
        for entry in result.files:
            try:
                with open(entry.abs_path, "rb") as fh:
                    raw_files.append((entry.rel_path, fh.read()))
            except OSError:
                continue
        write_zip_bundle(args.output, text_bundle=result.text, files=raw_files)
    else:
        if args.chunk_when_over_limit and result.chunks:
            paths = _write_chunked(args.output, result.chunks)
            _emit(
                f"Bundle split into {len(paths)} chunks: " + ", ".join(paths),
                quiet,
            )
        else:
            with open(args.output, "w", encoding="utf-8") as fh:
                fh.write(result.text)

    if args.clipboard:
        try:
            backend = copy_to_clipboard(result.text)
            _emit(f"Bundle copied to clipboard via {backend}.", quiet)
        except ClipboardUnavailableError as exc:
            print(f"Clipboard unavailable: {exc}", file=sys.stderr)

    if args.show_ignored:
        _emit(_format_ignore_report(result), quiet)

    if args.json_report:
        report = {
            "repo_path": opts.repo_path,
            "files_included": [
                {"path": e.rel_path, "tokens": e.token_estimate,
                 "size_bytes": e.size_bytes, "encoding": e.encoding}
                for e in result.files
            ],
            "files_ignored": [
                {"path": e.rel_path, "reason": e.reason}
                for e in result.ignored
            ],
            "token_count": result.token_count,
            "token_method": result.token_method,
            "discovered_ignore_files": result.discovered_ignore_files,
            "warnings": result.warnings,
        }
        with open(args.json_report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        _emit(f"JSON report written to {args.json_report}.", quiet)

    for warning in result.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)

    if not args.quiet:
        _emit(
            f"Bundle written to {args.output} - "
            f"{result.included_count} files included, "
            f"{result.ignored_count} ignored, "
            f"{result.token_count} tokens ({result.token_method} backend).",
            quiet,
        )
    if token_limit and result.token_count > token_limit and not args.chunk_when_over_limit:
        # Non-zero exit so CI can fail noisily when a bundle would not fit a
        # prompt window; callers who want to ignore can pass --token-limit 0.
        return 2
    return 0


def _unbundle_command(args: argparse.Namespace) -> int:
    with open(args.bundle, encoding="utf-8") as fh:
        text = fh.read()
    written = unbundle(
        text,
        args.destination,
        overwrite=args.overwrite,
        include=args.only or None,
    )
    print(f"Wrote {len(written)} files to {os.path.abspath(args.destination)}.")
    return 0


def _init_preamble_command(args: argparse.Namespace) -> int:
    path = init_preamble_file(args.path, overwrite=args.force)
    print(f"Preamble template written to {path}.")
    return 0


def _serve_command(args: argparse.Namespace) -> int:
    try:
        from .web import create_app
    except RuntimeError as exc:
        print(f"Cannot start Web UI: {exc}", file=sys.stderr)
        return 1
    app = create_app()
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv and raw_argv[0] == "--version":
        from . import __version__
        print(f"gpt-repository-loader {__version__}")
        return 0
    normalized = _normalize_argv(raw_argv)
    args = parser.parse_args(normalized)
    if getattr(args, "version", False):
        from . import __version__
        print(f"gpt-repository-loader {__version__}")
        return 0
    command = args.command or "bundle"
    if command == "bundle":
        return _bundle_command(args)
    if command == "unbundle":
        return _unbundle_command(args)
    if command == "init-preamble":
        return _init_preamble_command(args)
    if command == "serve":
        return _serve_command(args)
    parser.error(f"Unknown command: {command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
