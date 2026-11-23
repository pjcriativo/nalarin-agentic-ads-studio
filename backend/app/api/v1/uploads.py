from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from fastapi.responses import FileResponse
import os
from typing import Dict
from app.core.config import settings
from app.core.storage import UPLOAD_DIR, local_path, r2_key, save_local  # noqa: F401
from app.core.tenancy import OrgContext, require_org_permission

router = APIRouter()
# Serves private files at /uploads/<org>/<signed name> (mounted WITHOUT the /api/v1 prefix).
files_router = APIRouter()

# Security: Define allowed file types and size limits
ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}


def _looks_like_image(content: bytes) -> bool:
    """Magic-byte sniff for the allowed image formats (extension alone is spoofable)."""
    if len(content) < 12:
        return False
    if content[:3] == b'\xff\xd8\xff':            # jpg/jpeg
        return True
    if content[:8] == b'\x89PNG\r\n\x1a\n':        # png
        return True
    if content[:6] in (b'GIF87a', b'GIF89a'):      # gif
        return True
    if content[:4] == b'RIFF' and content[8:12] == b'WEBP':  # webp
        return True
    return False
ALLOWED_VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.webm'}

# ISO-BMFF top-level boxes that may legitimately open an .mp4/.mov file.
_ISOBMFF_BOXES = (b'ftyp', b'moov', b'mdat', b'free', b'wide', b'skip')


def _looks_like_video(content: bytes, extension: str) -> bool:
    """Magic-byte sniff for the allowed video containers, matched to the
    claimed extension (a renamed executable must not ride in as .mp4)."""
    if len(content) < 12:
        return False
    if extension in ('.mp4', '.mov'):
        return content[4:8] in _ISOBMFF_BOXES
    if extension == '.webm':
        return content[:4] == b'\x1a\x45\xdf\xa3'          # EBML header
    if extension == '.avi':
        return content[:4] == b'RIFF' and content[8:12] == b'AVI '
    return False
ALLOWED_EXTENSIONS = ALLOWED_IMAGE_EXTENSIONS | ALLOWED_VIDEO_EXTENSIONS
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB for images
MAX_VIDEO_SIZE = 500 * 1024 * 1024  # 500MB for videos

# Local uploads are namespaced per organization (see app.core.storage).

# Initialize R2 client if configured
_s3_client = None

def get_s3_client():
    global _s3_client
    if _s3_client is None and settings.r2_enabled:
        import boto3
        _s3_client = boto3.client(
            's3',
            endpoint_url=settings.r2_endpoint_url,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            region_name='auto'
        )
    return _s3_client


async def upload_to_r2(file_content: bytes, filename: str, content_type: str) -> str:
    """Upload file to Cloudflare R2 and return public URL"""
    client = get_s3_client()
    if not client:
        raise HTTPException(status_code=500, detail="R2 storage not configured")

    try:
        client.put_object(
            Bucket=settings.R2_BUCKET_NAME,
            Key=filename,
            Body=file_content,
            ContentType=content_type
        )
        return f"{settings.R2_PUBLIC_URL}/{filename}"
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload to R2: {str(e)}")


async def upload_to_local(file_content: bytes, org_id: str, extension: str) -> str:
    """Store the file under the organization's namespace; returns the signed URL."""
    return save_local(org_id, file_content, extension)


@router.post("/", response_model=Dict[str, str])
async def upload_file(
    file: UploadFile = File(...),
    ctx: OrgContext = Depends(require_org_permission("uploads:write")),
):
    try:
        # Security: Sanitize filename to prevent path traversal
        safe_filename = os.path.basename(file.filename)
        file_extension = os.path.splitext(safe_filename)[1].lower()

        # Security: Validate file extension
        if file_extension not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}"
            )

        # Determine if video or image
        is_video = file_extension in ALLOWED_VIDEO_EXTENSIONS
        max_size = MAX_VIDEO_SIZE if is_video else MAX_IMAGE_SIZE

        # Read file content
        file_content = await file.read(max_size + 1)

        # Security: Validate file size
        if len(file_content) > max_size:
            raise HTTPException(
                status_code=400,
                detail=f"File too large. Maximum size: {max_size / (1024 * 1024)}MB"
            )

        # Security: magic-byte sniff so a renamed .exe can't ride in as .png/.mp4
        if is_video:
            if not _looks_like_video(file_content, file_extension):
                raise HTTPException(status_code=400, detail="File content does not match a video type")
        elif not _looks_like_image(file_content):
            raise HTTPException(status_code=400, detail="File content does not match an image type")

        # Upload to R2 (key namespaced by organization) if configured, otherwise local
        if settings.r2_enabled:
            url = await upload_to_r2(file_content, r2_key(ctx.org.id, file_extension),
                                     file.content_type or 'application/octet-stream')
        else:
            url = await upload_to_local(file_content, ctx.org.id, file_extension)

        # Return media type along with URL
        media_type = 'video' if is_video else 'image'
        return {"url": url, "media_type": media_type}
    except HTTPException:
        raise
    except Exception:
        # Never echo internal exception text to the client (leaks paths/stack info)
        raise HTTPException(status_code=500, detail="Could not upload file")


@files_router.get("/uploads/{org_id}/{filename}", include_in_schema=False)
def serve_private_upload(org_id: str, filename: str):
    """Serve a private upload. The signed file name must match the organization
    in the path; anything else (unknown, forged, another tenant's path,
    traversal) is the same 404."""
    path = local_path(org_id, filename)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})


@files_router.get("/uploads/{filename}", include_in_schema=False)
def legacy_flat_upload(filename: str):
    """Files from before tenant namespacing sit directly in uploads/ and have no
    known owner: they are quarantined (never served) until an operator relocates them."""
    raise HTTPException(status_code=404, detail="Not found")
