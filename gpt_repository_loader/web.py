"""Flask-based Web UI (issue #51) and JSON REST API.

The Web UI exposes the same bundle pipeline as the CLI through a browser-friendly
single-page form plus a small JSON API suitable for programmatic use. The UI
runs in three modes:

* **Local path** - the user pastes the absolute path of a repo on the same host
  as the Flask server. Useful for developers running the UI on their laptop.
* **Remote URL** - the user supplies a URL recognised by
  :func:`gpt_repository_loader.url_loader.is_url`. The repo is cloned into a
  temp dir, bundled, then the temp dir is cleaned up.
* **Upload archive** - the user uploads a zip/tar archive. It is extracted into
  a temp dir, bundled, and the temp dir is cleaned up.

All endpoints work the same way regardless of mode: the bundle text and full
metadata are returned alongside a list of ignored files so the UI can render
the "what got skipped" report (issue #52).
"""

from __future__ import annotations

import io
import os
import shutil
import tempfile
import zipfile
from typing import Any, Dict, List

try:
    from flask import (  # type: ignore
        Flask,
        abort,
        jsonify,
        render_template,
        request,
        send_file,
    )
    from werkzeug.utils import secure_filename  # type: ignore
    HAS_FLASK = True
except Exception:  # pragma: no cover - import-time guard
    Flask = None  # type: ignore
    HAS_FLASK = False

from .compression import write_zip_bundle
from .constants import DEFAULT_OUTPUT_FILENAME, ENCODING_GZIP_BASE64, ENCODING_RAW
from .core import BundleOptions, bundle_repository
from .preamble import PreambleConfig
from .reverse import unbundle
from .url_loader import is_url, open_remote


def _build_bundle_options(form: Dict[str, Any], repo_path: str) -> BundleOptions:
    """Construct a :class:`BundleOptions` from a Flask form payload."""
    exclude_lines = [
        line.strip()
        for line in (form.get("exclude") or "").splitlines()
        if line.strip()
    ]
    include_lines = [
        line.strip()
        for line in (form.get("include") or "").splitlines()
        if line.strip()
    ]
    preamble_text = form.get("preamble_text") or None
    preamble_config = PreambleConfig(
        text=preamble_text if preamble_text else None,
        include_stats=_truthy(form.get("preamble_stats")),
    )
    return BundleOptions(
        repo_path=repo_path,
        preamble=preamble_config,
        extra_excludes=exclude_lines,
        extra_includes=include_lines,
        include_gitignore=_truthy(form.get("include_gitignore"), default=True),
        include_gptignore=_truthy(form.get("include_gptignore"), default=True),
        skip_binary=_truthy(form.get("skip_binary"), default=True),
        follow_symlinks=_truthy(form.get("follow_symlinks")),
        tracked_only=_truthy(form.get("tracked_only")),
        use_git_check_ignore=_truthy(form.get("use_git_check_ignore")),
        encoding_mode=(
            ENCODING_GZIP_BASE64 if _truthy(form.get("compress")) else ENCODING_RAW
        ),
        token_limit=int(form.get("token_limit") or 0),
        chunk_when_over_limit=_truthy(form.get("chunk")),
        model=(form.get("model") or None) or None,
        sort_paths=True,
    )


