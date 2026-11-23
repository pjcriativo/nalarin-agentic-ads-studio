"""Deterministic extraction of commercial facts from a product / sales page (etapa 02.5, tool ``web.analyze_page``).

Everything returned is something the page ACTUALLY says, with the verbatim snippet it came from (``real`` data about the
page). What the page does not say is listed under ``not_found`` instead of being guessed: no price is invented, no review
count is estimated. The HTML is parsed as served (no JavaScript execution): a page that renders its content in the browser
is flagged ``likely_js_rendered`` so the agent knows the extraction is partial.

The page text is third-party, untrusted data. This module never follows or interprets instructions found in it; the runner
still wraps the whole result as untrusted tool output and flags injection-looking text.
"""
import json
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

from app.agents import ads_rules

MAX_ITEMS = 12
SNIPPET = 160

_PRICE = re.compile(
    r"(?:R\$|US\$|\$|€|£)\s?\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\b\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})\s?(?:reais|usd|eur|brl)\b", re.I)
_NUMBER = re.compile(r"(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+)")
_CTA = re.compile(r"\b(comprar|compre|quero|adicionar ao carrinho|adicionar|assinar|assine|garantir|reservar|solicitar|baixar|"
                  r"buy|add to cart|order|subscribe|get started|sign up|checkout|start (?:free )?trial|book|download)\b", re.I)
_GUARANTEE = re.compile(r"[^.!?\n]{0,80}\b(garantia|guarantee|money[- ]back|reembolso|refund|devolu[cç][aã]o)\b[^.!?\n]{0,80}", re.I)
_SHIPPING = re.compile(r"[^.!?\n]{0,60}\b(frete|envio|entrega|shipping|delivery)\b[^.!?\n]{0,60}", re.I)
_PAYMENT = re.compile(r"\b(pix|boleto|cart[aã]o|credit card|visa|mastercard|paypal|parcel\w+|installments?)\b", re.I)
_URGENCY = re.compile(r"[^.!?\n]{0,60}\b(últimas? unidades|restam \d+|only \d+ left|oferta (?:termina|expira)|offer ends|hoje apenas|limited time|"
                      r"tempo limitado|contagem regressiva)\b[^.!?\n]{0,60}", re.I)
_SOCIAL_PROOF = re.compile(r"[^.!?\n]{0,60}\b(depoimentos?|testimonials?|avalia[cç][oõ]es|reviews?|clientes satisfeitos|stars?|estrelas)\b[^.!?\n]{0,60}", re.I)
_POLICY_LINKS = {"privacy": re.compile(r"privacidade|privacy", re.I), "terms": re.compile(r"termos|terms", re.I),
                 "refund": re.compile(r"reembolso|refund|devolu|troca", re.I), "contact": re.compile(r"contato|contact|fale conosco|suporte|support", re.I)}
