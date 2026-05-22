"""Tests for the Flask web UI."""

from __future__ import annotations

import io
import os
import zipfile

import pytest

flask = pytest.importorskip("flask")
from gpt_repository_loader.web import create_app  # noqa: E402


@pytest.fixture
def client(tmp_path):
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_index_renders(client):
    res = client.get("/")
    assert res.status_code == 200
    assert b"gpt-repository-loader" in res.data


def test_health_endpoint(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "ok"
    assert body["version"]


def test_api_bundle_with_local_path(client, tiny_repo):
    res = client.post("/api/bundle", data={"repo": tiny_repo})
    assert res.status_code == 200
    body = res.get_json()
    assert body["files"]
    assert any(f["path"] == "main.py" for f in body["files"])


def test_api_bundle_rejects_missing_path(client):
    res = client.post("/api/bundle", data={"repo": "/__definitely_missing__"})
    assert res.status_code == 400
    body = res.get_json()
    assert "error" in body


def test_api_bundle_download_text(client, tiny_repo):
    res = client.post(
        "/api/bundle/download",
        data={"repo": tiny_repo, "format": "text"},
    )
    assert res.status_code == 200
    assert b"main.py" in res.data


def test_api_bundle_download_zip(client, tiny_repo):
    res = client.post(
        "/api/bundle/download",
        data={"repo": tiny_repo, "format": "zip"},
    )
    assert res.status_code == 200
    buf = io.BytesIO(res.data)
    with zipfile.ZipFile(buf) as zf:
        names = zf.namelist()
    assert "bundle.txt" in names


def test_api_bundle_upload_archive(client, tmp_path):
    archive = tmp_path / "small.zip"
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "foo.py").write_text("print('upload')\n")
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(inner / "foo.py", arcname="inner/foo.py")

    with open(archive, "rb") as fh:
        res = client.post(
            "/api/bundle",
            data={"upload": (fh, "small.zip")},
            content_type="multipart/form-data",
        )
    assert res.status_code == 200
    body = res.get_json()
    assert any(f["path"].endswith("foo.py") for f in body["files"])


def test_api_unbundle(client, tmp_path):
    bundle = "preamble\n----\nfoo.py\nprint('hi')\n--END--"
    dest = str(tmp_path / "out")
    res = client.post(
        "/api/unbundle",
        data={"bundle": bundle, "destination": dest},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert os.path.exists(os.path.join(dest, "foo.py"))
    assert body["destination"] == dest
    assert body["written"]


def test_api_unbundle_rejects_traversal(client, tmp_path):
    bundle = "preamble\n----\n../escape.txt\nbad\n--END--"
    dest = str(tmp_path / "out")
    res = client.post(
        "/api/unbundle",
        data={"bundle": bundle, "destination": dest},
    )
    assert res.status_code == 400
    body = res.get_json()
    assert "error" in body
