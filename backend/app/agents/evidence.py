"""Evidence ledger: what the tools of THIS run actually returned, and the rules that keep model claims honest (etapa 02.5).

Every real-data tool records the facts it returned (``record_metric``) and that it succeeded (``record_tool_ok``). When a
specialist finishes, ``basis_problems`` checks each claim of its output against that ledger:

* ``hypothesis``  : the model's own idea. May cite no metric at all (a hypothesis with a number is a fabricated measurement).
* ``real``        : a fact from a tool of this run (Google Ads account data, the product page) or from data the requester
                    supplied. Every cited metric must equal a value the tool returned (or a number present in the supplied
                    data). Model knowledge can NEVER be ``real``.
* ``estimate``    : only Google Keyword Planner figures (Google's own estimates), verified the same way.

A violation is returned as a problem list: the runner gives the model one repair turn, then fails the task with
``invalid_output``. So a fabricated volume, CPC or conversion count cannot reach a stored result.
"""
import math
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

SOURCE_TOOLS = {
    "google_ads_api": {"google_ads.get_account_info", "google_ads.get_account_structure", "google_ads.get_campaign_performance",
                       "google_ads.get_ad_group_performance", "google_ads.get_ad_performance", "google_ads.get_search_terms_report"},
    "google_keyword_planner": {"google_ads.keyword_ideas"},
    "product_page": {"web.analyze_page"},
    "supplied_context": set(),
    "model_knowledge": set(),
}
SOURCES = tuple(SOURCE_TOOLS)


def ledger(ctx) -> Dict[str, Any]:
    if not ctx.evidence:
        ctx.evidence.update({"tools_ok": set(), "metrics": {}})
    return ctx.evidence


def record_tool_ok(ctx, tool_name: str) -> None:
    ledger(ctx)["tools_ok"].add(tool_name)


def record_metric(ctx, entity: str, name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return
    ledger(ctx)["metrics"][(entity.lower(), name)] = float(value)


def _numbers(value: Any, out: Set[float], depth: int = 0) -> None:
    if depth > 8:
        return
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        out.add(float(value))
    elif isinstance(value, dict):
        for v in value.values():
            _numbers(v, out, depth + 1)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _numbers(v, out, depth + 1)


def supplied_numbers(ctx) -> Set[float]:
    """Numbers present in what the requester supplied (task context) or in upstream agents' results."""
    numbers: Set[float] = set()
    _numbers(ctx.input_context, numbers)
    _numbers(getattr(ctx, "upstream", None), numbers)
    return numbers


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-9)


Claim = Tuple[str, str, str, List[Any]]      # (label, data_kind, source, cited metrics)


def basis_problems(claims: Iterable[Claim], ctx) -> List[str]:
    problems: List[str] = []
    led = ledger(ctx)
    supplied: Optional[Set[float]] = None
    for label, kind, source, metrics in claims:
        if kind == "hypothesis":
            if metrics:
                problems.append(f"{label}: a hypothesis cannot cite metrics; if the number is real, cite the tool that returned it")
            continue
        if source == "model_knowledge":
            problems.append(f"{label}: model knowledge can only be labelled 'hypothesis', not '{kind}'")
            continue
        if kind == "estimate" and source != "google_keyword_planner":
            problems.append(f"{label}: only Google Keyword Planner figures may be labelled 'estimate' (source was '{source}')")
            continue
        if kind == "real" and source == "google_keyword_planner":
            problems.append(f"{label}: Keyword Planner figures are Google estimates; label them 'estimate'")
            continue
        if kind == "estimate" and not metrics:
            problems.append(f"{label}: an 'estimate' must cite the Keyword Planner metric it refers to")
        needed = SOURCE_TOOLS[source]
        if needed and not (needed & led["tools_ok"]):
            problems.append(f"{label}: source '{source}' was never successfully read in this run; call the tool or label this a hypothesis")
            continue
        for metric in metrics:
            if source == "supplied_context":
                supplied = supplied_numbers(ctx) if supplied is None else supplied
                if not any(_close(metric.value, n) for n in supplied):
                    problems.append(f"{label}: {metric.name}={metric.value} is not in the data supplied with the task")
            else:
                known = led["metrics"].get((metric.entity.lower(), metric.name))
                if known is None or not _close(known, metric.value):
                    problems.append(f"{label}: {metric.entity} {metric.name}={metric.value} does not match any value the tools returned")
    return problems[:12]
