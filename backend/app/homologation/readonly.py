"""Runtime READ-ONLY guard for the Google Ads SDK client used in homologation.

The static tests prove no mutation call is WRITTEN in the reader; this proves none can HAPPEN: the SDK client is wrapped so that
only an allowlist of (service, method) pairs can be reached, and GAQL must be a plain ``SELECT``. Anything else raises
``MutationBlocked`` BEFORE any request is built. Applied to the harness and, via ``install()``, to the application's own client
factories during the agent tests.
"""
import re
from typing import Any, Callable, Dict, FrozenSet

ALLOWED: Dict[str, FrozenSet[str]] = {
    "GoogleAdsService": frozenset({"search", "search_stream", "language_constant_path", "geo_target_constant_path"}),
    "CustomerService": frozenset({"list_accessible_customers"}),
    "KeywordPlanIdeaService": frozenset({"generate_keyword_ideas"}),
}
_SELECT = re.compile(r"^\s*SELECT\b", re.I)


class MutationBlocked(Exception):
    pass


class _GuardedService:
    def __init__(self, name: str, service: Any):
        self._name, self._service = name, service

    def __getattr__(self, attr: str):
        if attr not in ALLOWED[self._name]:
            raise MutationBlocked(f"{self._name}.{attr} is not allowed in read-only homologation")
        target = getattr(self._service, attr)
        if attr in ("search", "search_stream"):
            def checked(*args, **kwargs):
                query = kwargs.get("query") if "query" in kwargs else (args[1] if len(args) > 1 else "")
                if not _SELECT.match(str(query or "")) or ";" in str(query):
                    raise MutationBlocked("only a single GAQL SELECT is allowed")
                return target(*args, **kwargs)
            return checked
        return target


class GuardedClient:
    def __init__(self, client: Any):
        self._client = client

    def get_service(self, name: str, *args, **kwargs):
        if name not in ALLOWED:
            raise MutationBlocked(f"service {name} is not allowed in read-only homologation")
        return _GuardedService(name, self._client.get_service(name, *args, **kwargs))

    def __getattr__(self, attr: str):   # get_type / enums are pure data builders
        if attr in ("get_type", "enums"):
            return getattr(self._client, attr)
        raise MutationBlocked(f"client.{attr} is not allowed in read-only homologation")


def guard_factory(factory: Callable[..., Any]) -> Callable[..., Any]:
    def guarded(*args, **kwargs):
        return GuardedClient(factory(*args, **kwargs))
    guarded.__wrapped_readonly__ = True  # type: ignore[attr-defined]
    return guarded


def install() -> Callable[[], None]:
    """Wrap every Google Ads client factory of the application. Returns an uninstall function."""
    from app.services import google_ads_reader as reader
    from app.services import google_ads_service as service
    originals = (reader.CLIENT_FACTORY, service._build_client)
    if not getattr(reader.CLIENT_FACTORY, "__wrapped_readonly__", False):
        reader.CLIENT_FACTORY = guard_factory(reader.CLIENT_FACTORY)
    if not getattr(service._build_client, "__wrapped_readonly__", False):
        service._build_client = guard_factory(service._build_client)

    def uninstall():
        reader.CLIENT_FACTORY, service._build_client = originals
    return uninstall
