"""Safety rails of the homologation runner: authorization, allowlist, cost ledger, redaction, report.

* AUTHORIZATION: real calls need ``HOMOLOG_AUTHORIZATION == AUTHORIZATION_PHRASE`` in the environment AND ``--execute``.
* ALLOWLIST: only operations declared in ``operations.OPERATIONS`` can run; each is read-only on the Google side
  (GAQL SELECT, Keyword Planner ideas, OAuth refresh, list accessible customers) and a tiny fixed prompt on the AI side.
* COST: every paid call is priced BEFORE it is sent (worst case = estimated prompt + the full output allowance, using the
  operator-supplied price). A call that would cross the per-operation or the total cap is refused, never sent.
* REDACTION: every string that reaches a report/log passes ``Redactor`` (exact secret values + token-shaped patterns).
  Reports hold statuses, counts, tokens and costs: never prompts, completions, keys, tokens or account data.
"""
import json
import math
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

AUTHORIZATION_PHRASE = "I_AUTHORIZE_REAL_CALLS_PHASE_B"
AUTH_ENV = "HOMOLOG_AUTHORIZATION"

# PROVISIONAL aggregate ceiling for the WHOLE of Phase B (harness + agent tests), approved only as a ceiling: nothing runs
# without the explicit authorization. It is a hard clamp: a larger --max-total-usd is lowered to it.
HARD_MAX_TOTAL_USD = 1.00
DEFAULT_MAX_TOTAL_USD = HARD_MAX_TOTAL_USD
DEFAULT_MAX_OP_USD = 0.05
LEDGER_ENV = "HOMOLOG_LEDGER_FILE"

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),                  # OpenAI / Anthropic style keys
    re.compile(r"AIza[0-9A-Za-z_\-]{10,}"),                # Google API keys
    re.compile(r"ya29\.[0-9A-Za-z_\-]{10,}"),              # Google OAuth access tokens
    re.compile(r"1//[0-9A-Za-z_\-]{10,}"),                 # Google OAuth refresh tokens
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|authorization)(\"?\s*[:=]\s*\"?)[^\s\",}]{6,}"),
]


class Redactor:
    """Masks exact secret values given at runtime plus token-shaped strings."""

    def __init__(self, secrets: Iterable[str] = ()):
        self._secrets = sorted({s for s in secrets if s and len(s) >= 6}, key=len, reverse=True)

    def add(self, *secrets: str) -> None:
        self.__init__([*self._secrets, *secrets])  # type: ignore[misc]

    def __call__(self, text) -> str:
        out = str(text)
        for secret in self._secrets:
            out = out.replace(secret, "[REDACTED]")
        for pattern in _SECRET_PATTERNS:
            out = pattern.sub(lambda m: "[REDACTED]" if m.lastindex is None else f"{m.group(1)}{m.group(2)}[REDACTED]", out)
        return out


class NotAuthorized(Exception):
    pass


class CostCapExceeded(Exception):
    pass


def authorized(env: Optional[Dict[str, str]] = None) -> bool:
    return (env if env is not None else os.environ).get(AUTH_ENV, "") == AUTHORIZATION_PHRASE


def require_authorization(execute: bool, env: Optional[Dict[str, str]] = None) -> None:
    if not execute:
        raise NotAuthorized("--execute was not given: nothing is sent (use `plan` to inspect)")
    if not authorized(env):
        raise NotAuthorized(f"{AUTH_ENV} does not hold the authorization phrase: real calls are not authorized")


def usd_to_micros(usd: float) -> int:
    return int(round(usd * 1_000_000))


def default_ledger_path(env: Optional[Dict[str, str]] = None) -> str:
    env = env if env is not None else os.environ
    return env.get(LEDGER_ENV) or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                               ".homolog-ledger.json")


