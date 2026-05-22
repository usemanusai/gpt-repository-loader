"""Per-file content compression (issue #37) and bundle-level zip output (#48).

Two layers exist:

* **Per-file compression**: when ``--compress`` is supplied each file's contents
  are gzip-compressed, base64-encoded and emitted with an ``# encoding:``
  metadata line. The reverse loader transparently decodes them. The encoding
  metadata is forward-compatible: an unknown codec is reported as an error
  rather than silently mis-decoded.

* **Bundle-level zip output**: when ``--format zip`` is supplied the loader
  builds a ZIP archive that contains both the canonical text bundle (so it can
  still be consumed by an LLM through ``unzip -p``) and the raw files. This
  addresses the recurring "wouldn't a zip be smaller?" question from #48 while
  preserving the LLM-friendly format that motivated the project.
"""

from __future__ import annotations

import base64
import gzip
import io
import os
import zipfile
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

from .constants import (
    ENCODING_BASE64,
    ENCODING_GZIP_BASE64,
    ENCODING_PREFIX,
    ENCODING_RAW,
)


@dataclass
class EncodedContent:
    """A single file payload after encoding."""

    encoding: str
    text: str


def encode_content(content: str, *, mode: str = ENCODING_RAW) -> EncodedContent:
    """Encode ``content`` for inclusion in a bundle.

    ``mode`` is one of :data:`ENCODING_RAW`, :data:`ENCODING_BASE64` or
    :data:`ENCODING_GZIP_BASE64`. ``base64`` is offered separately for callers
    who want a deterministic, non-compressed binary-safe encoding (useful when
    bundling files with embedded delimiter strings).
    """
    if mode == ENCODING_RAW:
        return EncodedContent(encoding=ENCODING_RAW, text=content)
    raw_bytes = content.encode("utf-8")
    if mode == ENCODING_BASE64:
        encoded = base64.b64encode(raw_bytes).decode("ascii")
        return EncodedContent(encoding=ENCODING_BASE64, text=encoded)
    if mode == ENCODING_GZIP_BASE64:
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
            gz.write(raw_bytes)
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
        return EncodedContent(encoding=ENCODING_GZIP_BASE64, text=encoded)
    raise ValueError(f"Unknown encoding mode: {mode!r}")


def decode_content(text: str, encoding: str) -> str:
    """Reverse of :func:`encode_content`."""
    if encoding == ENCODING_RAW:
        return text
    payload = "".join(text.split())  # tolerate line-wrapped base64
    raw_bytes = base64.b64decode(payload)
    if encoding == ENCODING_BASE64:
        return raw_bytes.decode("utf-8", errors="replace")
    if encoding == ENCODING_GZIP_BASE64:
        buf = io.BytesIO(raw_bytes)
        with gzip.GzipFile(fileobj=buf, mode="rb") as gz:
            return gz.read().decode("utf-8", errors="replace")
    raise ValueError(f"Unknown encoding for decode: {encoding!r}")


def encoding_line(encoding: str) -> Optional[str]:
    """Return the metadata line emitted for ``encoding`` or ``None`` for raw."""
    if encoding == ENCODING_RAW:
        return None
    return f"{ENCODING_PREFIX} {encoding}"


def write_zip_bundle(
    zip_path: str,
    *,
    text_bundle: str,
    files: Iterable[Tuple[str, bytes]],
    compresslevel: int = 6,
) -> None:
    """Write a ZIP archive containing the text bundle and raw file payloads.

    The archive layout is:

    * ``bundle.txt``     - the canonical text bundle (post-encoding).
    * ``files/<path>``   - raw file payloads preserving the original tree.

    Both deflated. Compression level is configurable via ``compresslevel``.
    """
    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=compresslevel) as zf:
        zf.writestr("bundle.txt", text_bundle)
        for rel_path, payload in files:
            arcname = os.path.join("files", rel_path).replace(os.sep, "/")
            zf.writestr(arcname, payload)


def read_zip_bundle(zip_path: str) -> Tuple[str, list]:
    """Inverse of :func:`write_zip_bundle`. Returns ``(bundle_text, file_list)``."""
    with zipfile.ZipFile(zip_path, mode="r") as zf:
        bundle_text = zf.read("bundle.txt").decode("utf-8")
        files = []
        for name in zf.namelist():
            if name == "bundle.txt" or name.endswith("/"):
                continue
            if name.startswith("files/"):
                rel = name[len("files/") :]
            else:
                rel = name
            files.append((rel, zf.read(name)))
    return bundle_text, files
