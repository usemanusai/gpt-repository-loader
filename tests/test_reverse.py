"""Tests for the reverse loader."""

from __future__ import annotations

import os

import pytest

from gpt_repository_loader.constants import ENCODING_GZIP_BASE64
from gpt_repository_loader.core import BundleOptions, bundle_repository
from gpt_repository_loader.reverse import parse_bundle, unbundle


def test_unbundle_round_trips_a_repo(tiny_repo, temp_dir):
    bundle = bundle_repository(BundleOptions(repo_path=tiny_repo))
    dest = os.path.join(temp_dir, "restored")
    written = unbundle(bundle.text, dest)
    rel_written = sorted(os.path.relpath(p, dest) for p in written)
    expected = sorted(f.rel_path for f in bundle.files)
    assert [p.replace(os.sep, "/") for p in rel_written] == expected
    for entry in bundle.files:
        with open(os.path.join(dest, entry.rel_path), encoding="utf-8") as fh:
            assert fh.read() == entry.content


def test_unbundle_handles_compressed_files(tiny_repo, temp_dir):
    bundle = bundle_repository(
        BundleOptions(repo_path=tiny_repo, encoding_mode=ENCODING_GZIP_BASE64)
    )
    dest = os.path.join(temp_dir, "restored")
    unbundle(bundle.text, dest)
    with open(os.path.join(dest, "main.py"), encoding="utf-8") as fh:
        assert fh.read().strip() == "print('hi')"


def test_unbundle_rejects_directory_traversal(temp_dir):
    bundle = (
        "preamble\n"
        "----\n"
        "../escape.txt\n"
        "evil\n"
        "--END--"
    )
    with pytest.raises(ValueError):
        unbundle(bundle, os.path.join(temp_dir, "dst"))


def test_unbundle_refuses_to_overwrite_by_default(tiny_repo, temp_dir):
    bundle = bundle_repository(BundleOptions(repo_path=tiny_repo))
    dest = os.path.join(temp_dir, "restored")
    unbundle(bundle.text, dest)
    with pytest.raises(FileExistsError):
        unbundle(bundle.text, dest)


def test_unbundle_overwrite_flag(tiny_repo, temp_dir):
    bundle = bundle_repository(BundleOptions(repo_path=tiny_repo))
    dest = os.path.join(temp_dir, "restored")
    unbundle(bundle.text, dest)
    # Second call with overwrite should not raise.
    unbundle(bundle.text, dest, overwrite=True)


def test_parse_bundle_with_no_files():
    result = parse_bundle("Just a preamble.\n--END--")
    assert result.files == []
    assert "preamble" in result.preamble.lower()


def test_unbundle_part_suffix_concatenates_into_single_file(temp_dir):
    bundle = (
        "preamble\n"
        "----\n"
        "big.py (part 1 of 2)\n"
        "first half\n"
        "----\n"
        "big.py (part 2 of 2)\n"
        "second half\n"
        "--END--"
    )
    dest = os.path.join(temp_dir, "out")
    unbundle(bundle, dest)
    with open(os.path.join(dest, "big.py"), encoding="utf-8") as fh:
        text = fh.read()
    assert "first half" in text
    assert "second half" in text


def test_unbundle_only_filter(temp_dir):
    bundle = (
        "preamble\n"
        "----\n"
        "a.py\n"
        "AAA\n"
        "----\n"
        "b.py\n"
        "BBB\n"
        "--END--"
    )
    dest = os.path.join(temp_dir, "out")
    written = unbundle(bundle, dest, include=["a.py"])
    assert len(written) == 1
    assert os.path.basename(written[0]) == "a.py"
    assert not os.path.exists(os.path.join(dest, "b.py"))
