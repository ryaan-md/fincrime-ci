"""WITHIN_PROFILE: the alerted activity fits what the customer already declared or what was already reviewed.
Two proofs (POLICY.md "Policy settings": an earlier review covers only what it describes, not new or larger activity):
  1. earlier review: a CLOSE review of the same rule, dated on or after the last alerted txn, whose note describes
     every alerted txn (counterparty, amount to the penny, booking date) -> CLOSE. Anything else -> None.
  2. seasonal peak: in a doc on file dated on or before the first alerted txn, one whole clause is a plain peak
     declaration ("December [and January] peak [sales][:|of] £X" or "[seasonal ]peak [sales ]in December[: £X]")
     whose months contain every alerted txn's month. Figure at least the alerted total -> CLOSE; plain declaration
     without a figure -> RFI_UPDATED_PROFILE. Any other wording (negated, hedged, comparative, annual) -> None."""
import re
from ..model import Proof
from ..loader import parse_amounts, parse_dates
from .util import amt_eq, norm_name, fmt_gbp

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
_MONTH = "(?:" + "|".join(m if m != "may" else "(?-i:May|MAY)" for m in MONTHS) + ")"
_MLIST = "(?P<m>" + _MONTH + r"(?:\s*(?:\band\b|&)\s*" + _MONTH + ")?)"
_SALES = r"(?:\s+(?:sales|takings|revenue))?"
_FIG = r"(?:\s*:\s*|\s+of\s+|\s+)£\s?(?P<fig>[\d,]+(?:\.\d{1,2})?)"
DECLS = [_MLIST + r"\s+peak" + _SALES, r"(?:seasonal\s+)?peak" + _SALES + r"\s+in\s+" + _MLIST]
CLAUSE_RE = re.compile(r",(?!\d)|[;()\u2013\u2014]|\s-\s|:(?!\s*£)")


def _sentences(text):
    return [s for s in re.split(r"(?<=[.;])\s+|\n+", text or "") if s.strip()]


def _alert_total(txs):
    return round(sum(t["amount_gbp"] for t in txs), 2)


def _review_cover(case, alert, txs):
    last = txs[-1]["booked_at"][:10]
    names = {norm_name(t.get("counterparty_name")) for t in txs}
    if "" in names:
        return None
    for r in sorted(case.prev_reviews, key=lambda r: (r.get("reviewed_on") or "", r.get("review_id") or "")):
        if r.get("verdict") != "CLOSE" or r.get("rule_id") != alert["rule_id"] or (r.get("reviewed_on") or "") < last:
            continue
        clauses = [(norm_name(c), parse_amounts(c), set(parse_dates(c))) for c in _text_clauses(r.get("note"))]
        if all(any(re.search(r"\b" + re.escape(norm_name(t.get("counterparty_name"))) + r"\b", note)
                   and any(amt_eq(a, t["amount_gbp"]) for a in amounts) and t["booked_at"][:10] in dates
                   for note, amounts, dates in clauses) for t in txs):
            ev = [r["review_id"]] + [t["transaction_id"] for t in txs][:3]
            return Proof(alert["alert_id"], True, "WITHIN_PROFILE", ev,
                         {"review_id": r["review_id"], "review_date": r["reviewed_on"], "review_note": r.get("note"),
                          "amount": fmt_gbp(_alert_total(txs)), "counterparty": txs[0].get("counterparty_name"),
                          "rule": alert["rule_id"]},
                         "within_profile")
    return None


def _text_clauses(text):
    return [c for s in _sentences((text or "").replace("*", "")) for c in CLAUSE_RE.split(s) if c.strip()]


def _declaration(clause):
    c = clause.strip(" .\t")
    for decl in DECLS:
        for pattern, has_fig in ((decl + _FIG, True), (decl, False)):
            m = re.fullmatch(pattern, c, re.I)
            if m:
                peak = {i + 1 for i, n in enumerate(MONTHS) if re.search(r"\b" + n + r"\b", m.group("m").lower())}
                return peak, round(float(m.group("fig").replace(",", "")), 2) if has_fig else None
    return set(), None


def _season_cover(case, alert, txs, first):
    months = {int(t["booked_at"][5:7]) for t in txs}
    total = _alert_total(txs)
    partial = None
    docs = sorted((d for d in case.docs_by_window_end() if d.dated <= first), key=lambda d: (d.dated, d.doc_id))
    for d in docs:
        for c in _text_clauses(d.text):
            peak, figure = _declaration(c)
            if not peak or not months <= peak:
                continue
            facts = {"doc_id": d.doc_id, "doc_title": d.title, "doc_date": d.dated, "amount": fmt_gbp(total),
                     "peak_months": [MONTHS[i - 1].title() for i in sorted(peak)], "rule": alert["rule_id"]}
            ev = [d.doc_id] + [t["transaction_id"] for t in txs][:3]
            if figure is None:
                partial = partial or Proof(alert["alert_id"], False, "RFI_UPDATED_PROFILE", ev, facts, "within_profile")
            elif total <= figure + 0.005:
                return Proof(alert["alert_id"], True, "WITHIN_PROFILE", ev,
                             {**facts, "peak_figure": fmt_gbp(figure)}, "within_profile")
    return partial


def check_within_profile(case, alert, policy):
    txs = sorted(case.alert_txns.get(alert["alert_id"], []), key=lambda t: (t["booked_at"], t["transaction_id"]))
    if not txs:
        return None
    first = txs[0]["booked_at"][:10]
    return _review_cover(case, alert, txs) or (_season_cover(case, alert, txs, first)
                                               if alert["rule_id"] == "TM01" else None)
