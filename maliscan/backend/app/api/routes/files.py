"""Download endpoint for locally stored files, authorised by a short-lived
HMAC-signed token (issued only after an RLS-checked API call)."""

from fastapi import APIRouter
from fastapi.responses import Response

from app.api.errors import ApiError
from app.services.storage import LocalStorage, StorageError, get_storage

router = APIRouter(tags=["files"])

_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp", "pdf": "application/pdf",
          "xml": "application/xml", "csv": "text/csv",
          "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


@router.get("/files/{token}", summary="Signed file download (local storage backend only)")
def download(token: str) -> Response:
    storage = get_storage()
    if not isinstance(storage, LocalStorage):
        raise ApiError(404, "not_found", "not found")
    try:
        payload = storage.verify(token)
        content = storage.get(payload["b"], payload["p"])
    except (StorageError, ValueError, KeyError) as exc:
        raise ApiError(403, "invalid_link", "link is invalid or expired") from exc
    ext = payload["p"].rsplit(".", 1)[-1].lower()
    headers = {"Cache-Control": "private, max-age=60", "X-Content-Type-Options": "nosniff",
               "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"}
    name = payload.get("n")
    if name:
        safe = "".join(c for c in name if c.isalnum() or c in "._-")[:100] or "download"
        headers["Content-Disposition"] = f'attachment; filename="{safe}"'
    else:
        headers["Content-Disposition"] = "inline"
    return Response(content, media_type=_TYPES.get(ext, "application/octet-stream"), headers=headers)