@dataclass
class CostLedger:
    """ESTIMATED-spend budget for the WHOLE Phase B (USD micros), shared by the harness and the agent tests (possibly in
    different processes) through a small JSON file (numbers only, git-ignored). The cap is clamped to HARD_MAX_TOTAL_USD.

    It is a budget of what WE estimate/measure, not a guarantee of the maximum the provider can bill (see the doc, 11.7).

    Concurrency: every read-modify-write runs under an exclusive OS file lock (+ a thread lock), and a paid call must first
    ``reserve`` its worst case: the amount is deducted from what is available BEFORE anything is sent, so two processes can
    never both spend the same remainder. ``settle`` replaces the reservation by the real cost, or by the whole reservation
    when the cost is unknown (interrupted call, error after sending, missing usage). A reservation left behind by a process
    that died stays counted (conservative) until the ledger file is deleted on purpose."""
    max_total_micros: int = usd_to_micros(DEFAULT_MAX_TOTAL_USD)
    max_op_micros: int = usd_to_micros(DEFAULT_MAX_OP_USD)
    spent_micros: int = 0
    reservations: Dict[str, int] = field(default_factory=dict)
    path: Optional[str] = None
    _tlock: object = field(default_factory=threading.Lock, repr=False, compare=False)

    LOCK_TIMEOUT_SECONDS = 30.0

    def __post_init__(self):
        self.max_total_micros = min(self.max_total_micros, usd_to_micros(HARD_MAX_TOTAL_USD))
        self._read()

    @classmethod
    def shared(cls, env: Optional[Dict[str, str]] = None, max_total_usd: float = DEFAULT_MAX_TOTAL_USD,
               max_op_usd: float = DEFAULT_MAX_OP_USD) -> "CostLedger":
        return cls(usd_to_micros(max_total_usd), usd_to_micros(max_op_usd), path=default_ledger_path(env))

    # ---- persistence (always inside _transaction, except the read-only properties) ----------------------
    def _read(self) -> None:
        if self.path and os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                raise CostCapExceeded("ledger file unreadable: refusing to send anything (delete it on purpose to reset)")
            self.spent_micros = max(int(data.get("spent_micros", 0)), 0)
            self.reservations = {str(k): int(v) for k, v in (data.get("reservations") or {}).items()}

    def _save(self) -> None:
        if not self.path:
            return
        tmp = f"{self.path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"spent_micros": self.spent_micros, "reservations": self.reservations}, fh)
        os.replace(tmp, self.path)

    @contextmanager
    def _transaction(self):
        with self._tlock:
            if not self.path:
                yield
                return
            handle = open(self.path + ".lock", "a+b")
            try:
                handle.write(b"0")
                handle.flush()
                deadline = time.monotonic() + self.LOCK_TIMEOUT_SECONDS
                while True:
                    try:
                        if os.name == "nt":
                            import msvcrt
                            handle.seek(0)
                            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError:
                        if time.monotonic() > deadline:
                            raise CostCapExceeded("ledger lock timeout: refusing to send anything")
                        time.sleep(0.02)
                try:
                    self._read()
                    yield
                    self._save()
                finally:
                    if os.name == "nt":
                        import msvcrt
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    # ---- accounting -------------------------------------------------------------------------------------
    @property
    def reserved_micros(self) -> int:
        return sum(self.reservations.values())

    @property
    def remaining_micros(self) -> int:
        """What can still be reserved: ceiling - settled spend - open reservations (re-read from the file)."""
        self._read()
        return max(self.max_total_micros - self.spent_micros - self.reserved_micros, 0)

    def reserve(self, worst_case_micros: Optional[int], label: str = "operation", enforce_op_cap: bool = True) -> str:
        """Atomically set aside the worst case BEFORE sending. Raises CostCapExceeded (nothing is sent) when the price is
        unknown, the per-operation cap is crossed, or settled spend + open reservations + this amount > the ceiling."""
        if worst_case_micros is None:
            raise CostCapExceeded(f"{label}: no price supplied for the model, so the cost cap cannot be enforced (refused)")
        if enforce_op_cap and worst_case_micros > self.max_op_micros:
            raise CostCapExceeded(f"{label}: worst case {worst_case_micros} > per-operation cap {self.max_op_micros} micros")
        with self._transaction():
            if self.spent_micros + self.reserved_micros + worst_case_micros > self.max_total_micros:
                raise CostCapExceeded(f"{label}: would exceed the total cap ({self.max_total_micros} micros)")
            token = uuid.uuid4().hex
            self.reservations[token] = int(worst_case_micros)
            return token

    def settle(self, token: str, actual_micros: Optional[int]) -> int:
        """Turn a reservation into spend: the real cost, or the WHOLE reservation when the cost is unknown (never
        under-count). A real cost above the reservation is recorded as it is."""
        with self._transaction():
            reserved = self.reservations.pop(token, 0)
            amount = int(actual_micros) if actual_micros is not None else reserved
            self.spent_micros += max(amount, 0)
            return amount

    def release(self, token: str) -> None:
        """Give a reservation back. ONLY for failures that happened before anything was sent."""
        with self._transaction():
            self.reservations.pop(token, None)

    def add_external(self, micros: int) -> None:
        """Account spending measured elsewhere, without a prior reservation."""
        with self._transaction():
            self.spent_micros += max(int(micros), 0)


def worst_case_micros(prompt_chars: int, max_output_tokens: int, in_price: Optional[int], out_price: Optional[int]) -> Optional[int]:
    """Same conservative arithmetic as app.ai.pricing (3 chars/token, +16, rounded up)."""
    if in_price is None or out_price is None:
        return None
    prompt_tokens = math.ceil(prompt_chars / 3) + 16
    return math.ceil(prompt_tokens * in_price / 1_000_000) + math.ceil(max_output_tokens * out_price / 1_000_000)


# ---- report -------------------------------------------------------------------------------------------
PASSED, FAILED, NOT_TESTED = "approved", "failed", "not_tested"


@dataclass
class OpResult:
    op_id: str
    system: str
    status: str                      # approved | failed | not_tested
    detail: str = ""                 # short, redacted, no payloads
    latency_ms: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_micros: Optional[int] = None
    checks: Dict[str, bool] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v not in (None, "", {})}


# ---- leak scan --------------------------------------------------------------------------------------
SECRET_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "REFRESH", "PASSWORD", "AUTHORIZATION")


def secret_values(env: Dict[str, str]) -> List[str]:
    return [v for k, v in env.items() if v and len(v) >= 8 and any(t in k.upper() for t in SECRET_ENV_MARKERS)
            and k != AUTH_ENV]


def scan_for_secrets(paths: Iterable[str], env: Dict[str, str], patterns: bool = True) -> List[str]:
    """Names (never values) of files that contain a configured secret value or a token-shaped string."""
    secrets, hits = secret_values(env), []
    for root in paths:
        files = [root] if os.path.isfile(root) else [os.path.join(d, f) for d, ds, fs in os.walk(root)
                                                     if "__pycache__" not in d and "node_modules" not in d for f in fs
                                                     if not f.endswith((".pyc", ".png", ".jpg"))]
        for path in files:
            try:
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
            except OSError:
                continue
            if any(sec in text for sec in secrets) or (patterns and any(p.search(text) for p in _SECRET_PATTERNS[:5])):
                hits.append(path)
    return hits