def _truthy(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y", "t"}


def _extract_upload(file_storage) -> str:
    """Save an uploaded archive into a temp dir and return its repo root."""
    filename = secure_filename(file_storage.filename or "upload")
    if not filename:
        raise ValueError("Uploaded file has no usable name.")
    tmpdir = tempfile.mkdtemp(prefix="gptrepo-upload-")
    archive_path = os.path.join(tmpdir, filename)
    file_storage.save(archive_path)
    extract_dir = os.path.join(tmpdir, "src")
    os.makedirs(extract_dir, exist_ok=True)
    extract_dir_abs = os.path.abspath(extract_dir)
    if archive_path.lower().endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zf:
            for member in zf.namelist():
                normalized = os.path.normpath(member.replace("\\", "/"))
                if normalized.startswith("..") or os.path.isabs(normalized):
                    shutil.rmtree(tmpdir, ignore_errors=True)
                    raise ValueError(f"Unsafe path in archive: {member!r}")
            zf.extractall(extract_dir)
    else:
        import tarfile
        with tarfile.open(archive_path) as tf:
            for member in tf.getmembers():
                normalized = member.name.replace("\\", "/").lstrip("/")
                resolved = os.path.abspath(
                    os.path.join(extract_dir_abs, normalized)
                )
                if resolved != extract_dir_abs and not resolved.startswith(
                    extract_dir_abs + os.sep
                ):
                    shutil.rmtree(tmpdir, ignore_errors=True)
                    raise ValueError(f"Unsafe path in archive: {member.name!r}")
            tf.extractall(extract_dir)
    # If single top-level directory exists, promote it.
    entries = [e for e in os.listdir(extract_dir) if not e.startswith(".")]
    if len(entries) == 1 and os.path.isdir(os.path.join(extract_dir, entries[0])):
        return os.path.join(extract_dir, entries[0])
    return extract_dir


def _result_payload(result, opts: BundleOptions) -> Dict[str, Any]:
    return {
        "preamble": result.preamble,
        "bundle": result.text,
        "files": [
            {
                "path": f.rel_path,
                "tokens": f.token_estimate,
                "size_bytes": f.size_bytes,
                "encoding": f.encoding,
            }
            for f in result.files
        ],
        "ignored": [
            {"path": f.rel_path, "reason": f.reason} for f in result.ignored
        ],
        "discovered_ignore_files": result.discovered_ignore_files,
        "warnings": result.warnings,
        "token_count": result.token_count,
        "token_method": result.token_method,
        "options": {
            "repo_path": opts.repo_path,
            "compress": opts.encoding_mode == ENCODING_GZIP_BASE64,
            "skip_binary": opts.skip_binary,
            "include_gitignore": opts.include_gitignore,
            "include_gptignore": opts.include_gptignore,
            "model": opts.model,
            "token_limit": opts.token_limit,
            "chunk": opts.chunk_when_over_limit,
        },
        "chunks": result.chunks,
    }


def create_app() -> Flask:
    """Return a configured :class:`Flask` app instance."""
    if not HAS_FLASK:
        raise RuntimeError(
            "Flask is required to run the Web UI. Install with "
            "`pip install gpt-repository-loader[web]`."
        )
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "templates"),
        static_folder=os.path.join(os.path.dirname(__file__), "static"),
        static_url_path="/static",
    )
    app.config["MAX_CONTENT_LENGTH"] = 256 * 1024 * 1024  # 256 MiB upload cap

    def _bundle_from_request(form, files) -> Dict[str, Any]:
        repo_input = (form.get("repo") or "").strip()
        upload = files.get("upload") if files else None
        cleanup_dirs: List[str] = []
        try:
            if upload and upload.filename:
                repo_path = _extract_upload(upload)
                cleanup_dirs.append(os.path.dirname(os.path.dirname(repo_path)))
                opts = _build_bundle_options(form, repo_path)
                result = bundle_repository(opts)
                return _result_payload(result, opts)
            if not repo_input:
                abort(400, "Provide a repo path, URL, or upload an archive.")
            if is_url(repo_input):
                with open_remote(repo_input) as remote:
                    opts = _build_bundle_options(form, remote.path)
                    result = bundle_repository(opts)
                    return _result_payload(result, opts)
            if not os.path.isdir(repo_input):
                abort(400, f"Path does not exist or is not a directory: {repo_input}")
            opts = _build_bundle_options(form, repo_input)
            result = bundle_repository(opts)
            return _result_payload(result, opts)
        finally:
            for d in cleanup_dirs:
                shutil.rmtree(d, ignore_errors=True)

    @app.route("/", methods=["GET"])
    def index():
        return render_template("index.html")

    @app.route("/api/bundle", methods=["POST"])
    def api_bundle():
        if request.is_json:
            form = request.get_json(silent=True) or {}
            files = None
        else:
            form = request.form
            files = request.files
        try:
            payload = _bundle_from_request(form, files)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(payload)

    @app.route("/api/bundle/download", methods=["POST"])
    def api_download():
        form = request.form
        files = request.files
        try:
            payload = _bundle_from_request(form, files)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        fmt = (form.get("format") or "text").lower()
        if fmt == "json":
            import json
            buf = io.BytesIO(json.dumps(payload, indent=2).encode("utf-8"))
            return send_file(
                buf, mimetype="application/json", as_attachment=True,
                download_name="bundle.json",
            )
        if fmt == "zip":
            tmp_zip = tempfile.NamedTemporaryFile(
                suffix=".zip", delete=False, prefix="gptrepo-"
            )
            tmp_zip.close()
            try:
                write_zip_bundle(
                    tmp_zip.name,
                    text_bundle=payload["bundle"],
                    files=[
                        (f["path"], f.get("content", "").encode("utf-8"))
                        for f in payload["files"]
                    ],
                )
                with open(tmp_zip.name, "rb") as fh:
                    buf = io.BytesIO(fh.read())
            finally:
                try:
                    os.unlink(tmp_zip.name)
                except OSError:
                    pass
            return send_file(
                buf, mimetype="application/zip", as_attachment=True,
                download_name="bundle.zip",
            )
        # Default: plain text
        buf = io.BytesIO(payload["bundle"].encode("utf-8"))
        return send_file(
            buf, mimetype="text/plain", as_attachment=True,
            download_name=DEFAULT_OUTPUT_FILENAME,
        )

    @app.route("/api/unbundle", methods=["POST"])
    def api_unbundle():
        if request.is_json:
            data = request.get_json(silent=True) or {}
            bundle_text = data.get("bundle") or ""
            destination = data.get("destination")
            overwrite = bool(data.get("overwrite"))
            only = data.get("only") or None
        else:
            bundle_text = request.form.get("bundle") or (
                request.files["upload"].read().decode("utf-8")
                if "upload" in request.files else ""
            )
            destination = request.form.get("destination")
            overwrite = _truthy(request.form.get("overwrite"))
            only = request.form.get("only")
            if only:
                only = [line.strip() for line in only.splitlines() if line.strip()]
        if not bundle_text:
            return jsonify({"error": "No bundle text provided."}), 400
        if not destination:
            destination = tempfile.mkdtemp(prefix="gptrepo-unbundle-")
        try:
            written = unbundle(
                bundle_text,
                destination,
                overwrite=overwrite,
                include=only,
            )
        except (ValueError, FileExistsError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"destination": destination, "written": written})

    @app.route("/api/health", methods=["GET"])
    def api_health():
        from . import __version__
        return jsonify({"status": "ok", "version": __version__})

    @app.errorhandler(400)
    def handle_400(err):
        message = getattr(err, "description", "Bad request")
        return jsonify({"error": message}), 400

    return app


def run(host: str = "127.0.0.1", port: int = 5050, debug: bool = False) -> None:
    """Convenience wrapper for ``python -c 'from ...web import run; run()'``."""
    app = create_app()
    app.run(host=host, port=port, debug=debug)
