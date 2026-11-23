"""Test doubles for the real-data agent tools (etapa 02.5). NOTHING here reaches Google: ``FakeGoogle`` replaces the Google Ads
SDK client factory with objects shaped like the SDK's (``row.campaign.id``, ``row.metrics.clicks``, enums with ``.name`` ...),
so the real parsing/guard code runs; it is NOT evidence that the real API behaves this way (see docs/etapa-02-05-agent-tools.md)."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from typing import Any, Dict, List, Optional

from app.core.token_encryption import encrypt_token
from app.models import GoogleAdsConnection


def enum(name):
    return NS(name=name)


def metrics(impressions=0, clicks=0, cost_micros=0, conversions=0.0, conversions_value=0.0):
    return NS(impressions=impressions, clicks=clicks, cost_micros=cost_micros, conversions=conversions, conversions_value=conversions_value)


def campaign_row(cid, name, status="ENABLED", **m):
    return NS(campaign=NS(id=int(cid), name=name, status=enum(status)), metrics=metrics(**m))


def ad_group_row(gid, cid, name, status="ENABLED", **m):
    return NS(ad_group=NS(id=int(gid), name=name, status=enum(status), type_=enum("SEARCH_STANDARD")), campaign=NS(id=int(cid)), metrics=metrics(**m))


def ad_row(aid, gid, cid, status="ENABLED", **m):
    return NS(ad_group_ad=NS(ad=NS(id=int(aid)), status=enum(status)), ad_group=NS(id=int(gid)), campaign=NS(id=int(cid)), metrics=metrics(**m))


def term_row(term, cid, gid, **m):
    return NS(search_term_view=NS(search_term=term), campaign=NS(id=int(cid)), ad_group=NS(id=int(gid)), metrics=metrics(**m))


def idea(text, avg=None, competition="UNSPECIFIED", low=0, high=0):
    return NS(text=text, keyword_idea_metrics=NS(avg_monthly_searches=avg, competition=enum(competition),
                                                 low_top_of_page_bid_micros=low, high_top_of_page_bid_micros=high))


class GoogleError(Exception):
    """Shaped like google.ads.googleads.errors.GoogleAdsException (``failure.errors[].message``)."""

    def __init__(self, message):
        super().__init__(f"raw provider repr with request_id=abc123 and refresh-A-secret ({message})")
        self.failure = NS(errors=[NS(message=message)])


class Account:
    """One Google Ads customer's data."""

    def __init__(self, customer_id, name="Acme Ads", currency="BRL", campaigns=None, groups=None, ads=None, perf_campaigns=None,
                 perf_groups=None, perf_ads=None, terms=None, ideas=None, structure_ads=None):
        self.customer_id, self.name, self.currency = customer_id, name, currency
        self.campaigns = campaigns or []                 # structure rows
        self.groups = groups or []
        self.structure_ads = structure_ads or []
        self.perf = {"campaign": perf_campaigns or [], "ad_group": perf_groups or [], "ad": perf_ads or []}
        self.terms = terms or []
        self.ideas = ideas or []


class FakeGoogle:
    """The whole fake Google: accounts by customer id, a refresh-token -> customers map (what each token may read), a call log."""

    def __init__(self):
        self.accounts: Dict[str, Account] = {}
        self.tokens: Dict[str, List[str]] = {}
        self.factory_calls: List[tuple] = []              # (refresh_token, login_customer_id)
        self.queries: List[tuple] = []                    # (customer_id, query)
        self.idea_requests: List[Any] = []
        self.error: Optional[Exception] = None
        self.delay: float = 0.0

    # --- what monkeypatching ``google_ads_reader.CLIENT_FACTORY`` installs
    def factory(self, refresh_token, login_customer_id):
        self.factory_calls.append((refresh_token, login_customer_id))
        return FakeClient(self, refresh_token)


class FakeClient:
    enums = NS(KeywordPlanNetworkEnum=NS(GOOGLE_SEARCH="GOOGLE_SEARCH"))

    def __init__(self, world: FakeGoogle, token: str):
        self.world, self.token = world, token

    def get_service(self, name):
        return {"GoogleAdsService": _SearchService, "KeywordPlanIdeaService": _IdeaService}[name](self)

    def get_type(self, name):
        return NS(keyword_seed=NS(keywords=[]), geo_target_constants=[])


class _SearchService:
    def __init__(self, client):
        self.client = client

    def language_constant_path(self, lang):
        return f"languageConstants/{lang}"

    def geo_target_constant_path(self, geo):
        return f"geoTargetConstants/{geo}"

    def search_stream(self, customer_id, query):
        world = self.client.world
        world.queries.append((customer_id, query))
        if world.error is not None:
            raise world.error
        if customer_id not in world.tokens.get(self.client.token, []):          # Google rejects an account the token cannot see
            raise GoogleError("The caller does not have permission to access this customer")
        account = world.accounts[customer_id]
        q = " ".join(query.split())
        if "FROM customer" in q:
            rows = [NS(customer=NS(id=int(customer_id), descriptive_name=account.name, currency_code=account.currency, time_zone="America/Sao_Paulo",
                                   test_account=False, manager=False))]
        elif "FROM search_term_view" in q:
            rows = account.terms
        elif "FROM campaign WHERE campaign.status" in q:
            rows = account.campaigns
        elif "FROM ad_group WHERE ad_group.status" in q:
            rows = account.groups
        elif "FROM ad_group_ad WHERE ad_group_ad.status" in q:
            rows = account.structure_ads
        elif "FROM campaign WHERE segments" in q:
            rows = account.perf["campaign"]
        elif "FROM ad_group WHERE segments" in q:
            rows = account.perf["ad_group"]
        elif "FROM ad_group_ad WHERE segments" in q:
            rows = account.perf["ad"]
        else:
            raise AssertionError(f"unexpected query: {q}")
        return [NS(results=rows)]


class _IdeaService:
    def __init__(self, client):
        self.client = client

    def generate_keyword_ideas(self, request):
        world = self.client.world
        world.idea_requests.append(request)
        if world.error is not None:
            raise world.error
        if request.customer_id not in world.tokens.get(self.client.token, []):
            raise GoogleError("The caller does not have permission to access this customer")
        return list(world.accounts[request.customer_id].ideas)


def connect(add_connection, org, user, customer_id, refresh_token, login_customer_id=None, active=True, link_status=None):
    """Persist a Google Ads connection (encrypted tokens, valid access token) + its account link for one organization."""
    connection = GoogleAdsConnection(
        user_id=user.id, customer_id=customer_id, account_name=f"Account {customer_id}", login_customer_id=login_customer_id,
        encrypted_refresh_token=encrypt_token(refresh_token), encrypted_access_token=encrypt_token("access-token-xyz"),
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1), is_active=active)
    return add_connection(org.id, "google_ads", connection, link_status=link_status)
