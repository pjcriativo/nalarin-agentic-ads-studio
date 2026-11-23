"""Key that authenticates the tenant context handed to PostgreSQL.

RLS policies do not trust the raw ``app.current_org`` setting (any SQL running
in the session could overwrite it). The application signs
``org | platform_admin | nonce`` with this key (HMAC-SHA256); the database
re-computes the signature with its own copy of the key (table
``tenant_ctx_secret``, unreadable by the runtime role) inside SECURITY DEFINER
functions, and a forged or stale context yields NO organization at all.

Only ``os`` / ``hashlib`` are used so Alembic migrations can import this module
without loading the application.
"""
import hashlib
import hmac
import os


def tenant_context_key() -> str:
    """Return the signing key as text (``TENANT_CONTEXT_KEY``, else derived
    from ``SECRET_KEY`` so deployments work without extra configuration)."""
    explicit = os.getenv("TENANT_CONTEXT_KEY", "").strip()
    if explicit:
        if len(explicit) < 32:
            raise ValueError("TENANT_CONTEXT_KEY must be at least 32 characters")
        return explicit
    secret = os.getenv("SECRET_KEY", "")
    if not secret:
        raise ValueError("SECRET_KEY (or TENANT_CONTEXT_KEY) is required to sign the tenant context")
    return hmac.new(secret.encode(), b"tenant-context-v1", hashlib.sha256).hexdigest()


def sign_context(org_id: str, platform_admin: str, nonce: str, key: str = None) -> str:
    key = key or tenant_context_key()
    message = f"{org_id}|{platform_admin}|{nonce}".encode()
    return hmac.new(key.encode(), message, hashlib.sha256).hexdigest()
