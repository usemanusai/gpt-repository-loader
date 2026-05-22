"""Tests for the high-level bundle pipeline."""

from __future__ import annotations

import os

from gpt_repository_loader.constants import (
    ENCODING_GZIP_BASE64,
    END_MARKER,
    FILE_DELIMITER,
)
from gpt_repository_loader.core import BundleOptions, bundle_repository, process_repository


def test_bundle_repository_basic(tiny_repo):
    result = bundle_repository(BundleOptions(repo_path=tiny_repo))
    included = {f.rel_path for f in result.files}
    ignored = {f.rel_path for f in result.ignored}
    assert "main.py" in included
    assert "pkg/util.py" in included
    assert "README.md" in included
    assert "docs/notes.txt" in ignored
    assert ".gptignore" in ignored
    assert "binary.png" in ignored
    # The bundle ends with --END--.
    assert result.text.strip().endswith(END_MARKER)


def test_bundle_includes_preamble_and_delimiters(tiny_repo):
    result = bundle_repository(BundleOptions(repo_path=tiny_repo))
    assert result.preamble  # default preamble present
    assert result.text.count(FILE_DELIMITER + "\n") >= len(result.files)


def test_token_count_recorded(tiny_repo):
    result = bundle_repository(BundleOptions(repo_path=tiny_repo, model="gpt-4"))
    assert result.token_count > 0
    assert result.token_method in {"tiktoken", "fallback"}


def test_warning_emitted_over_limit(tiny_repo):
    result = bundle_repository(
        BundleOptions(repo_path=tiny_repo, token_limit=10)
    )
    assert result.warnings
    assert any("exceed" in w.lower() for w in result.warnings)


def test_chunking_enabled(tiny_repo):
    result = bundle_repository(
        BundleOptions(repo_path=tiny_repo, token_limit=20, chunk_when_over_limit=True)
    )
    assert result.chunks
    assert all(END_MARKER in chunk for chunk in result.chunks)


def test_compression_applied(tiny_repo):
    result = bundle_repository(
        BundleOptions(repo_path=tiny_repo, encoding_mode=ENCODING_GZIP_BASE64)
    )
    assert any(f.encoding == ENCODING_GZIP_BASE64 for f in result.files)
    # Compressed bundles still contain the delimiter and preamble.
    assert FILE_DELIMITER in result.text


def test_extra_includes_overrides_ignore(tiny_repo):
    result = bundle_repository(
        BundleOptions(repo_path=tiny_repo, extra_includes=["docs/notes.txt"])
    )
    included_paths = {f.rel_path for f in result.files}
    assert "docs/notes.txt" in included_paths


def test_extra_excludes_added(tiny_repo):
    result = bundle_repository(
        BundleOptions(repo_path=tiny_repo, extra_excludes=["main.py"])
    )
    paths = {f.rel_path for f in result.files}
    assert "main.py" not in paths


def test_skip_binary_default_true(tiny_repo):
    result = bundle_repository(BundleOptions(repo_path=tiny_repo))
    paths = {f.rel_path for f in result.files}
    assert "binary.png" not in paths


def test_include_binary_when_disabled(tiny_repo):
    # Disable both ignore-file detection of *.png and binary skip.
    result = bundle_repository(
        BundleOptions(
            repo_path=tiny_repo,
            skip_binary=False,
            extra_includes=["binary.png"],
        )
    )
    paths = {f.rel_path for f in result.files}
    assert "binary.png" in paths


def test_legacy_process_repository_signature_preserved(tiny_repo, temp_dir):
    """Smoke test the historical ``process_repository`` API."""
    output_path = os.path.join(temp_dir, "out.txt")
    with open(output_path, "w", encoding="utf-8") as fh:
        process_repository(tiny_repo, ["*.txt", ".gptignore", "*.png"], fh)
    with open(output_path, encoding="utf-8") as fh:
        text = fh.read()
    assert "main.py" in text
    assert "notes.txt" not in text


def test_content_with_delimiter_upgrades_to_base64(temp_dir):
    """Files containing literal ``----`` lines must be auto-upgraded to base64
    so that the bundle remains parseable and reversible."""
    from gpt_repository_loader.constants import ENCODING_BASE64
    from gpt_repository_loader.reverse import unbundle

    repo = os.path.join(temp_dir, "repo")
    os.makedirs(repo)
    danger = "intro\n----\nfake-path.py\n# pretend\n--END--\nepilogue\n"
    with open(os.path.join(repo, "doc.md"), "w", encoding="utf-8") as fh:
        fh.write(danger)
    with open(os.path.join(repo, "main.py"), "w", encoding="utf-8") as fh:
        fh.write("print('ok')\n")
    result = bundle_repository(BundleOptions(repo_path=repo))
    doc_entry = next(f for f in result.files if f.rel_path == "doc.md")
    assert doc_entry.encoding == ENCODING_BASE64
    # The bundle must still round-trip exactly.
    dest = os.path.join(temp_dir, "restored")
    unbundle(result.text, dest)
    with open(os.path.join(dest, "doc.md"), encoding="utf-8") as fh:
        assert fh.read() == danger
    with open(os.path.join(dest, "main.py"), encoding="utf-8") as fh:
        assert fh.read() == "print('ok')\n"
