"""Constants and shared protocol values for the bundle format.

The bundle format used by ``gpt-repository-loader`` is intentionally simple so that
it can be reliably emitted by language models and reliably re-parsed when the bundle
is fed back into :mod:`gpt_repository_loader.reverse`. The format consists of:

* An optional preamble (any free-form text) that ends with the marker emitted by the
  preamble module.
* A sequence of file sections. Each file section starts with a delimiter line
  composed of four dashes (``----``) followed by a newline, then a single line
  containing the file path relative to the repository root, then the verbatim file
  contents, terminated by a newline before the next delimiter.
* A terminating end marker line ``--END--``.

For compressed file contents an additional metadata line is emitted directly after
the path line, of the form ``# encoding: <codec>``, where ``<codec>`` is one of
``raw`` (default and assumed when the line is absent), ``gzip+base64`` or
``base64``. The reverse loader decodes these on the way back.
"""

from __future__ import annotations

#: Standard preamble emitted when ``--preamble`` is not provided. The exact wording
#: is part of the historical contract of the tool and is intentionally preserved.
DEFAULT_PREAMBLE = (
    "The following text is a Git repository with code. The structure of the text "
    "are sections that begin with ----, followed by a single line containing the "
    "file path and file name, followed by a variable amount of lines containing "
    "the file contents. The text representing the Git repository ends when the "
    "symbols --END-- are encountered. Any further text beyond --END-- are meant "
    "to be interpreted as instructions using the aforementioned Git repository "
    "as context."
)

#: Delimiter that separates file sections in the bundle output.
FILE_DELIMITER = "----"

#: Marker that ends the repository portion of the bundle.
END_MARKER = "--END--"

#: Encoding metadata line prefix.
ENCODING_PREFIX = "# encoding:"

#: Supported per-file content encodings written as metadata.
ENCODING_RAW = "raw"
ENCODING_BASE64 = "base64"
ENCODING_GZIP_BASE64 = "gzip+base64"

#: Default output filename used when the user does not specify one.
DEFAULT_OUTPUT_FILENAME = "output.txt"

#: Maximum number of bytes scanned from a file by the binary detector. Reading
#: more is wasteful and reading less misses interleaved NUL bytes in long files.
BINARY_SNIFF_BYTES = 8192

#: Heuristic share of NUL bytes above which a file is considered binary.
BINARY_NUL_RATIO = 0.0  # any NUL byte is enough to flag a file as binary

#: Heuristic share of non-text bytes above which a file is considered binary.
BINARY_NONTEXT_RATIO = 0.30

#: Default token limit applied when ``--token-limit`` is not provided. Picked to
#: match the GPT-4 8k context with headroom for prompt + response.
DEFAULT_TOKEN_LIMIT = 8000

#: Model-name -> token-context-size mapping used when the user passes ``--model``.
MODEL_CONTEXT_SIZES = {
    "gpt-3.5-turbo": 16385,
    "gpt-3.5-turbo-16k": 16385,
    "gpt-4": 8192,
    "gpt-4-32k": 32768,
    "gpt-4-turbo": 128000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4.1": 1_047_576,
    "o1": 200000,
    "o1-mini": 128000,
    "o3": 200000,
    "o3-mini": 200000,
    "o4-mini": 200000,
    "claude-3-5-sonnet": 200000,
    "claude-3-opus": 200000,
    "claude-3-sonnet": 200000,
    "claude-3-haiku": 200000,
    "claude-opus-4": 200000,
    "claude-sonnet-4": 200000,
    "gemini-1.5-pro": 2_097_152,
    "gemini-1.5-flash": 1_048_576,
}

__all__ = [
    "DEFAULT_PREAMBLE",
    "FILE_DELIMITER",
    "END_MARKER",
    "ENCODING_PREFIX",
    "ENCODING_RAW",
    "ENCODING_BASE64",
    "ENCODING_GZIP_BASE64",
    "DEFAULT_OUTPUT_FILENAME",
    "BINARY_SNIFF_BYTES",
    "BINARY_NUL_RATIO",
    "BINARY_NONTEXT_RATIO",
    "DEFAULT_TOKEN_LIMIT",
    "MODEL_CONTEXT_SIZES",
]
