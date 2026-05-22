"""Tests for compression and zip output."""

from __future__ import annotations

import os
import zipfile

from gpt_repository_loader.compression import (
    decode_content,
    encode_content,
    read_zip_bundle,
    write_zip_bundle,
)
from gpt_repository_loader.constants import (
    ENCODING_BASE64,
    ENCODING_GZIP_BASE64,
    ENCODING_RAW,
)


def test_raw_roundtrip():
    encoded = encode_content("hello world", mode=ENCODING_RAW)
    assert encoded.encoding == ENCODING_RAW
    assert decode_content(encoded.text, encoded.encoding) == "hello world"


def test_base64_roundtrip():
    encoded = encode_content("hello world", mode=ENCODING_BASE64)
    assert encoded.encoding == ENCODING_BASE64
    assert encoded.text != "hello world"
    assert decode_content(encoded.text, encoded.encoding) == "hello world"


def test_gzip_base64_roundtrip():
    payload = "lorem ipsum " * 200
    encoded = encode_content(payload, mode=ENCODING_GZIP_BASE64)
    assert encoded.encoding == ENCODING_GZIP_BASE64
    # Compression should make it shorter than the raw payload (lorem ipsum is
    # very compressible).
    assert len(encoded.text) < len(payload)
    assert decode_content(encoded.text, encoded.encoding) == payload


def test_zip_bundle_round_trip(temp_dir):
    bundle_text = "preamble\n----\nfoo.py\nprint('x')\n--END--"
    zip_path = os.path.join(temp_dir, "out.zip")
    write_zip_bundle(
        zip_path,
        text_bundle=bundle_text,
        files=[("foo.py", b"print('x')\n")],
    )
    with zipfile.ZipFile(zip_path) as zf:
        names = sorted(zf.namelist())
    assert names == ["bundle.txt", "files/foo.py"]
    text, files = read_zip_bundle(zip_path)
    assert text == bundle_text
    assert files == [("foo.py", b"print('x')\n")]


def test_decode_rejects_unknown_encoding():
    import pytest

    with pytest.raises(ValueError):
        decode_content("anything", "made-up-codec")


def test_encode_rejects_unknown_mode():
    import pytest

    with pytest.raises(ValueError):
        encode_content("text", mode="??")