_CONTACT = {"email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "phone": re.compile(r"(?:\+?\d{1,3}[\s-]?)?\(?\d{2}\)?[\s-]?\d{4,5}[\s-]?\d{4}")}


def _clip(text: str, limit: int = SNIPPET) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _parse_number(raw: str) -> Optional[float]:
    """'1.299,90' / '1,299.90' / '197' -> float; None when it does not read as a number."""
    match = _NUMBER.search(raw)
    if not match:
        return None
    token = match.group(1)
    if "," in token and "." in token:
        token = token.replace(".", "").replace(",", ".") if token.rfind(",") > token.rfind(".") else token.replace(",", "")
    elif "," in token:
        head, _, tail = token.rpartition(",")
        token = head.replace(",", "") + "." + tail if len(tail) in (1, 2) else token.replace(",", "")
    elif "." in token:
        head, _, tail = token.rpartition(".")
        token = head.replace(".", "") + "." + tail if len(tail) in (1, 2) else token.replace(".", "")
    try:
        return float(token)
    except ValueError:
        return None


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.lang: Optional[str] = None
        self.meta: Dict[str, str] = {}
        self.canonical: Optional[str] = None
        self.headings: List[Dict[str, str]] = []
        self.ctas: List[str] = []
        self.links: List[Dict[str, str]] = []
        self.jsonld: List[str] = []
        self.text: List[str] = []
        self.scripts = 0
        self.forms = 0
        self.images = 0
        self._stack: List[str] = []
        self._buf: List[str] = []
        self._ld: Optional[List[str]] = None
        self._href = ""

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        self._stack.append(tag)
        if tag == "html":
            self.lang = a.get("lang") or None
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key and a.get("content"):
                self.meta.setdefault(key, a["content"])
        elif tag == "link" and a.get("rel", "").lower() == "canonical":
            self.canonical = a.get("href") or None
        elif tag == "script":
            self.scripts += 1
            if a.get("type", "").lower() == "application/ld+json":
                self._ld = []
        elif tag == "form":
            self.forms += 1
        elif tag == "img":
            self.images += 1
        elif tag == "a":
            self._href = a.get("href", "")
        if tag in ("h1", "h2", "h3", "button", "a", "title"):
            self._buf = []

    def handle_endtag(self, tag):
        text = _clip("".join(self._buf), 200)
        if tag == "title":
            self.title = text
        elif tag in ("h1", "h2", "h3") and text:
            self.headings.append({"level": tag, "text": text})
        elif tag == "button" and text:
            self.ctas.append(text)
        elif tag == "a":
            if text:
                self.links.append({"text": text, "href": self._href[:300]})
                if _CTA.search(text):
                    self.ctas.append(text)
        elif tag == "script" and self._ld is not None:
            self.jsonld.append("".join(self._ld))
            self._ld = None
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()
        if tag in ("h1", "h2", "h3", "button", "a", "title"):
            self._buf = []

    def handle_data(self, data):
        if self._ld is not None:
            self._ld.append(data)
            return
        in_skipped = any(t in ("script", "style", "noscript", "template", "svg") for t in self._stack)
        if not in_skipped:
            self._buf.append(data)
            if data.strip():
                self.text.append(data.strip())


def _walk_jsonld(node: Any, out: List[Dict[str, Any]]) -> None:
    if isinstance(node, list):
        for item in node:
            _walk_jsonld(item, out)
    elif isinstance(node, dict):
        out.append(node)
        for value in node.values():
            if isinstance(value, (dict, list)):
                _walk_jsonld(value, out)


def _types(node: Dict[str, Any]) -> List[str]:
    t = node.get("@type")
    return [str(x) for x in t] if isinstance(t, list) else ([str(t)] if t else [])


def _structured(blocks: List[str]) -> Dict[str, Any]:
    nodes: List[Dict[str, Any]] = []
    for block in blocks[:10]:
        try:
            _walk_jsonld(json.loads(block), nodes)
        except (ValueError, RecursionError):
            continue
    products, faqs, ratings = [], [], []
    for node in nodes:
        kinds = _types(node)
        if "Product" in kinds:
            offers = node.get("offers")
            offer_list = offers if isinstance(offers, list) else ([offers] if isinstance(offers, dict) else [])
            products.append({
                "name": _clip(str(node.get("name", "")), 120) or None,
                "brand": _clip(str((node.get("brand") or {}).get("name") if isinstance(node.get("brand"), dict) else node.get("brand") or ""), 80) or None,
                "description": _clip(str(node.get("description", "")), 240) or None,
                "offers": [{"price": _parse_number(str(o.get("price", ""))) if o.get("price") not in (None, "") else None,
                            "currency": o.get("priceCurrency") or None, "availability": str(o.get("availability", "")).rsplit("/", 1)[-1] or None}
                           for o in offer_list[:5] if isinstance(o, dict)]})
        if "AggregateRating" in kinds and node.get("ratingValue") is not None:
            ratings.append({"rating_value": _parse_number(str(node.get("ratingValue"))), "review_count": _parse_number(str(node.get("reviewCount", node.get("ratingCount", "")))) if (node.get("reviewCount") or node.get("ratingCount")) else None})
        if "Question" in kinds and node.get("name"):
            faqs.append(_clip(str(node["name"]), 140))
    return {"products": products[:5], "aggregate_ratings": ratings[:3], "faq_questions": faqs[:MAX_ITEMS]}


def _matches(pattern: re.Pattern, text: str) -> List[str]:
    seen, out = set(), []
    for m in pattern.finditer(text):
        snippet = _clip(m.group(0), SNIPPET)
        if snippet.lower() not in seen:
            seen.add(snippet.lower())
            out.append(snippet)
        if len(out) >= MAX_ITEMS:
            break
    return out


def analyze_html(html: str, url: str) -> Dict[str, Any]:
    parser = _Page()
    parser.feed(html)
    parser.close()
    body = re.sub(r"\s+", " ", " ".join(parser.text))
    structured = _structured(parser.jsonld)

    prices: List[Dict[str, Any]] = []
    for product in structured["products"]:
        for offer in product["offers"]:
            if offer["price"] is not None:
                prices.append({"amount": offer["price"], "currency": offer["currency"], "source": "structured_data", "snippet": product["name"]})
    for m in _PRICE.finditer(body):
        amount = _parse_number(m.group(0))
        if amount is not None and not any(abs(p["amount"] - amount) < 1e-9 and p["source"] == "page_text" for p in prices):
            prices.append({"amount": amount, "currency": None, "source": "page_text", "snippet": _clip(body[max(0, m.start() - 40): m.end() + 40])})
        if len(prices) >= MAX_ITEMS:
            break

    guarantees, shipping = _matches(_GUARANTEE, body), _matches(_SHIPPING, body)
    urgency, social = _matches(_URGENCY, body), _matches(_SOCIAL_PROOF, body)
    payments = sorted({m.group(0).lower() for m in _PAYMENT.finditer(body)})[:MAX_ITEMS]
    ctas = list(dict.fromkeys(parser.ctas))[:MAX_ITEMS]
    link_text = " | ".join(f"{l['text']} {l['href']}" for l in parser.links)
    policy_pages = {name: bool(rx.search(link_text)) for name, rx in _POLICY_LINKS.items()}
    contact = {name: bool(rx.search(body)) for name, rx in _CONTACT.items()}

    prominent = " . ".join([parser.title, parser.meta.get("description", "")] + [h["text"] for h in parser.headings if h["level"] == "h1"])
    policy = ads_rules.check_policy_text(prominent[:1500], "body") if prominent.strip() else None
    risk_flags = [{"rule": f["rule"], "severity": f["severity"], "excerpt": f["excerpt"]} for f in (policy or {}).get("findings", [])]

    facts = {
        "title": parser.title or None,
        "meta_description": _clip(parser.meta.get("description", ""), 300) or None,
        "open_graph": {k[3:]: _clip(v, 200) for k, v in parser.meta.items() if k in ("og:title", "og:description", "og:site_name", "og:type")} or None,
        "language": parser.lang, "canonical_url": parser.canonical,
        "headings": parser.headings[:MAX_ITEMS * 2], "call_to_actions": ctas, "prices": prices[:MAX_ITEMS],
        "structured_data": structured if any(structured.values()) else None,
        "guarantee_or_refund_mentions": guarantees, "shipping_mentions": shipping, "payment_methods_mentioned": payments,
        "social_proof_mentions": social, "urgency_or_scarcity_claims": urgency,
        "policy_pages_linked": policy_pages, "contact_details_on_page": contact, "forms": parser.forms,
    }
    not_found = [name for name, empty in (
        ("price", not prices), ("call_to_action", not ctas), ("guarantee_or_refund", not guarantees), ("shipping_info", not shipping),
        ("structured_product_data", not structured["products"]), ("customer_reviews", not social and not structured["aggregate_ratings"]),
        ("privacy_policy_link", not policy_pages["privacy"]), ("terms_link", not policy_pages["terms"]),
        ("contact_details", not any(contact.values()) and not policy_pages["contact"])) if empty]
    likely_js = len(body) < 400 and parser.scripts >= 3
    return {
        "analyzed": True, "url": url, "text_characters": len(body), "likely_js_rendered": likely_js,
        "provenance": {"data_kind": "real", "source": "product_page",
                       "note": "Facts are what the page states, with the snippet they came from. Whether they are TRUE is not verified."},
        "facts": facts, "not_found": not_found, "policy_risk_flags": risk_flags,
        "limitations": ["The page was read as served (JavaScript is not executed)."]
                       + (["Very little text was found and the page relies on scripts: the extraction is probably incomplete."] if likely_js else []),
    }
