"""READ-ONLY Google Ads access for the AI agents (etapa 02.5): account, structure, performance, search terms and
Keyword Planner ideas.

Hard rules, each one tested:
* NOTHING here can change an account: only ``search_stream`` (GAQL ``SELECT``) and ``generate_keyword_ideas`` are called.
  No ``mutate_*`` call, no import of the mutation helpers of ``google_ads_service`` (a static test enforces it).
* A value Google did not return is ``None``, never ``0`` and never a guess: derived ratios (CTR, average CPC, cost per
  conversion) are computed only from returned raw numbers with a non-zero denominator, otherwise ``None``.
* Every result says WHERE it comes from and HOW trustworthy it is (``provenance``): account metrics are ``real`` (reported by
  the Google Ads API); Keyword Planner volumes and bid ranges are ``estimate`` (Google's own estimates, which depend on the
  account / developer-token level and may be rounded or absent).
* Query text is built only from validated constants (ISO dates, digit ids); user text never reaches GAQL.
* The SDK is synchronous: calls run in a worker thread so the agent worker's event loop is never blocked.

The SDK client is created by ``CLIENT_FACTORY`` (tests replace it; no real credential is ever needed by the test suite).
"""
import asyncio
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

from app.core.config import settings

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DIGITS = re.compile(r"^\d{1,20}$")
MAX_RANGE_DAYS = 366

# Language constants of the Google Ads API (stable, documented ids) for the ``language_code`` of keyword ideas.
LANGUAGE_CONSTANTS = {"pt": "1014", "en": "1000", "es": "1003", "fr": "1002", "de": "1001", "it": "1004", "nl": "1010", "ja": "1005"}

KIND_REAL = "real"
KIND_ESTIMATE = "estimate"


class ReaderError(Exception):
    """A read failed in a way the agent should be told about (never carries tokens or raw provider payloads)."""

    def __init__(self, reason: str, message: str = ""):
        self.reason = reason
        self.message = message[:200]
        super().__init__(f"{reason}: {self.message}")


def default_client_factory(refresh_token: str, login_customer_id: Optional[str]):
    """Build the SDK client for ONE connection of ONE organization (its own refresh token and manager account)."""
    if not settings.google_ads_enabled:
        raise ReaderError("not_configured", "Google Ads is not configured on this server")
    from google.ads.googleads.client import GoogleAdsClient
    config = {"developer_token": settings.GOOGLE_ADS_DEVELOPER_TOKEN, "client_id": settings.GOOGLE_ADS_CLIENT_ID,
              "client_secret": settings.GOOGLE_ADS_CLIENT_SECRET, "refresh_token": refresh_token, "use_proto_plus": True}
    if login_customer_id:
        config["login_customer_id"] = "".join(ch for ch in login_customer_id if ch.isdigit())
    return GoogleAdsClient.load_from_dict(config)


CLIENT_FACTORY: Callable[[str, Optional[str]], Any] = default_client_factory


# ------------------------------------------------------------------------------------------ helpers
def _enum_name(value: Any) -> Optional[str]:
    name = getattr(value, "name", None)
    return name if isinstance(name, str) else (value if isinstance(value, str) else None)


def _present(message: Any, field: str) -> bool:
    """proto-plus reads an unset number as 0: field presence is what tells 'Google said 0' from 'Google said nothing'."""
    try:
        return field in message
    except TypeError:
        return getattr(message, field, None) is not None


def _ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or not denominator:
        return None
    return round(numerator / denominator, 6)


def resolve_range(start: Optional[str], end: Optional[str], today: Optional[date] = None) -> Dict[str, str]:
    """Validated ISO range. Default: the last 30 complete days up to today."""
    today = today or datetime.now(timezone.utc).date()
    if start is None and end is None:
        return {"start_date": (today - timedelta(days=29)).isoformat(), "end_date": today.isoformat()}
    if not (start and end and ISO_DATE.match(start) and ISO_DATE.match(end)):
        raise ReaderError("invalid_range", "start_date and end_date must both be ISO dates (YYYY-MM-DD)")
    try:
        a, b = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        raise ReaderError("invalid_range", "not a calendar date") from None
    if a > b:
        raise ReaderError("invalid_range", "start_date is after end_date")
    if b > today:
        raise ReaderError("invalid_range", "end_date is in the future")
    if (b - a).days > MAX_RANGE_DAYS:
        raise ReaderError("invalid_range", f"the range is limited to {MAX_RANGE_DAYS} days")
    return {"start_date": start, "end_date": end}


