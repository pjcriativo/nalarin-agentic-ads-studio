"""Deterministic, local checks used by tools and by the output validation. They are STATIC EDITORIAL RULES,
not an official Google Ads policy decision (that needs the platform; see future_tools.py): every result says so.
"""
import re
from typing import Dict, List

HEADLINE_MAX = 30
DESCRIPTION_MAX = 90
HEADLINES_RANGE = (3, 15)
DESCRIPTIONS_RANGE = (2, 4)
SOURCE = "static_rules"
NOTICE = "Static editorial checks only; not an official Google Ads policy decision."

_PHONE = re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s.-]?)?\(?\d{2,3}\)?[\s.-]?\d{4,5}[\s.-]?\d{4}(?!\d)")
_REPEATED_PUNCT = re.compile(r"([!?.,;:])\1{1,}")
_SHOUTING_WORD = re.compile(r"\b[A-ZÀ-Ý]{5,}\b")


def validate_rsa(headlines: List[str], descriptions: List[str]) -> Dict:
    """Responsive Search Ad asset limits and common editorial rejections."""
    issues: List[Dict] = []

    def add(field: str, index: int, rule: str, message: str, severity: str = "error"):
        issues.append({"field": field, "index": index, "rule": rule, "severity": severity, "message": message})

    if not HEADLINES_RANGE[0] <= len(headlines) <= HEADLINES_RANGE[1]:
        add("headlines", -1, "headline_count", f"Need {HEADLINES_RANGE[0]}-{HEADLINES_RANGE[1]} headlines, got {len(headlines)}.")
    if not DESCRIPTIONS_RANGE[0] <= len(descriptions) <= DESCRIPTIONS_RANGE[1]:
        add("descriptions", -1, "description_count", f"Need {DESCRIPTIONS_RANGE[0]}-{DESCRIPTIONS_RANGE[1]} descriptions, got {len(descriptions)}.")
    for field, items, limit in (("headlines", headlines, HEADLINE_MAX), ("descriptions", descriptions, DESCRIPTION_MAX)):
        seen = set()
        for i, text in enumerate(items):
            if len(text) > limit:
                add(field, i, "length", f"{len(text)} characters (limit {limit}).")
            if text != text.strip():
                add(field, i, "whitespace", "Leading or trailing whitespace.", "warning")
            key = text.strip().lower()
            if key in seen:
                add(field, i, "duplicate", "Duplicate asset.")
            seen.add(key)
            if _REPEATED_PUNCT.search(text):
                add(field, i, "repeated_punctuation", "Repeated punctuation.")
            if _SHOUTING_WORD.search(text):
                add(field, i, "excessive_capitals", "Word in all capitals (not an acronym of 4 letters or less).", "warning")
            if _PHONE.search(text):
                add(field, i, "phone_number", "Phone numbers in ad text are restricted.", "warning")
            if field == "headlines" and "!" in text:
                add(field, i, "exclamation_in_headline", "Exclamation marks are not allowed in headlines.")
    errors = [i for i in issues if i["severity"] == "error"]
    return {
        "valid": not errors,
        "issues": issues,
        "counts": {"headlines": len(headlines), "descriptions": len(descriptions)},
        "limits": {"headline_chars": HEADLINE_MAX, "description_chars": DESCRIPTION_MAX},
        "source": SOURCE, "notice": NOTICE,
    }


# rule id -> (severity, pattern, explanation). English and Portuguese: the product is bilingual.
_POLICY_RULES = [
    ("guaranteed_results", "warning", r"(?i)\b(guarantee[sd]?|guaranteed results?|garanti[ad]\w*|100\s?%\s?(seguro|garant|eficaz|efficacy|effective))\b",
     "Absolute guarantees are usually rejected unless substantiated and with terms."),
    ("miracle_or_cure_claim", "blocker", r"(?i)\b(miracle|miraculous|cures?\b|cura\s+(?:definitiva|garantida)|milagr\w+)",
     "Miracle or cure claims are prohibited in most categories."),
    ("get_rich_quick", "blocker", r"(?i)\b(get rich quick|easy money|dinheiro f[aá]cil|fique rico|ganhe\s+dinheiro\s+(?:r[aá]pido|f[aá]cil))\b",
     "Get-rich-quick promises are prohibited."),
    ("unsubstantiated_superlative", "warning", r"(?i)(?:#\s?1\b|\bnumber one\b|\bthe best\b|\bo melhor\b|\ba melhor\b|\bn[úu]mero 1\b)",
     "Superlatives need verifiable proof (a third-party source) in the ad or landing page."),
    ("before_after_claim", "warning", r"(?i)\b(before and after|antes e depois)\b",
     "Before/after claims are restricted for health and weight products."),
    ("personal_attributes", "blocker", r"(?i)\b(are you (?:diabetic|depressed|overweight|bankrupt|gay)|voc[eê] (?:[eé] )?(?:diab[eé]tico|deprimido|obeso|falido))\b",
     "Ads must not imply knowledge of personal attributes."),
    ("clickbait", "warning", r"(?i)(you won'?t believe|n[aã]o vai acreditar|shocking|chocante)",
     "Clickbait phrasing is discouraged."),
    ("urgency_pressure", "info", r"(?i)\b(last chance|only today|[uú]ltima chance|s[oó] hoje)\b",
     "Urgency claims must be true and verifiable."),
    ("free_without_terms", "info", r"(?i)\b(free|gr[aá]tis|gratuito)\b",
     "'Free' must be genuinely free; state any conditions on the landing page."),
    ("medical_claim", "warning", r"(?i)\b(treats?|heals?|trata\s+\w+|emagre[cç]a\s+\d+)\b",
     "Health and weight-loss claims are restricted and may need certification."),
]
_COMPILED = [(rid, sev, re.compile(rx), msg) for rid, sev, rx, msg in _POLICY_RULES]


def check_policy_text(text: str, kind: str = "text") -> Dict:
    findings = []
    for rule_id, severity, pattern, message in _COMPILED:
        match = pattern.search(text)
        if match:
            findings.append({"rule": rule_id, "severity": severity, "excerpt": match.group(0)[:60], "message": message})
    if _REPEATED_PUNCT.search(text):
        findings.append({"rule": "repeated_punctuation", "severity": "warning", "excerpt": _REPEATED_PUNCT.search(text).group(0), "message": "Repeated punctuation is rejected."})
    if _SHOUTING_WORD.search(text):
        findings.append({"rule": "excessive_capitals", "severity": "warning", "excerpt": _SHOUTING_WORD.search(text).group(0), "message": "Excessive capitalization."})
    worst = "blocker" if any(f["severity"] == "blocker" for f in findings) else ("warning" if any(f["severity"] == "warning" for f in findings) else "clear")
    return {"kind": kind, "worst": worst, "findings": findings, "source": SOURCE, "notice": NOTICE}
