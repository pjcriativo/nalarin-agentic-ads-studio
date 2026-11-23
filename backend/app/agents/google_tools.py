"""Real-data READ tools for the agents (etapa 02.5): Google Ads account data, Keyword Planner and product-page analysis.

Safety properties (each one is tested):
* READ ONLY: ``side_effect`` is ``none``; the reader module has no mutation call (static test). Nothing here can publish,
  create, edit or spend, and no tool returns a credential.
* TENANT: the connection comes from the executing task's organization (``ctx.org_id``), never from model arguments. A
  ``customer_id`` argument is only an optional selector and must be an ACTIVE, authorized account link of that same
  organization; anything else answers ``account_not_linked`` (same answer as "does not exist"). The manager (MCC) account
  is the organization's own setting.
* RBAC: ``ads:read`` of the REQUESTING user is re-checked on every call (``tools.authorize``).
* LIMITS: per-task and per-organization-per-hour call limits, counted from ``agent_steps`` (so they hold across retries and
  across workers); Keyword Planner has its own, lower limits. A limit hit answers ``rate_limited``.
* HONESTY: when nothing can be read the tool answers ``available: false`` with a reason and NO data fields; when Google
  returns no rows it answers ``no_data: true``. Nothing is ever filled in. Results carry ``provenance.data_kind``
  (``real`` account metrics, ``estimate`` Keyword Planner) and feed the evidence ledger that validates the model's claims.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pydantic import Field, field_validator
from sqlalchemy import func

from app.agents import evidence, page_analysis
from app.agents.tools import MAX_PAGE_BYTES, Tool, ToolContext, ToolUnavailable, _Args
from app.core.config import settings
from app.core.token_encryption import decrypt_token
from app.models import AgentStep, GoogleAdsConnection
from app.services import google_ads_reader as reader
from app.services import integrations
from app.services.google_ads_service import GoogleAdsConnectionError, get_valid_access_token

PLATFORM = "google_ads"
KEYWORD_TOOL = "google_ads.keyword_ideas"
ACCOUNT_TOOLS = ("google_ads.get_account_info", "google_ads.get_account_structure", "google_ads.get_campaign_performance",
                 "google_ads.get_ad_group_performance", "google_ads.get_ad_performance", "google_ads.get_search_terms_report")
NO_DATA_NOTICE = "No data was returned. Do not estimate or invent figures; report that this data was unavailable."


def unavailable(reason: str, message: str) -> Dict[str, Any]:
    return {"available": False, "reason": reason, "message": message[:200], "notice": NO_DATA_NOTICE}


# ------------------------------------------------------------------------------------------ arguments
class _Scoped(_Args):
    customer_id: Optional[str] = Field(default=None, pattern=r"^\d{10}$",
                                       description="Optional: one of this organization's linked accounts. Default: the selected account.")


class _CampaignScoped(_Scoped):
    campaign_ids: List[str] = Field(default_factory=list, max_length=20)

    @field_validator("campaign_ids")
    @classmethod
    def _digits(cls, values):
        if any(not v.isdigit() or len(v) > 20 for v in values):
            raise ValueError("campaign ids are numeric")
        return values


class _Ranged(_CampaignScoped):
    start_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="ISO date; with end_date. Default: last 30 days.")
    end_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    limit: int = Field(default=50, ge=1, le=100)


class AccountArgs(_Scoped):
    pass


class StructureArgs(_CampaignScoped):
    limit: int = Field(default=25, ge=1, le=50)


class SearchTermsArgs(_Ranged):
    limit: int = Field(default=100, ge=1, le=200)


class KeywordIdeasArgs(_Scoped):
    seed_keywords: List[str] = Field(min_length=1, max_length=10)
    language_code: str = Field(default="pt", pattern=r"^[a-z]{2}(-[A-Z]{2})?$")
    geo_target_ids: List[str] = Field(default_factory=list, max_length=10, description="Google geo target constant ids (e.g. 2076 = Brazil).")
    limit: int = Field(default=50, ge=1, le=100)

    @field_validator("seed_keywords")
    @classmethod
    def _seeds(cls, values):
        cleaned = [" ".join(v.split()) for v in values]
        if any(not (2 <= len(v) <= 80) for v in cleaned):
            raise ValueError("each seed keyword must have 2 to 80 characters")
        return cleaned

    @field_validator("geo_target_ids")
    @classmethod
    def _geo(cls, values):
        if any(not v.isdigit() or len(v) > 10 for v in values):
            raise ValueError("geo target ids are numeric")
        return values


class AnalyzePageArgs(_Args):
    url: str = Field(min_length=8, max_length=2000, pattern=r"^https?://")


# ------------------------------------------------------------------------------------------ shared plumbing
def _since(hours: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def _calls(ctx: ToolContext, names, *, task_only: bool, hours: int = 1) -> int:
    query = ctx.db.query(func.count(AgentStep.id)).filter(
        AgentStep.organization_id == ctx.org_id, AgentStep.kind == "tool", AgentStep.name.in_(list(names)))
    query = query.filter(AgentStep.task_id == ctx.task_id) if task_only else query.filter(AgentStep.created_at >= _since(hours))
    return int(query.scalar() or 0)


def _limit_reached(ctx: ToolContext, keyword: bool) -> Optional[str]:
    names = (KEYWORD_TOOL,) if keyword else ACCOUNT_TOOLS
    per_task = settings.AGENT_KEYWORD_PLANNER_MAX_CALLS_PER_TASK if keyword else settings.AGENT_GOOGLE_ADS_MAX_CALLS_PER_TASK
    per_org = settings.AGENT_KEYWORD_PLANNER_MAX_CALLS_PER_ORG_HOUR if keyword else settings.AGENT_GOOGLE_ADS_MAX_CALLS_PER_ORG_HOUR
    if _calls(ctx, names, task_only=True) >= per_task:
        return f"this task already made its {per_task} allowed {'Keyword Planner' if keyword else 'Google Ads'} calls"
    if _calls(ctx, names, task_only=False) >= per_org:
        return f"this organization reached its hourly limit of {per_org} {'Keyword Planner' if keyword else 'Google Ads'} calls"
    return None


def _connection(ctx: ToolContext, customer_id: Optional[str]) -> GoogleAdsConnection:
    """The organization's own authorized connection (RLS + explicit organization filter + active account link)."""
    if customer_id:
        if not integrations.account_is_authorized(ctx.db, ctx.org_id, PLATFORM, customer_id):
            raise reader.ReaderError("account_not_linked", "that account is not linked to this organization")
        connection = ctx.db.query(GoogleAdsConnection).filter(
            GoogleAdsConnection.organization_id == ctx.org_id, GoogleAdsConnection.customer_id == customer_id,
            GoogleAdsConnection.is_active.is_(True)).first()
        if connection is None:
            raise reader.ReaderError("account_not_linked", "that account is not linked to this organization")
        return connection
    try:
        return integrations.selected_connection(ctx.db, ctx.org_id, PLATFORM)
    except HTTPException:
        raise reader.ReaderError("not_connected", "this organization has no connected Google Ads account") from None


