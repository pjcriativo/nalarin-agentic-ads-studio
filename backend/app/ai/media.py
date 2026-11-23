"""Load an image for a vision request, safely.

* ``/uploads/<org>/<signed name>``: only the CALLER'S organization's own private files
  (another tenant's path, an unsigned name or a legacy flat file is refused);
* ``http(s)://``: fetched through the SSRF guard (public hosts only, redirects re-checked, size cap);
* the bytes must really be a PNG / JPEG / GIF / WEBP (magic bytes), whatever the URL says.
"""
from typing import Optional

from fastapi import HTTPException

from app.ai.types import ImagePart
from app.core import storage

MAX_IMAGE_BYTES = 10 * 1024 * 1024


def sniff_image_mime(content: bytes) -> Optional[str]:
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if content[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if content[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


async def load_template_image(reference: str, org_id: str) -> ImagePart:
    if reference.startswith("/uploads/"):
        parts = reference[len("/uploads/"):].split("/")
        path = storage.local_path(org_id, parts[1]) if len(parts) == 2 and parts[0] == org_id else None
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="Template image not found")
        content = path.read_bytes()
        if len(content) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=400, detail="Template image is too large")
    elif reference.startswith(("http://", "https://")):
        try:
            content = await storage.fetch_public_bytes(reference, MAX_IMAGE_BYTES)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid media reference")
    else:
        raise HTTPException(status_code=400, detail="Invalid media reference")
    mime = sniff_image_mime(content)
    if mime is None:
        raise HTTPException(status_code=400, detail="File content does not match an image type")
    return ImagePart(mime_type=mime, data=content)
