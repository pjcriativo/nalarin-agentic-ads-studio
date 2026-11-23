"""BYOK credential management (per organization).

* The key is validated, Fernet-encrypted (``app.core.token_encryption``) and stored; it is
  NEVER returned, logged or audited. Only ``last4`` is ever shown.
* Create / rotate verify the key against the provider first (a free, read-only call): a key
  the provider rejects is never stored; an unreachable provider is reported, not guessed.
* Rotation replaces the secret in place (same credential id, ``rotated_at`` set).
* Revocation WIPES the stored ciphertext: a revoked credential cannot be used or recovered.
* At most one ACTIVE credential per (organization, provider) (partial unique index).
"""
import re
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai import errors as E
from app.ai.adapters import PROVIDER_ADAPTERS
from app.core.token_encryption import decrypt_token, encrypt_token
from app.models import AiCredential

_KEY_RE = re.compile(r"^[\x21-\x7e]{8,512}$")  # printable ASCII, no whitespace/control characters


def normalize_key(api_key: str) -> str:
    key = (api_key or "").strip()
    if not _KEY_RE.match(key):
        raise E.AIError(E.INVALID_REQUEST, "API key has an invalid format")
    return key


def _now() -> datetime:
    return datetime.now(timezone.utc)


def public_view(c: AiCredential) -> dict:
    """The ONLY representation of a credential that leaves the process."""
    return {
        "id": c.id, "provider": c.provider, "label": c.label, "status": c.status,
        "last4": c.last4, "masked": f"••••{c.last4}" if c.last4 else None,
        "created_at": c.created_at, "rotated_at": c.rotated_at, "revoked_at": c.revoked_at,
        "last_verified_at": c.last_verified_at, "last_verification_ok": c.last_verification_ok,
    }


def active_credential(db: Session, org_id: str, provider: str) -> Optional[AiCredential]:
    return db.query(AiCredential).filter(
        AiCredential.organization_id == org_id, AiCredential.provider == provider,
        AiCredential.status == "active").first()


def get_plain_key(db: Session, org_id: str, provider: str) -> Tuple[AiCredential, str]:
    """Decrypted key of the organization's ACTIVE credential (never cross-organization)."""
    credential = active_credential(db, org_id, provider)
    if credential is None or not credential.encrypted_key:
        raise E.AIError(E.NOT_CONFIGURED, f"no active credential for {provider}", provider=provider)
    try:
        return credential, decrypt_token(credential.encrypted_key)
    except ValueError:
        raise E.AIError(E.NOT_CONFIGURED, "stored credential cannot be decrypted; rotate it", provider=provider)


async def verify_key(provider: str, api_key: str) -> None:
    await PROVIDER_ADAPTERS[provider].verify(api_key)


async def create(db: Session, org_id: str, user_id: Optional[str], provider: str, api_key: str,
                 label: Optional[str] = None, verify: bool = True) -> AiCredential:
    if provider not in PROVIDER_ADAPTERS:
        raise E.AIError(E.INVALID_REQUEST, "unsupported provider")
    key = normalize_key(api_key)
    if active_credential(db, org_id, provider) is not None:
        raise E.AIError(E.INVALID_REQUEST, "an active credential already exists: rotate or revoke it")
    verified = None
    if verify:
        await verify_key(provider, key)  # raises AIError: nothing is stored
        verified = True
    credential = AiCredential(
        organization_id=org_id, provider=provider, label=(label or None) and label.strip()[:80],
        encrypted_key=encrypt_token(key), last4=key[-4:], status="active", created_by_user_id=user_id,
        last_verified_at=_now() if verified else None, last_verification_ok=verified,
    )
    db.add(credential)
    try:
        db.flush()
    except IntegrityError:  # concurrent create
        db.rollback()
        raise E.AIError(E.INVALID_REQUEST, "an active credential already exists: rotate or revoke it")
    return credential


async def rotate(db: Session, credential: AiCredential, api_key: str, verify: bool = True) -> AiCredential:
    if credential.status != "active":
        raise E.AIError(E.INVALID_REQUEST, "credential is revoked")
    key = normalize_key(api_key)
    if verify:
        await verify_key(credential.provider, key)
    credential.encrypted_key = encrypt_token(key)
    credential.last4 = key[-4:]
    credential.rotated_at = _now()
    credential.last_verified_at = _now() if verify else credential.last_verified_at
    credential.last_verification_ok = True if verify else None
    return credential


def revoke(credential: AiCredential) -> AiCredential:
    credential.status = "revoked"
    credential.revoked_at = _now()
    credential.encrypted_key = ""   # wipe: the secret cannot be recovered from the database
    return credential


async def test_connection(credential: AiCredential, plain_key: str) -> bool:
    """Run the provider check; record the outcome. Returns True on success, raises AIError otherwise
    (after recording the failure)."""
    try:
        await verify_key(credential.provider, plain_key)
    except E.AIError:
        credential.last_verified_at = _now()
        credential.last_verification_ok = False
        raise
    credential.last_verified_at = _now()
    credential.last_verification_ok = True
    return True