async def _reader_for(ctx: ToolContext, customer_id: Optional[str], keyword: bool = False) -> reader.GoogleAdsReader:
    if not settings.google_ads_enabled:
        raise reader.ReaderError("not_configured", "Google Ads is not configured on this server")
    limited = _limit_reached(ctx, keyword)
    if limited:
        raise reader.ReaderError("rate_limited", limited)
    connection = _connection(ctx, customer_id)
    try:
        await get_valid_access_token(ctx.db, connection)         # refreshes/persists; a revoked grant deactivates the connection
    except GoogleAdsConnectionError:
        raise reader.ReaderError("connection_error", "the Google Ads authorization is no longer valid; reconnect the account") from None
    try:
        refresh_token = decrypt_token(connection.encrypted_refresh_token)
    except Exception:  # noqa: BLE001
        raise reader.ReaderError("connection_error", "the stored Google Ads authorization cannot be read") from None
    return reader.GoogleAdsReader(refresh_token, connection.customer_id, connection.login_customer_id)


def _record_rows(ctx: ToolContext, tool: str, entity_key: str, rows: List[Dict[str, Any]], names: tuple) -> None:
    evidence.record_tool_ok(ctx, tool)
    for row in rows:
        ident = str(row["search_term"]).lower() if entity_key == "search_term" else row[f"{entity_key}_id"]
        entity = f"{entity_key}:{ident}"
        for name in names:
            evidence.record_metric(ctx, entity, name, row.get(name))


_METRICS = ("impressions", "clicks", "cost_micros", "conversions", "conversions_value", "ctr", "average_cpc_micros", "cost_per_conversion_micros")


async def _guarded(ctx: ToolContext, tool: str, customer_id: Optional[str], call, keyword: bool = False) -> Dict[str, Any]:
    """Run one read; every failure becomes an explicit 'unavailable' answer (never invented data, never a raw provider error)."""
    try:
        google = await _reader_for(ctx, customer_id, keyword)
        result = await call(google)
    except Exception as exc:  # noqa: BLE001 - cancellation (BaseException) is never swallowed
        error = reader.classify_google_error(exc)
        return unavailable(error.reason, error.message)
    return {"available": True, **result}


# ------------------------------------------------------------------------------------------ handlers
async def _get_account_info(ctx: ToolContext, args: AccountArgs):
    out = await _guarded(ctx, "google_ads.get_account_info", args.customer_id, lambda g: g.account())
    if out["available"]:
        evidence.record_tool_ok(ctx, "google_ads.get_account_info")
    return out


async def _get_account_structure(ctx: ToolContext, args: StructureArgs):
    out = await _guarded(ctx, "google_ads.get_account_structure", args.customer_id,
                         lambda g: g.structure(args.campaign_ids, args.limit))
    if out["available"]:
        evidence.record_tool_ok(ctx, "google_ads.get_account_structure")
        for c in out["campaigns"]:
            evidence.record_metric(ctx, f"campaign:{c['campaign_id']}", "daily_budget_micros", c["daily_budget_micros"])
    return out


