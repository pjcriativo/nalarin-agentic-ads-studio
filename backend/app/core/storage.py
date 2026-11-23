"""Tenant-namespaced storage for private uploads.

* Local files live in ``uploads/<organization_id>/<uuid>_<sig>.<ext>`` and R2
  objects under ``orgs/<organization_id>/``. A file name is never reusable
  across tenants.
* The ``<sig>`` part is an HMAC over ``organization|uuid`` (key derived from
  SECRET_KEY). The serving route recomputes it, so a URL can neither be forged
  nor guessed by enumerating ids, and a file requested through ANOTHER
  organization's path does not match. The signature sits in the file name, not
  in a query string, so URLs still end in the real extension (the UI
  recognizes videos by it) and can be used in <img> / <video> tags.
* The URL is a capability: whoever holds it can view that one file. It is
  only listed in the owning organization's own records (RLS).
* Public R2 objects are an intentional public CDN (``R2_PUBLIC_URL``); their
  keys are namespaced and unguessable but are not access controlled.
"""
import hashlib
import hmac
import ipaddress
import os
import re
import socket
import uuid
from urllib.parse import urlparse
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

from app.core.config import settings

UPLOAD_DIR = (Path(__file__).parent.parent.parent / "uploads").resolve()
os.makedirs(UPLOAD_DIR, mode=0o755, exist_ok=True)

_ORG_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
_NAME_RE = re.compile(r"^(?P<uid>[0-9a-f]{32})_(?P<sig>[0-9a-f]{16})(?P<ext>\.[a-z0-9]{2,5})$")


def _key() -> bytes:
    return hmac.new(settings.SECRET_KEY.encode(), b"private-uploads-v1", hashlib.sha256).digest()


def _sig(org_id: str, uid: str) -> str:
    return hmac.new(_key(), f"{org_id}|{uid}".encode(), hashlib.sha256).hexdigest()[:16]


def new_signed_name(org_id: str, extension: str) -> str:
    uid = uuid.uuid4().hex
    return f"{uid}_{_sig(org_id, uid)}{extension.lower()}"


def is_valid_signed_name(org_id: str, filename: str) -> bool:
    if not _ORG_RE.match(org_id or ""):
        return False
    match = _NAME_RE.match(filename or "")
    return bool(match) and hmac.compare_digest(match.group("sig"), _sig(org_id, match.group("uid")))


def local_path(org_id: str, filename: str) -> Optional[Path]:
    """Filesystem path of a valid signed file, or None (also for traversal)."""
    if not is_valid_signed_name(org_id, filename):
        return None
    path = (UPLOAD_DIR / org_id / filename).resolve()
    if UPLOAD_DIR not in path.parents:
        return None
    return path


def save_local(org_id: str, content: bytes, extension: str) -> str:
    """Persist ``content`` for ``org_id`` and return its ``/uploads/...`` URL."""
    if not _ORG_RE.match(org_id or ""):
        raise ValueError("Malformed organization id")
    name = new_signed_name(org_id, extension)
    target_dir = UPLOAD_DIR / org_id
    os.makedirs(target_dir, mode=0o755, exist_ok=True)
    with open(target_dir / name, "wb") as buffer:
        buffer.write(content)
    return f"/uploads/{org_id}/{name}"


def r2_key(org_id: str, extension: str) -> str:
    return f"orgs/{org_id}/{uuid.uuid4().hex}{extension.lower()}"


def media_url_belongs_to_org(url: Optional[str], org_id: str) -> bool:
    """True when ``url`` is empty, an external URL, or a private upload that
    belongs to ``org_id``. False for another tenant's (or an unsigned/legacy)
    private upload, so a record can never point at someone else's file."""
    if not url:
        return True
    if url.startswith("/uploads/"):
        parts = url[len("/uploads/"):].split("/")
        return len(parts) == 2 and parts[0] == org_id and is_valid_signed_name(org_id, parts[1])
    base = (settings.R2_PUBLIC_URL or "").rstrip("/")
    if base and url.startswith(base + "/"):
        key = url[len(base) + 1:]
        return key.startswith(f"orgs/{org_id}/")
    return True


def require_own_media(org_id: str, *urls: Optional[str]) -> None:
    for url in urls:
        if not media_url_belongs_to_org(url, org_id):
            raise HTTPException(status_code=400, detail="Invalid media reference")


def require_own_shots(org_id: str, shots) -> None:
    """``product_shots`` is a JSON list of media URLs: each must be the caller's."""
    if isinstance(shots, list):
        require_own_media(org_id, *[x for x in shots if isinstance(x, str)])


def resolve_media_reference(org_id: str, ref: str) -> str:
    """Turn a media reference sent to an ad platform into something safe to read.

    * ``http(s)://...`` is passed through (downloaded by the service).
    * ``[/]uploads/<org>/<signed name>`` must belong to ``org_id`` and becomes
      the absolute path of that file.
    * Anything else (arbitrary server paths, another tenant's file, legacy flat
      files, traversal) is refused: the service would otherwise read it from disk.
    """
    if ref.startswith(("http://", "https://")):
        assert_public_http_url(ref)
        return ref
    parts = ref.lstrip("/").split("/")
    if len(parts) == 3 and parts[0] == "uploads" and parts[1] == org_id:
        path = local_path(org_id, parts[2])
        if path is not None and path.is_file():
            return str(path)
    raise HTTPException(status_code=400, detail="Invalid media reference")


def _resolve_host(host: str):
    """DNS resolution seam (tests replace it to stay hermetic)."""
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def assert_public_http_url(url: str) -> None:
    """Best-effort SSRF guard for URLs the server will fetch on a user's behalf.

    Only http(s) with a host that resolves EXCLUSIVELY to public addresses:
    loopback, private ranges, link-local (cloud metadata 169.254.169.254) and
    reserved space are refused. RESIDUAL RISK (documented): DNS rebinding between
    this check and the actual fetch, and redirects followed by the fetching
    library, are not covered -- egress filtering at the network layer is needed.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="Invalid media reference")
    try:
        ipaddress.ip_address(parsed.hostname)
        addresses = [parsed.hostname]  # literal IP: no DNS involved
    except ValueError:
        try:
            addresses = _resolve_host(parsed.hostname)
        except OSError:
            raise HTTPException(status_code=400, detail="Invalid media reference")
    if not addresses:
        raise HTTPException(status_code=400, detail="Invalid media reference")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address.split("%")[0])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid media reference")
        if not ip.is_global or ip.is_multicast:
            raise HTTPException(status_code=400, detail="Invalid media reference")
