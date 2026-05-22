"""Tests for the gitignore-style pattern engine (:mod:`gpt_repository_loader.ignore`)."""

from __future__ import annotations

import os

from gpt_repository_loader.ignore import (
    IgnoreMatcher,
    compile_patterns,
    load_ignore_files,
    should_ignore,
)


def _match(patterns, path, is_dir=False):
    matcher = IgnoreMatcher(compile_patterns(patterns, source="test"))
    return matcher.is_ignored(path, is_dir=is_dir)


def test_single_star_does_not_cross_separator():
    assert _match(["*.py"], "foo.py")
    assert _match(["*.py"], "sub/foo.py")
    assert not _match(["*.py"], "foo.pyx")


def test_double_star_matches_multiple_segments():
    assert _match(["**/test_*.py"], "foo/test_a.py")
    assert _match(["**/test_*.py"], "a/b/c/test_a.py")
    assert _match(["docs/**/*.md"], "docs/x/y/z.md")


def test_negation_pattern_unignores():
    # Ignore everything in build/, but keep build/keep.txt.
    matcher = IgnoreMatcher(
        compile_patterns(["build/", "!build/keep.txt"], source="test")
    )
    assert matcher.is_ignored("build/foo.bin", is_dir=False)
    decision = matcher.decide("build/keep.txt", is_dir=False)
    assert decision.ignored is False
    assert decision.pattern is not None
    assert decision.pattern.negate


def test_leading_slash_anchors_to_base_dir():
    matcher = IgnoreMatcher(compile_patterns(["/root_only.txt"], source="test"))
    assert matcher.is_ignored("root_only.txt")
    assert not matcher.is_ignored("sub/root_only.txt")


def test_trailing_slash_means_directory_only():
    matcher = IgnoreMatcher(compile_patterns(["logs/"], source="test"))
    assert matcher.is_ignored("logs", is_dir=True)
    # Files named logs (not directories) should not match.
    assert not matcher.is_ignored("logs", is_dir=False)
    # Nested files under a logs dir do match when scanned as files.
    assert matcher.is_ignored("logs/app.log", is_dir=False)


def test_character_classes_supported():
    assert _match(["file[0-9].txt"], "file3.txt")
    assert not _match(["file[0-9].txt"], "fileA.txt")
    assert _match(["file[!0-9].txt"], "fileA.txt")


def test_comments_and_blank_lines_skipped():
    patterns = compile_patterns(
        ["", "  ", "# comment", "*.py"], source="t"
    )
    assert len(patterns) == 1


def test_escaped_hash_is_literal():
    assert _match(["\\#hashfile"], "#hashfile")


def test_load_ignore_files_walks_subdirs(tiny_repo):
    matcher, discovered = load_ignore_files(
        tiny_repo, package_fallback=None, include_gitignore=True
    )
    # Two files (top-level .gptignore + the package-level walks the same path).
    assert any(p.endswith(".gptignore") for p in discovered)
    assert matcher.is_ignored("docs/notes.txt")
    assert not matcher.is_ignored("main.py")


def test_extra_excludes_take_precedence(tiny_repo):
    matcher, _ = load_ignore_files(
        tiny_repo, extra_patterns=["main.py"], package_fallback=None
    )
    assert matcher.is_ignored("main.py")


def test_decide_returns_reason(tiny_repo):
    matcher, _ = load_ignore_files(tiny_repo, package_fallback=None)
    decision = matcher.decide("docs/notes.txt")
    assert decision.ignored is True
    assert "*.txt" in decision.reason
    assert decision.pattern is not None


def test_legacy_should_ignore_function_back_compat():
    assert should_ignore("foo.txt", ["*.txt"])
    assert not should_ignore("foo.py", ["*.txt"])


def test_subdir_gitignore_anchored_to_its_directory(temp_dir):
    repo = os.path.join(temp_dir, "anchor")
    os.makedirs(os.path.join(repo, "subA"), exist_ok=True)
    os.makedirs(os.path.join(repo, "subB"), exist_ok=True)
    with open(os.path.join(repo, "subA", ".gitignore"), "w", encoding="utf-8") as fh:
        fh.write("local_only.txt\n")
    matcher, _ = load_ignore_files(repo, package_fallback=None)
    # Pattern from subA/.gitignore should ignore subA/local_only.txt but not subB/local_only.txt.
    assert matcher.is_ignored("subA/local_only.txt")
    assert not matcher.is_ignored("subB/local_only.txt")


def test_negation_overrides_earlier_match():
    patterns = ["*.log", "!keep.log"]
    matcher = IgnoreMatcher(compile_patterns(patterns, source="t"))
    assert matcher.is_ignored("foo.log")
    assert not matcher.is_ignored("keep.log")


def test_legacy_should_ignore_directory_pattern():
    # The historical script used fnmatch which treats trailing slashes as
    # literal. Our shim returns True when either the new engine or fnmatch
    # matches, so callers cannot regress.
    assert should_ignore(".github/workflow.yml", [".github/*"])
