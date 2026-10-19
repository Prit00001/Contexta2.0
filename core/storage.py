"""Original-file storage abstraction. Local filesystem for dev/tests; Supabase Storage
(via its REST API, over plain httpx - no extra SDK) for production, matching the rest
of the codebase's style of talking to OpenAI-compatible/arXiv HTTP APIs directly.

Paths are always "<workspace_id>/<safe filename>". Both backends refuse paths that could
escape the storage root or another workspace's prefix (SR-30, defect D-01)."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path, PurePosixPath

import httpx

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._ ()\-]")
_MAX_FILENAME = 200


def safe_filename(name: str | None) -> str:
    """Reduce a client-supplied filename to a safe basename.

    Drops any directory components (both / and \\), control characters and leading dots,
    replaces anything outside a conservative character set with "_", and caps the length
    while keeping the extension. Never returns an empty string.
    """
    base = re.split(r"[/\\]", name or "")[-1]
    base = "".join(ch for ch in base if unicodedata.category(ch)[0] != "C")
    base = _UNSAFE_CHARS.sub("_", base).strip().lstrip(".").strip()
    if not base:
        return "upload"
    if len(base) > _MAX_FILENAME:
        stem, dot, ext = base.rpartition(".")
        if dot and 0 < len(ext) <= 10:
            base = stem[: _MAX_FILENAME - len(ext) - 1] + "." + ext
        else:
            base = base[:_MAX_FILENAME]
    return base


def _check_object_path(path: str) -> str:
    parts = PurePosixPath(path).parts if path else ()
    if not parts or path.startswith("/") or "//" in path or any(p in ("..", ".") for p in parts):
        raise ValueError("unsafe storage path")
    return path


class LocalStorage:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._resolved_root = self.root.resolve()

    def _path(self, path: str) -> Path:
        p = (self.root / path).resolve()
        if p == self._resolved_root or not p.is_relative_to(self._resolved_root):
            raise ValueError("unsafe storage path")
        return p

    def save(self, path: str, data: bytes) -> str:
        p = self._path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return path

    def load(self, path: str) -> bytes:
        return self._path(path).read_bytes()

    def delete(self, path: str) -> None:
        self._path(path).unlink(missing_ok=True)


class SupabaseStorage:
    def __init__(self, base_url: str, bucket: str, service_key: str, client: httpx.Client):
        self.base_url = base_url.rstrip("/")
        self.bucket = bucket
        self.client = client
        self.headers = {"Authorization": f"Bearer {service_key}", "apikey": service_key}

    def _url(self, path: str) -> str:
        return f"{self.base_url}/storage/v1/object/{self.bucket}/{_check_object_path(path)}"

    def save(self, path: str, data: bytes) -> str:
        url = self._url(path)
        r = self.client.post(url, headers={**self.headers, "Content-Type": "application/octet-stream"}, content=data)
        r.raise_for_status()
        return path

    def load(self, path: str) -> bytes:
        r = self.client.get(self._url(path), headers=self.headers)
        r.raise_for_status()
        return r.content

    def delete(self, path: str) -> None:
        r = self.client.delete(self._url(path), headers=self.headers)
        if r.status_code not in (200, 404):
            r.raise_for_status()


def get_storage(settings):
    if settings.storage_backend == "supabase":
        return SupabaseStorage(
            settings.supabase_url, settings.storage_bucket, settings.supabase_service_key, httpx.Client(timeout=30.0)
        )
    return LocalStorage(Path(settings.storage_dir))