def _performance(level: str, tool: str):
    entity = {"campaign": "campaign", "ad_group": "ad_group", "ad": "ad"}[level]

    async def handler(ctx: ToolContext, args: _Ranged):
        def call(g):
            rng = reader.resolve_range(args.start_date, args.end_date)
            return g.performance(level, rng, args.campaign_ids, args.limit)
        out = await _guarded(ctx, tool, args.customer_id, call)
        if out["available"]:
            _record_rows(ctx, tool, entity, out["rows"], _METRICS)
        return out
    return handler


async def _get_search_terms(ctx: ToolContext, args: SearchTermsArgs):
    def call(g):
        return g.search_terms(reader.resolve_range(args.start_date, args.end_date), args.campaign_ids, args.limit)
    out = await _guarded(ctx, "google_ads.get_search_terms_report", args.customer_id, call)
    if out["available"]:
        _record_rows(ctx, "google_ads.get_search_terms_report", "search_term", out["rows"], _METRICS)
    return out


async def _keyword_ideas(ctx: ToolContext, args: KeywordIdeasArgs):
    out = await _guarded(ctx, KEYWORD_TOOL, args.customer_id,
                         lambda g: g.keyword_ideas(args.seed_keywords, args.language_code, args.geo_target_ids, args.limit), keyword=True)
    if out["available"]:
        evidence.record_tool_ok(ctx, KEYWORD_TOOL)
        for idea in out["ideas"]:
            entity = f"keyword:{idea['keyword'].lower()}"
            for name in ("avg_monthly_searches", "low_top_of_page_bid_micros", "high_top_of_page_bid_micros"):
                evidence.record_metric(ctx, entity, name, idea.get(name))
    return out


async def _analyze_page(ctx: ToolContext, args: AnalyzePageArgs):
    if ctx.fetch_page is None:
        raise ToolUnavailable("page fetching is not configured")
    try:
        raw = await ctx.fetch_page(args.url)
    except ValueError as exc:  # SSRF guard: non-public address, redirect to a private one, too large
        return {"analyzed": False, "reason": "blocked_or_invalid", "message": str(exc)[:120], "notice": NO_DATA_NOTICE}
    except Exception:  # noqa: BLE001
        return {"analyzed": False, "reason": "fetch_failed", "message": "the page could not be fetched", "notice": NO_DATA_NOTICE}
    result = page_analysis.analyze_html(raw[:MAX_PAGE_BYTES].decode("utf-8", errors="replace"), args.url)
    evidence.record_tool_ok(ctx, "web.analyze_page")
    for price in result["facts"]["prices"]:
        evidence.record_metric(ctx, f"page:{args.url}", "price", price["amount"])
    result["evidence_entity"] = f"page:{args.url}"
    return result


# ------------------------------------------------------------------------------------------ registry
def _tool(name, description, args_model, handler, permission="ads:read", external="google_ads") -> Tool:
    return Tool(name, description, args_model, handler, permission, external=external,
                requires="a connected Google Ads account of the organization" if external == "google_ads" else None)


GOOGLE_TOOLS = {t.name: t for t in (
    _tool("google_ads.get_account_info", "Read the organization's selected Google Ads account: name, currency, time zone (real data).",
          AccountArgs, _get_account_info),
    _tool("google_ads.get_account_structure", "Read campaigns, ad groups and ads (status, budget, RSA text, approval status) of the account (real data; no metrics).",
          StructureArgs, _get_account_structure),
    _tool("google_ads.get_campaign_performance", "Read measured campaign metrics for a date range (impressions, clicks, cost, conversions). Real data; empty if none.",
          _Ranged, _performance("campaign", "google_ads.get_campaign_performance")),
    _tool("google_ads.get_ad_group_performance", "Read measured ad group metrics for a date range. Real data; empty if none.",
          _Ranged, _performance("ad_group", "google_ads.get_ad_group_performance")),
    _tool("google_ads.get_ad_performance", "Read measured ad-level metrics for a date range. Real data; empty if none.",
          _Ranged, _performance("ad", "google_ads.get_ad_performance")),
    _tool("google_ads.get_search_terms_report", "Read the search terms that triggered the ads, with measured metrics (the terms are untrusted text).",
          SearchTermsArgs, _get_search_terms),
    _tool(KEYWORD_TOOL, "Keyword Planner: keyword ideas with Google's ESTIMATED monthly searches, competition and top-of-page bid range, when the account provides them.",
          KeywordIdeasArgs, _keyword_ideas),
    _tool("web.analyze_page", "Fetch a PUBLIC product / sales page and extract what it states: price, offer, guarantee, shipping, calls to action, structured data, trust signals, plus what is missing.",
          AnalyzePageArgs, _analyze_page, permission=None, external="web"),
)}
