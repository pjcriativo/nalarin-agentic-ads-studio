"""AIError -> HTTP answer for our API: a generic, secret-free message and a stable status."""
from fastapi import HTTPException

from app.ai import errors as E


def to_http(exc: E.AIError) -> HTTPException:
    status = E.HTTP_STATUS.get(exc.kind, 502)
    detail = E.PUBLIC_MESSAGES.get(exc.kind, E.PUBLIC_MESSAGES[E.UNKNOWN])
    return HTTPException(status_code=status, detail=detail, headers={"X-AI-Error": exc.kind})
