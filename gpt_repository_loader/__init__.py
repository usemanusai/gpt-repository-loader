"""``gpt-repository-loader`` - turn a Git repository into an LLM-friendly bundle.

This package exposes the same public API the original single-file script did
(``process_repository`` and ``get_ignore_list``) plus a richer, fully
documented surface used by the modern CLI and the Web UI:

>>> from gpt_repository_loader import bundle_repository, BundleOptions
>>> result = bundle_repository(BundleOptions(repo_path="."))
>>> print(result.text)

Importing the package is intentionally cheap - heavyweight subsystems
(``tiktoken``, ``GitPython``, ``Flask``) are loaded on demand.
"""

from __future__ import annotations

from .binary import BinaryDetection, detect_binary, is_binary
from .chunking import Chunk, FileSection, chunk_sections, split_oversized_section
from .clipboard import (
    ClipboardUnavailableError,
    clipboard_available,
    copy_to_clipboard,
)
from .compression import (
    EncodedContent,
    decode_content,
    encode_content,
    read_zip_bundle,
    write_zip_bundle,
)
from .constants import (
    DEFAULT_OUTPUT_FILENAME,
    DEFAULT_PREAMBLE,
    DEFAULT_TOKEN_LIMIT,
    ENCODING_BASE64,
    ENCODING_GZIP_BASE64,
    ENCODING_PREFIX,
    ENCODING_RAW,
    END_MARKER,
    FILE_DELIMITER,
    MODEL_CONTEXT_SIZES,
)
from .core import (
    BundleOptions,
    BundleResult,
    FileEntry,
    bundle_repository,
    process_repository,
    stream_repository,
)
from .git_integration import (
    HAS_GITPYTHON,
    RepoInfo,
    clone_repo,
    describe_repo,
    diff_files,
    git_check_ignore,
    tracked_files,
)
from .ignore import (
    IgnoreDecision,
    IgnoreMatcher,
    IgnorePattern,
    compile_patterns,
    get_ignore_list,
    load_ignore_files,
    package_fallback_gptignore,
    should_ignore,
)
from .preamble import (
    PreambleConfig,
    generate_default_preamble,
    init_preamble_file,
    load_preamble,
    resolve_preamble,
)
from .reverse import ReverseFile, ReverseResult, parse_bundle, unbundle
from .tokens import TokenCount, TokenCounter, model_context_size, warn_if_over_limit
from .url_loader import RemoteRepository, is_url, open_remote

__version__ = "1.0.0"

__all__ = [
    "__version__",
    # Legacy public API
    "process_repository",
    "get_ignore_list",
    "should_ignore",
    # Modern bundle API
    "BundleOptions",
    "BundleResult",
    "FileEntry",
    "bundle_repository",
    "stream_repository",
    # Ignore engine
    "IgnoreDecision",
    "IgnoreMatcher",
    "IgnorePattern",
    "compile_patterns",
    "load_ignore_files",
    "package_fallback_gptignore",
    # Binary detection
    "BinaryDetection",
    "detect_binary",
    "is_binary",
    # Tokens
    "TokenCount",
    "TokenCounter",
    "model_context_size",
    "warn_if_over_limit",
    # Chunking
    "Chunk",
    "FileSection",
    "chunk_sections",
    "split_oversized_section",
    # Compression
    "EncodedContent",
    "encode_content",
    "decode_content",
    "read_zip_bundle",
    "write_zip_bundle",
    # Reverse
    "ReverseFile",
    "ReverseResult",
    "parse_bundle",
    "unbundle",
    # Preamble
    "PreambleConfig",
    "load_preamble",
    "generate_default_preamble",
    "init_preamble_file",
    "resolve_preamble",
    # Clipboard
    "ClipboardUnavailableError",
    "clipboard_available",
    "copy_to_clipboard",
    # Remote/URL
    "RemoteRepository",
    "is_url",
    "open_remote",
    # Git integration
    "HAS_GITPYTHON",
    "RepoInfo",
    "describe_repo",
    "diff_files",
    "git_check_ignore",
    "tracked_files",
    "clone_repo",
    # Constants
    "DEFAULT_OUTPUT_FILENAME",
    "DEFAULT_PREAMBLE",
    "DEFAULT_TOKEN_LIMIT",
    "END_MARKER",
    "FILE_DELIMITER",
    "ENCODING_PREFIX",
    "ENCODING_RAW",
    "ENCODING_BASE64",
    "ENCODING_GZIP_BASE64",
    "MODEL_CONTEXT_SIZES",
]
