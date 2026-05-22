"""Binary file detection.

The default behaviour of ``gpt-repository-loader`` is to skip binary files when
producing a bundle (issue #53 and a frequent ask in #56). Detection is performed
without external dependencies using a layered approach:

1. **Extension allow/deny lists.** Common archive, executable, image, audio,
   video, document and font extensions are flagged eagerly because reading
   bytes from gigabyte-scale binaries on a slow disk would otherwise dominate
   wall-clock time of a bundle run.

2. **Magic-byte sniffing.** Even when the extension is unfamiliar we read up to
   :data:`gpt_repository_loader.constants.BINARY_SNIFF_BYTES` bytes and match
   against well-known file signatures (PNG, JPEG, GIF, PDF, ZIP, gzip, ELF,
   Mach-O, PE, OGG, RIFF, WebP, SQLite, class files, etc.).

3. **Heuristic NUL/control-character ratio.** The classic ``git`` heuristic:
   any NUL byte, or more than 30% of bytes outside the printable ASCII range
   without being valid UTF-8 continuation bytes, marks the file as binary.

All three layers can be turned off individually via the public function
parameters which keeps the detector fully testable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Iterable, Optional

from .constants import BINARY_NONTEXT_RATIO, BINARY_SNIFF_BYTES

#: Extensions that are always considered binary regardless of magic bytes.
BINARY_EXTENSIONS = frozenset(
    {
        # Archives
        "zip", "tar", "tgz", "gz", "bz2", "xz", "7z", "rar", "ar", "lz", "lzma",
        "zst", "zstd", "cab", "iso", "dmg", "img", "deb", "rpm",
        # Executables / object files
        "exe", "dll", "so", "dylib", "a", "o", "obj", "lib", "ko", "elf", "out",
        # Bytecode / archives of bytecode
        "pyc", "pyo", "pyd", "class", "jar", "war", "ear",
        # Images
        "png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "ico", "webp", "psd",
        "ai", "eps", "heic", "heif", "avif",
        # Audio
        "mp3", "wav", "flac", "ogg", "m4a", "aac", "wma", "opus", "aiff",
        # Video
        "mp4", "mov", "avi", "mkv", "webm", "wmv", "flv", "m4v", "mpeg", "mpg",
        # Documents that aren't plain text
        "pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "odt", "ods", "odp",
        "rtf",
        # Fonts
        "ttf", "otf", "woff", "woff2", "eot",
        # Databases / serialised binary data
        "sqlite", "db", "mdb", "accdb", "dat",
        # Disk / firmware
        "bin", "rom", "fw",
        # Java / Android specific
        "apk", "aar", "aab",
        # Misc
        "msi", "swf", "fla",
    }
)

#: Extensions that are always considered text (forcing binary detection off).
TEXT_EXTENSIONS = frozenset(
    {
        "txt", "md", "rst", "py", "pyi", "js", "jsx", "ts", "tsx", "mjs", "cjs",
        "json", "json5", "jsonc", "yml", "yaml", "toml", "ini", "cfg", "conf",
        "html", "htm", "css", "scss", "sass", "less", "vue", "svelte",
        "c", "h", "cc", "cpp", "cxx", "hpp", "hh", "hxx", "m", "mm",
        "go", "rs", "java", "kt", "kts", "swift", "rb", "php", "pl", "pm",
        "scala", "sc", "sh", "bash", "zsh", "fish", "ps1", "bat", "cmd",
        "csv", "tsv", "log", "sql", "graphql", "gql", "proto", "thrift",
        "dockerfile", "makefile", "mk", "cmake", "gradle", "groovy",
        "tex", "bib", "r", "lua", "vim", "el", "lisp", "clj", "cljs",
        "ex", "exs", "erl", "hrl", "f", "f90", "f95", "for", "f03", "f08",
        "dart", "nim", "zig", "v", "vh", "vhd", "vhdl", "verilog", "sv",
        "asm", "s", "S", "ld", "x", "patch", "diff", "rej",
        "xml", "xsd", "xsl", "xslt", "rss", "atom", "svg",
    }
)


#: Magic byte signatures keyed by their byte sequence.
MAGIC_SIGNATURES: Dict[bytes, str] = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
    b"BM": "image/bmp",
    b"RIFF": "container/riff",
    b"%PDF-": "application/pdf",
    b"PK\x03\x04": "application/zip",
    b"PK\x05\x06": "application/zip",
    b"PK\x07\x08": "application/zip",
    b"\x1f\x8b": "application/gzip",
    b"BZh": "application/bzip2",
    b"\xfd7zXZ\x00": "application/xz",
    b"7z\xbc\xaf\x27\x1c": "application/x-7z",
    b"Rar!\x1a\x07\x00": "application/x-rar",
    b"\x7fELF": "application/x-elf",
    b"\xfe\xed\xfa\xce": "application/x-macho",
    b"\xfe\xed\xfa\xcf": "application/x-macho",
    b"\xcf\xfa\xed\xfe": "application/x-macho",
    b"\xca\xfe\xba\xbe": "application/java-vm",
    b"MZ": "application/x-msdownload",
    b"SQLite format 3\x00": "application/x-sqlite3",
    b"OggS": "application/ogg",
    b"ID3": "audio/mpeg",
    b"\xff\xfb": "audio/mpeg",
    b"\xff\xf3": "audio/mpeg",
    b"\x00\x00\x01\xba": "video/mpeg",
    b"\x00\x00\x00\x18ftypmp4": "video/mp4",
    b"\x00\x00\x00\x20ftypisom": "video/mp4",
    b"\x1aE\xdf\xa3": "video/x-matroska",
    b"wOFF": "font/woff",
    b"wOF2": "font/woff2",
    b"OTTO": "font/otf",
    b"\x00\x01\x00\x00\x00": "font/ttf",
    b"true\x00\x00\x00": "font/ttf",
    b"\xca\xfe\xd0\x0d": "application/java-class",
}


@dataclass
class BinaryDetection:
    """Result of running the binary detector against a file."""

    is_binary: bool
    reason: str
    mime: Optional[str] = None


def _extension(path: str) -> str:
    name = os.path.basename(path).lower()
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1]


def _magic_match(buf: bytes) -> Optional[str]:
    for sig, mime in MAGIC_SIGNATURES.items():
        if buf.startswith(sig):
            return mime
    return None


def _looks_textual(buf: bytes) -> bool:
    """Return ``True`` when the buffer is plausible text."""
    if not buf:
        return True
    if b"\x00" in buf:
        return False
    # Allow common control characters: TAB, LF, CR, FF, BS, ESC.
    text_chars = bytes(range(32, 127)) + b"\b\t\n\r\f\x1b"
    nontext = sum(1 for byte in buf if bytes([byte]) not in text_chars)
    if nontext / len(buf) > BINARY_NONTEXT_RATIO:
        # As a final escape hatch, try to decode as UTF-8. UTF-8 text often has
        # many high bytes that the simple table above flags as non-text.
        try:
            buf.decode("utf-8")
            return True
        except UnicodeDecodeError:
            return False
    return True


def detect_binary(
    file_path: str,
    *,
    use_extension: bool = True,
    use_magic: bool = True,
    use_heuristic: bool = True,
    text_extensions: Optional[Iterable[str]] = None,
    binary_extensions: Optional[Iterable[str]] = None,
) -> BinaryDetection:
    """Inspect ``file_path`` and return a :class:`BinaryDetection`.

    Parameters mirror the layered design described in the module docstring.
    Passing ``False`` for any of the ``use_*`` flags disables that layer.
    """
    text_set = set(TEXT_EXTENSIONS if text_extensions is None else text_extensions)
    binary_set = set(BINARY_EXTENSIONS if binary_extensions is None else binary_extensions)
    ext = _extension(file_path)

    if use_extension:
        if ext and ext in binary_set:
            return BinaryDetection(True, f"binary extension '.{ext}'")
        if ext and ext in text_set:
            return BinaryDetection(False, f"text extension '.{ext}'")

    try:
        with open(file_path, "rb") as fh:
            buf = fh.read(BINARY_SNIFF_BYTES)
    except OSError as exc:
        # If we cannot read the file we cannot bundle it - report as binary so
        # the bundler skips it cleanly rather than crashing.
        return BinaryDetection(True, f"unreadable: {exc}")

    if use_magic:
        mime = _magic_match(buf)
        if mime is not None:
            return BinaryDetection(True, f"magic signature {mime}", mime=mime)

    if use_heuristic:
        if not _looks_textual(buf):
            return BinaryDetection(True, "binary heuristic (NUL byte or non-text ratio)")

    return BinaryDetection(False, "appears to be text")


def is_binary(file_path: str) -> bool:
    """Convenience wrapper that just returns the boolean decision."""
    return detect_binary(file_path).is_binary
