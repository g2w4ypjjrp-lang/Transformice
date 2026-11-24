"""Private file storage with short-lived signed URLs.

* LocalStorage    — filesystem (single VPS / development). Signed URLs are
                    HMAC tokens served by GET /api/v1/files/{token}.
* SupabaseStorage — Supabase Storage REST API using the service-role key
                    (backend only; never sent to the browser).

The frontend never receives storage credentials or permanent URLs.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from functools import lru_cache
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import httpx

from app.core.config import Settings, get_settings


class StorageError(Exception):
    pass


class Storage(Protocol):
    def put(self, bucket: str, path: str, content: bytes, content_type: str) -> None: ...
    def get(self, bucket: str, path: str) -> bytes: ...
    def delete(self, bucket: str, path: str) -> None: ...
    def signed_url(self, bucket: str, path: str, *, download_name: str | None = None) -> str: ...


def _safe_parts(bucket: str, path: str) -> tuple[str, str]:
    if not bucket.isidentifier() or ".." in path.split("/") or path.startswith("/") or "\\" in path:
        raise StorageError("invalid storage path")
    return bucket, path


class LocalStorage:
    def __init__(self, settings: Settings):
        self.root = Path(settings.local_storage_dir).resolve()
        self.secret = settings.signing_secret.get_secret_value().encode()
        self.ttl = settings.signed_url_ttl_seconds
        self.base = settings.public_api_url.rstrip("/") + settings.api_prefix + "/files/"

    def _file(self, bucket: str, path: str) -> Path:
        bucket, path = _safe_parts(bucket, path)
        f = (self.root / bucket / path).resolve()
        if self.root not in f.parents:
            raise StorageError("invalid storage path")
        return f

    def put(self, bucket: str, path: str, content: bytes, content_type: str) -> None:
        f = self._file(bucket, path)
        f.parent.mkdir(parents=True, exist_ok=True)
        if f.exists():
            raise StorageError("object already exists")
        tmp = f.with_suffix(f.suffix + ".tmp")
        tmp.write_bytes(content)
        tmp.replace(f)

    def get(self, bucket: str, path: str) -> bytes:
        f = self._file(bucket, path)
        if not f.exists():
            raise StorageError("object not found")
        return f.read_bytes()

    def delete(self, bucket: str, path: str) -> None:
        f = self._file(bucket, path)
        if f.exists():
            f.unlink()

    # -- signed URLs ------------------------------------------------------------
    def sign(self, bucket: str, path: str, download_name: str | None = None, ttl: int | None = None) -> str:
        payload = {"b": bucket, "p": path, "e": int(time.time()) + (ttl or self.ttl)}
        if download_name:
            payload["n"] = download_name
        body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=")
        sig = base64.urlsafe_b64encode(hmac.new(self.secret, body, hashlib.sha256).digest()).rstrip(b"=")
        return (body + b"." + sig).decode()

    def verify(self, token: str) -> dict:
        try:
            body, sig = token.encode().split(b".", 1)
        except ValueError as exc:
            raise StorageError("malformed token") from exc
        expected = base64.urlsafe_b64encode(hmac.new(self.secret, body, hashlib.sha256).digest()).rstrip(b"=")
        if not hmac.compare_digest(expected, sig):
            raise StorageError("bad signature")
        payload = json.loads(base64.urlsafe_b64decode(body + b"=" * (-len(body) % 4)))
        if payload["e"] < time.time():
            raise StorageError("expired")
        return payload

    def signed_url(self, bucket: str, path: str, *, download_name: str | None = None) -> str:
        return self.base + self.sign(bucket, path, download_name)


class SupabaseStorage:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        assert settings.supabase_url and settings.supabase_service_role_key
        self.base = settings.supabase_url.rstrip("/") + "/storage/v1"
        key = settings.supabase_service_role_key.get_secret_value()
        self.client = client or httpx.Client(timeout=60)
        self.headers = {"Authorization": f"Bearer {key}", "apikey": key}
        self.ttl = settings.signed_url_ttl_seconds

    def _url(self, kind: str, bucket: str, path: str) -> str:
        bucket, path = _safe_parts(bucket, path)
        return f"{self.base}/object/{kind}{bucket}/{quote(path)}"

    def _check(self, r: httpx.Response) -> httpx.Response:
        if r.status_code >= 400:
            raise StorageError(f"supabase storage error {r.status_code}")
        return r

    def put(self, bucket: str, path: str, content: bytes, content_type: str) -> None:
        self._check(self.client.post(self._url("", bucket, path), content=content,
                                     headers={**self.headers, "Content-Type": content_type, "x-upsert": "false"}))

    def get(self, bucket: str, path: str) -> bytes:
        return self._check(self.client.get(self._url("", bucket, path), headers=self.headers)).content

    def delete(self, bucket: str, path: str) -> None:
        _safe_parts(bucket, path)
        self._check(self.client.request("DELETE", f"{self.base}/object/{bucket}", headers=self.headers,
                                        json={"prefixes": [path]}))

    def signed_url(self, bucket: str, path: str, *, download_name: str | None = None) -> str:
        body: dict = {"expiresIn": self.ttl}
        r = self._check(self.client.post(self._url("sign/", bucket, path), headers=self.headers, json=body))
        signed = r.json().get("signedURL") or r.json().get("signedUrl")
        if not signed:
            raise StorageError("no signed url returned")
        url = self.base + signed if signed.startswith("/") else signed
        if download_name:
            url += ("&" if "?" in url else "?") + "download=" + quote(download_name)
        return url


@lru_cache
def get_storage() -> Storage:
    s = get_settings()
    return SupabaseStorage(s) if s.storage_backend == "supabase" else LocalStorage(s)


class BucketStorage:
    """Adapter used by the Luca export repository (single bucket)."""

    def __init__(self, storage: Storage, bucket: str):
        self.storage, self.bucket = storage, bucket

    def put(self, path: str, content: bytes, content_type: str) -> None:
        self.storage.put(self.bucket, path, content, content_type)
