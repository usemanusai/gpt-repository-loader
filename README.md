# gpt-repository-loader

`gpt-repository-loader` packs a Git repository (or remote URL, or uploaded
archive) into a single LLM-friendly text bundle: a preamble describing the
format, a `----`-delimited list of file sections, and an `--END--` marker that
tells the model where the source code stops and your instructions begin.

It is a tiny tool with a deliberately rigid format, designed for pasting source
code into chat-based language models, building retrieval-augmented contexts,
or producing diffs / refactor patches that can be applied back to disk via the
reverse `unbundle` command.

## Highlights

| Feature | Flag(s) | Notes |
| --- | --- | --- |
| Bundle a local repository | `gpt-repository-loader <path>` | Default subcommand is `bundle`. |
| Bundle a remote repository | `gpt-repository-loader <url>` | Supports GitHub/GitLab/Bitbucket/Codeberg URLs, `git@host:org/repo.git`, `file://`, and downloadable `.zip`/`.tar.*` archives. |
| Reverse bundle into files | `gpt-repository-loader unbundle <bundle>` | Recreates a directory tree from a bundle, with directory-traversal protection. |
| Generate a starter preamble | `gpt-repository-loader init-preamble` | Drops a customisable `.gpt-preamble` template. |
| Launch the Web UI | `gpt-repository-loader serve` | Flask single-page app exposing the same options as the CLI plus a JSON REST API. |
| Compress per-file contents | `--compress` / `--encoding-mode gzip+base64` | Bundle still parseable by the reverse loader. |
| Output as ZIP or JSON | `--format zip` / `--format json` | ZIP contains both `bundle.txt` and raw files. |
| Token counting & warnings | `--model gpt-4o --token-limit 100000` | Uses tiktoken when installed; falls back to a deterministic estimator otherwise. |
| Split oversized bundles | `--chunk` | Writes `<output>.chunk01.txt`, `<output>.chunk02.txt`, ... |
| Copy bundle to clipboard | `--clipboard` | pyperclip, pbcopy, xclip, xsel, wl-copy or `clip.exe`, automatic. |
| Skip binary files | default (`--include-binary` to override) | Magic-byte + heuristic detection (PNG, JPEG, ELF, gzip, zip, ...). |
| Honour `.gitignore` and `.gptignore` | default (`--no-gitignore` / `--no-gptignore`) | Full gitignore semantics: negation patterns, anchored slashes, `**`, character classes. |
| Honour `git check-ignore` | `--use-git-check-ignore` | Catches global excludes / `info/exclude` that the in-tree matcher cannot see. |
| Tracked-only mode | `--tracked-only` | Uses `git ls-files` to limit the walk. |
| Explain which files were ignored | `--show-ignored` and `--json-report` | Prints / writes a structured report with the matching pattern, source file and reason. |

## Install