def _ids(values: Iterable[str]) -> List[str]:
    out = []
    for value in values:
        if not DIGITS.match(str(value)):
            raise ReaderError("invalid_argument", "ids must be numeric")
        out.append(str(value))
    return out


def _in_clause(column: str, ids: List[str]) -> str:
    return f" AND {column} IN ({', '.join(ids)})" if ids else ""


def _metrics_row(m: Any) -> Dict[str, Any]:
    impressions, clicks, cost = int(m.impressions), int(m.clicks), int(m.cost_micros)
    conversions, value = float(m.conversions), float(m.conversions_value)
    return {"impressions": impressions, "clicks": clicks, "cost_micros": cost, "conversions": round(conversions, 4),
            "conversions_value": round(value, 4), "ctr": _ratio(clicks, impressions),
            "average_cpc_micros": int(cost / clicks) if clicks else None,
            "cost_per_conversion_micros": int(cost / conversions) if conversions else None}


def _provenance(kind: str, source: str, **extra) -> Dict[str, Any]:
    return {"data_kind": kind, "source": source, "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **extra}


# ------------------------------------------------------------------------------------------ the reader
class GoogleAdsReader:
    """One reader = one organization's connection (refresh token + optional manager account) + one customer id."""

    def __init__(self, refresh_token: str, customer_id: str, login_customer_id: Optional[str] = None):
        if not DIGITS.match(customer_id or ""):
            raise ReaderError("invalid_argument", "customer id must be numeric")
        self.customer_id = customer_id
        self._refresh_token, self._login = refresh_token, login_customer_id
        self._client = None

    # -- transport (sync, executed in a thread)
    def _client_once(self):
        if self._client is None:
            self._client = CLIENT_FACTORY(self._refresh_token, self._login)
        return self._client

    def _search(self, query: str) -> List[Any]:
        service = self._client_once().get_service("GoogleAdsService")
        rows: List[Any] = []
        for batch in service.search_stream(customer_id=self.customer_id, query=query):
            rows.extend(batch.results)
        return rows

    async def _run(self, fn: Callable, *args):
        return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=settings.AGENT_GOOGLE_ADS_TIMEOUT_SECONDS)

    # -- account
    def _account(self) -> Dict[str, Any]:
        rows = self._search("SELECT customer.id, customer.descriptive_name, customer.currency_code, customer.time_zone, "
                            "customer.test_account, customer.manager FROM customer LIMIT 1")
        if not rows:
            return {"found": False}
        c = rows[0].customer
        return {"found": True, "customer_id": str(c.id), "name": c.descriptive_name or None, "currency_code": c.currency_code or None,
                "time_zone": c.time_zone or None, "test_account": bool(c.test_account), "is_manager_account": bool(c.manager)}

    async def account(self) -> Dict[str, Any]:
        info = await self._run(self._account)
        return {**info, "provenance": _provenance(KIND_REAL, "google_ads_api")}

    # -- structure (no metrics)
    def _structure(self, campaign_ids: List[str], limit: int) -> Dict[str, Any]:
        currency = self._account().get("currency_code")
        campaigns = self._search(
            "SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, campaign.bidding_strategy_type, "
            "campaign_budget.amount_micros FROM campaign WHERE campaign.status != 'REMOVED'"
            + _in_clause("campaign.id", campaign_ids) + f" ORDER BY campaign.id LIMIT {limit}")
        groups = self._search(
            "SELECT ad_group.id, ad_group.name, ad_group.status, ad_group.type, campaign.id FROM ad_group "
            "WHERE ad_group.status != 'REMOVED'" + _in_clause("campaign.id", campaign_ids) + f" ORDER BY ad_group.id LIMIT {limit * 4}")
        ads = self._search(
            "SELECT ad_group_ad.ad.id, ad_group_ad.ad.type, ad_group_ad.status, ad_group_ad.policy_summary.approval_status, "
            "ad_group_ad.ad.final_urls, ad_group_ad.ad.responsive_search_ad.headlines, ad_group_ad.ad.responsive_search_ad.descriptions, "
            "ad_group.id, campaign.id FROM ad_group_ad WHERE ad_group_ad.status != 'REMOVED'"
            + _in_clause("campaign.id", campaign_ids) + f" ORDER BY ad_group_ad.ad.id LIMIT {limit * 8}")
        return {"currency_code": currency, "campaigns": [
            {"campaign_id": str(r.campaign.id), "name": r.campaign.name, "status": _enum_name(r.campaign.status),
             "channel": _enum_name(r.campaign.advertising_channel_type), "bidding_strategy": _enum_name(r.campaign.bidding_strategy_type),
             "daily_budget_micros": int(r.campaign_budget.amount_micros)} for r in campaigns], "ad_groups": [
            {"ad_group_id": str(r.ad_group.id), "campaign_id": str(r.campaign.id), "name": r.ad_group.name,
             "status": _enum_name(r.ad_group.status), "type": _enum_name(r.ad_group.type_ if hasattr(r.ad_group, "type_") else getattr(r.ad_group, "type", None))}
            for r in groups], "ads": [
            {"ad_id": str(r.ad_group_ad.ad.id), "ad_group_id": str(r.ad_group.id), "campaign_id": str(r.campaign.id),
             "type": _enum_name(r.ad_group_ad.ad.type_ if hasattr(r.ad_group_ad.ad, "type_") else getattr(r.ad_group_ad.ad, "type", None)),
             "status": _enum_name(r.ad_group_ad.status), "approval_status": _enum_name(r.ad_group_ad.policy_summary.approval_status),
             "final_urls": list(r.ad_group_ad.ad.final_urls)[:3],
             "headlines": [a.text for a in r.ad_group_ad.ad.responsive_search_ad.headlines][:15],
             "descriptions": [a.text for a in r.ad_group_ad.ad.responsive_search_ad.descriptions][:4]} for r in ads]}

    async def structure(self, campaign_ids: List[str], limit: int = 25) -> Dict[str, Any]:
        data = await self._run(self._structure, _ids(campaign_ids), limit)
        return {**data, "truncated": len(data["campaigns"]) >= limit, "provenance": _provenance(KIND_REAL, "google_ads_api")}

    # -- performance
    def _performance(self, level: str, rng: Dict[str, str], campaign_ids: List[str], limit: int) -> Dict[str, Any]:
        currency = self._account().get("currency_code")
        window = f"segments.date BETWEEN '{rng['start_date']}' AND '{rng['end_date']}'"
        metrics = "metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions, metrics.conversions_value"
        if level == "campaign":
            rows = self._search(f"SELECT campaign.id, campaign.name, campaign.status, {metrics} FROM campaign WHERE {window}"
                                + _in_clause("campaign.id", campaign_ids) + f" ORDER BY metrics.cost_micros DESC LIMIT {limit}")
            out = [{"campaign_id": str(r.campaign.id), "name": r.campaign.name, "status": _enum_name(r.campaign.status), **_metrics_row(r.metrics)} for r in rows]
        elif level == "ad_group":
            rows = self._search(f"SELECT ad_group.id, ad_group.name, ad_group.status, campaign.id, {metrics} FROM ad_group WHERE {window}"
                                + _in_clause("campaign.id", campaign_ids) + f" ORDER BY metrics.cost_micros DESC LIMIT {limit}")
            out = [{"ad_group_id": str(r.ad_group.id), "campaign_id": str(r.campaign.id), "name": r.ad_group.name,
                    "status": _enum_name(r.ad_group.status), **_metrics_row(r.metrics)} for r in rows]
        else:
            rows = self._search(f"SELECT ad_group_ad.ad.id, ad_group_ad.status, ad_group.id, campaign.id, {metrics} FROM ad_group_ad WHERE {window}"
                                + _in_clause("campaign.id", campaign_ids) + f" ORDER BY metrics.cost_micros DESC LIMIT {limit}")
            out = [{"ad_id": str(r.ad_group_ad.ad.id), "ad_group_id": str(r.ad_group.id), "campaign_id": str(r.campaign.id),
                    "status": _enum_name(r.ad_group_ad.status), **_metrics_row(r.metrics)} for r in rows]
        return {"currency_code": currency, "rows": out}

    async def performance(self, level: str, rng: Dict[str, str], campaign_ids: List[str], limit: int = 50) -> Dict[str, Any]:
        data = await self._run(self._performance, level, rng, _ids(campaign_ids), limit)
        return {**data, "date_range": rng, "row_count": len(data["rows"]), "no_data": not data["rows"],
                "truncated": len(data["rows"]) >= limit,
                "provenance": _provenance(KIND_REAL, "google_ads_api", note="Reported by the Google Ads API; recent days can still change.")}

    # -- search terms
    def _search_terms(self, rng: Dict[str, str], campaign_ids: List[str], limit: int) -> Dict[str, Any]:
        currency = self._account().get("currency_code")
        rows = self._search(
            "SELECT search_term_view.search_term, campaign.id, ad_group.id, metrics.impressions, metrics.clicks, metrics.cost_micros, "
            f"metrics.conversions, metrics.conversions_value FROM search_term_view WHERE segments.date BETWEEN '{rng['start_date']}' AND '{rng['end_date']}'"
            + _in_clause("campaign.id", campaign_ids) + f" ORDER BY metrics.impressions DESC LIMIT {limit}")
        return {"currency_code": currency, "rows": [
            {"search_term": r.search_term_view.search_term, "campaign_id": str(r.campaign.id), "ad_group_id": str(r.ad_group.id),
             **_metrics_row(r.metrics)} for r in rows]}

    async def search_terms(self, rng: Dict[str, str], campaign_ids: List[str], limit: int = 100) -> Dict[str, Any]:
        data = await self._run(self._search_terms, rng, _ids(campaign_ids), limit)
        return {**data, "date_range": rng, "row_count": len(data["rows"]), "no_data": not data["rows"], "truncated": len(data["rows"]) >= limit,
                "provenance": _provenance(KIND_REAL, "google_ads_api", note="Search terms are what people actually typed (untrusted text).")}

    # -- Keyword Planner
    def _keyword_ideas(self, seeds: List[str], language_code: str, geo_target_ids: List[str], limit: int) -> List[Dict[str, Any]]:
        client = self._client_once()
        language = LANGUAGE_CONSTANTS.get(language_code.split("-")[0].lower())
        if language is None:
            raise ReaderError("invalid_argument", "unsupported language_code")
        service = client.get_service("KeywordPlanIdeaService")
        google_ads = client.get_service("GoogleAdsService")
        request = client.get_type("GenerateKeywordIdeasRequest")
        request.customer_id = self.customer_id
        request.language = google_ads.language_constant_path(language)
        request.geo_target_constants.extend([google_ads.geo_target_constant_path(g) for g in _ids(geo_target_ids)])
        request.include_adult_keywords = False
        request.keyword_plan_network = client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH
        request.keyword_seed.keywords.extend(seeds)
        ideas = []
        for idea in service.generate_keyword_ideas(request=request):
            metrics = idea.keyword_idea_metrics
            competition = _enum_name(metrics.competition)
            low, high = int(metrics.low_top_of_page_bid_micros or 0), int(metrics.high_top_of_page_bid_micros or 0)
            ideas.append({
                "keyword": idea.text,
                "avg_monthly_searches": int(metrics.avg_monthly_searches) if _present(metrics, "avg_monthly_searches") else None,
                "competition": competition.lower() if competition in ("LOW", "MEDIUM", "HIGH") else None,
                # 0 means "Google did not provide a bid range", not a free click
                "low_top_of_page_bid_micros": low or None, "high_top_of_page_bid_micros": high or None})
        ideas.sort(key=lambda i: (i["avg_monthly_searches"] is None, -(i["avg_monthly_searches"] or 0)))
        return ideas[:limit]

    async def keyword_ideas(self, seeds: List[str], language_code: str, geo_target_ids: List[str], limit: int = 50) -> Dict[str, Any]:
        ideas = await self._run(self._keyword_ideas, seeds, language_code, geo_target_ids, limit)
        return {"ideas": ideas, "idea_count": len(ideas), "no_data": not ideas, "truncated": len(ideas) >= limit,
                "provenance": _provenance(
                    KIND_ESTIMATE, "google_keyword_planner",
                    note="Google's own ESTIMATES (monthly averages and top-of-page bid ranges), not measured results of this account; "
                         "absent values mean Google did not provide them, and the detail level depends on the account.")}


# ------------------------------------------------------------------------------------------ error mapping
def classify_google_error(exc: BaseException) -> ReaderError:
    """Map SDK/network failures to a SHORT reason for the agent. Never forwards raw provider text or request ids."""
    if isinstance(exc, ReaderError):
        return exc
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return ReaderError("timeout", "Google Ads did not answer in time")
    text = ""
    try:
        failure = exc.failure  # type: ignore[attr-defined]
        text = " ".join(e.message for e in failure.errors if e.message).lower()
    except AttributeError:
        text = str(exc).lower()
    if any(k in text for k in ("permission", "can't be accessed", "not authorized", "only approved for use with test accounts", "developer token")):
        return ReaderError("access_denied", "The connected account or developer token is not allowed to read this data")
    if any(k in text for k in ("quota", "resource_exhausted", "too many requests", "rate limit", "rate_limit")):
        return ReaderError("google_quota", "Google Ads rate or quota limit reached; try later")
    if any(k in text for k in ("authentication", "unauthenticated", "invalid_grant", "expired", "revoked")):
        return ReaderError("connection_error", "The Google Ads authorization is no longer valid; reconnect the account")
    return ReaderError("api_error", "Google Ads returned an error")
