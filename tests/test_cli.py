"""Tests for the CLI surface."""

from __future__ import annotations

import json
import os

from gpt_repository_loader import __version__
from gpt_repository_loader.cli import main


def test_cli_version(capsys):
    rc = main(["--version"])
    out = capsys.readouterr().out
    assert rc == 0
    assert __version__ in out


def test_cli_bundle_default_subcommand(tiny_repo, temp_dir, capsys):
    output = os.path.join(temp_dir, "bundle.txt")
    rc = main([tiny_repo, "-o", output])
    assert rc == 0
    with open(output, encoding="utf-8") as fh:
        text = fh.read()
    assert "main.py" in text
    assert "--END--" in text


def test_cli_bundle_explicit_subcommand(tiny_repo, temp_dir):
    output = os.path.join(temp_dir, "bundle.txt")
    rc = main(["bundle", tiny_repo, "-o", output])
    assert rc == 0
    assert os.path.exists(output)


def test_cli_json_format(tiny_repo, temp_dir):
    output = os.path.join(temp_dir, "bundle.json")
    rc = main(["bundle", tiny_repo, "-o", output, "--format", "json", "--quiet"])
    assert rc == 0
    with open(output, encoding="utf-8") as fh:
        data = json.load(fh)
    assert "files" in data and "ignored" in data
    assert any(f["path"] == "main.py" for f in data["files"])


def test_cli_zip_format(tiny_repo, temp_dir):
    output = os.path.join(temp_dir, "bundle.zip")
    rc = main(["bundle", tiny_repo, "-o", output, "--format", "zip", "--quiet"])
    assert rc == 0
    assert os.path.exists(output)


def test_cli_show_ignored(tiny_repo, temp_dir, capsys):
    output = os.path.join(temp_dir, "bundle.txt")
    assert main(["bundle", tiny_repo, "-o", output, "--show-ignored"]) == 0
    out = capsys.readouterr().out
    assert "Ignored files report" in out
    assert "notes.txt" in out


def test_cli_chunk_with_token_limit(tiny_repo, temp_dir):
    output = os.path.join(temp_dir, "bundle.txt")
    rc = main([
        "bundle", tiny_repo, "-o", output,
        "--token-limit", "20", "--chunk", "--quiet",
    ])
    assert rc == 0
    chunks = [f for f in os.listdir(temp_dir) if "chunk" in f]
    assert chunks


def test_cli_init_preamble(temp_dir):
    path = os.path.join(temp_dir, ".gpt-preamble")
    rc = main(["init-preamble", path])
    assert rc == 0
    assert os.path.exists(path)


def test_cli_unbundle(tiny_repo, temp_dir):
    bundle_path = os.path.join(temp_dir, "bundle.txt")
    dest = os.path.join(temp_dir, "restored")
    assert main(["bundle", tiny_repo, "-o", bundle_path, "--quiet"]) == 0
    rc = main(["unbundle", bundle_path, "-d", dest])
    assert rc == 0
    assert os.path.exists(os.path.join(dest, "main.py"))


def test_cli_token_limit_exit_code(tiny_repo, temp_dir):
    output = os.path.join(temp_dir, "bundle.txt")
    rc = main(["bundle", tiny_repo, "-o", output, "--token-limit", "1", "--quiet"])
    # No --chunk - exit code 2 is the documented "over limit" signal.
    assert rc == 2


def test_cli_exclude_pattern(tiny_repo, temp_dir):
    output = os.path.join(temp_dir, "bundle.txt")
    rc = main([
        "bundle", tiny_repo, "-o", output, "-e", "main.py", "--quiet"
    ])
    assert rc == 0
    with open(output, encoding="utf-8") as fh:
        text = fh.read()
    assert "print('hi')" not in text


def test_cli_include_pattern_overrides_ignore(tiny_repo, temp_dir):
    output = os.path.join(temp_dir, "bundle.txt")
    rc = main([
        "bundle", tiny_repo, "-o", output, "-i", "docs/notes.txt", "--quiet"
    ])
    assert rc == 0
    with open(output, encoding="utf-8") as fh:
        text = fh.read()
    assert "ignored notes" in text


def test_cli_json_report(tiny_repo, temp_dir):
    output = os.path.join(temp_dir, "bundle.txt")
    report = os.path.join(temp_dir, "report.json")
    rc = main(["bundle", tiny_repo, "-o", output, "--json-report", report, "--quiet"])
    assert rc == 0
    with open(report, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["files_included"]
    assert "discovered_ignore_files" in data