The project ships as a regular Python package with a `pyproject.toml`. The
recommended way to install it as a globally-available CLI is
[pipx](https://pipx.pypa.io):

```bash
pipx install gpt-repository-loader
gpt-repository-loader --help
```

Plain `pip` works too:

```bash
python -m pip install gpt-repository-loader
```

Optional extras unlock the heavier features:

```bash
# Accurate token counting:
pip install "gpt-repository-loader[tokens]"

# Remote git URLs / GitPython integration:
pip install "gpt-repository-loader[git]"

# Cross-platform clipboard support:
pip install "gpt-repository-loader[clipboard]"

# Flask Web UI:
pip install "gpt-repository-loader[web]"

# Everything at once (recommended for local development):
pip install "gpt-repository-loader[all,dev]"
```

The development dependencies (pytest, ruff, build) install with `[dev]`.

## Quickstart

```bash
# Bundle the current directory and write to ./output.txt
gpt-repository-loader .

# Bundle a remote repository, copy the result straight to the clipboard
gpt-repository-loader https://github.com/mpoon/gpt-repository-loader --clipboard

# Aim for a specific model. Tokens are counted with tiktoken when installed.
gpt-repository-loader . --model gpt-4o --show-ignored

# Skip large or non-relevant files explicitly.
gpt-repository-loader . -e "docs/legacy/**" -e "*.proto"

# Force-include something the default ignore set excludes:
gpt-repository-loader . -i "docs/important.md"

# Reverse a bundle back into a directory tree.
gpt-repository-loader unbundle output.txt -d ./restored
```

### Module / programmatic usage

```python
from gpt_repository_loader import BundleOptions, bundle_repository

result = bundle_repository(BundleOptions(repo_path=".", model="gpt-4o"))
print(result.text)
print("Tokens:", result.token_count, "via", result.token_method)
for entry in result.ignored:
    print("Skipped", entry.rel_path, "-", entry.reason)
```

The legacy two-function API (`process_repository`, `get_ignore_list`) is still
importable from `gpt_repository_loader` and behaves exactly like the original
script, so existing automation keeps working unchanged.

## Bundle format

A bundle is plain UTF-8 text:

```
The following text is a Git repository with code. ...
----
README.md
# Tiny Repo
----
src/main.py
print('hi')
--END--
```

- `----` introduces each file section.
- The next line is the file path, relative to the repository root, with
  forward slashes.
- An optional `# encoding: gzip+base64` (or `base64`) line on the third line
  tells the reverse loader how to decode the body.
- File contents follow verbatim until the next `----` line or the `--END--`
  terminator.
- Anything after `--END--` is left untouched and is meant to be interpreted as
  instructions for the model.

The `unbundle` subcommand parses this format and writes every section back to
disk, while refusing absolute paths or `..` traversal.

## Ignore semantics

Both `.gitignore` and `.gptignore` are honoured by default. The matcher
implements the full gitignore specification:

- Negation patterns: `!important.txt` re-includes a previously excluded file.
- Leading slash anchors a pattern to the directory containing the ignore file.
- Trailing slash restricts a pattern to directories - all contents of a matched
  directory are excluded automatically.
- `**` matches any number of path segments; `*` matches inside a single
  segment.
- Character classes (`[abc]`, `[!abc]`, `[0-9]`).
- Comments (`#`) and escaping (`\#`, `\!`).

The package ships a comprehensive default `.gptignore` covering build outputs,
common dot-directories, secrets, binaries, archives, images and editor noise.
A repository's own ignore files override the defaults, and `--exclude` /
`--include` patterns on the command line override everything.

## Web UI

Launch the Flask single-page app:

```bash
gpt-repository-loader serve --host 127.0.0.1 --port 5050
```

The UI lets you:

- Point at a local repository path, a remote URL, or upload a `.zip` / `.tar.*`.
- Tweak ignore patterns, force-includes, encoding, model, token limit and
  chunking interactively.
- Download the bundle as plain text, ZIP or JSON.
- Inspect a table of ignored files, the list of discovered ignore files, and
  any warnings emitted by the pipeline.

The same functionality is exposed through a small JSON API at:

- `POST /api/bundle` - returns a JSON payload with the bundle text, file list
  and ignored-file report.
- `POST /api/bundle/download` - returns a downloadable artifact (`text`,
  `zip`, or `json`).
- `POST /api/unbundle` - extracts a bundle into a target directory.
- `GET /api/health` - liveness probe returning the package version.

## Devcontainer

A turnkey VS Code devcontainer lives in `.devcontainer/devcontainer.json`. It
boots a Debian image with Python 3.12, installs the package in editable mode
with all extras, and forwards port 5050 for the Web UI.

## Running tests

```bash
python -m pytest                                  # full suite
python -m unittest test_gpt_repository_loader.py  # legacy harness
```

Both suites run in CI on Ubuntu, macOS and Windows under Python 3.8 - 3.12.

## Acknowledgements

This project began life as a single-file script that mostly built itself via
ChatGPT-4. It has since grown a real test suite, gitignore engine, token
budgeter and Web UI - but the goal is unchanged: feed code to language models
without losing your mind to copy-paste.

## License

MIT. See [LICENSE](LICENSE).
