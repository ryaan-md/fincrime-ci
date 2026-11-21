"""ESC_STRUCTURING: a staff, outlet or support note records the customer asking whether keeping amounts under a
reporting, identity-check or invoice limit (or splitting them, or spreading them across locations) avoids it.

POLICY.md: TM03 is "repeated transactions below a threshold"; ESC_STRUCTURING is "transactions were split to stay
under a threshold". Once there is suspicion, escalate rather than ask the customer (tipping off).

A note fires when ONE sentence contains all three of: a limit (an amount like £10,000 or £1,000, or words such as
threshold / reporting / identity verification / supporting invoices), a below-or-split idea (under, below, splitting,
breaking down, aggregated) and an avoidance idea (exempt, avoid, without triggering, not reported, keep each...).
It also needs corroboration in the transactions: a TM03 alert, or at least two flagged transactions just under a
limit. A note on its own, with nothing near a limit, does not escalate."""
import re
from ..model import Flag

NOTE_TYPES = {"staff_note", "contact_note", "outlet_note", "support_chat", "branch_note", "call_note"}
LIMIT = re.compile(r"(£\s?\d{1,3}(,\d{3})+(\.\d{2})?|£\s?\d+k\b|threshold|limit|reporting|report(ed)?|identity (verification|check)s?"
                   r"|id checks?|verification|supporting invoices?|invoice requirement|checks?)", re.I)
BELOW = re.compile(r"(below|under|beneath|less than|split(ting)?|break(ing)? (them |it |payments |amounts )?(down|up)"
                   r"|broken (down|up)|smaller (amounts|payments|deposits|top-?ups)|aggregat\w*)", re.I)
AVOID = re.compile(r"(exempt\w*|avoid\w*|without (triggering|needing|having)|bypass\w*|get(ting)? around"
                   r"|not (be )?(reported|flagged|checked|aggregated)|isn'?t (reported|flagged|checked)|stay(ing)? (under|below)"
                   r"|keep(ing)? (it|them|each|individual|amounts|payments|deposits|top-?ups)\b|so (that )?(it|they|nothing)"
                   r"|need to provide|trigger\w*)", re.I)
SENTENCE = re.compile(r"[^.?!\n]+[.?!]?")
LIMITS_GBP = (10000.0, 1000.0)
NEAR = 0.8          # "just under" = at least 80% of the limit


def structuring_sentences(text):
    """Sentences that ask about staying under / splitting to avoid a limit."""
    return [s.strip() for s in SENTENCE.findall(text or "")
            if LIMIT.search(s) and BELOW.search(s) and AVOID.search(s)]


def _near_limit_txns(case):
    out = {}
    for a in case.alerts:
        for t in case.alert_txns.get(a["alert_id"], []):
            amt = t.get("amount_gbp")
            if amt is not None and any(NEAR * lim <= float(amt) < lim for lim in LIMITS_GBP):
                out[t["transaction_id"]] = t
    return sorted(out.values(), key=lambda t: (t["booked_at"], t["transaction_id"]))


def scan_structuring_language(case, policy):
    tm03 = [a for a in case.alerts if a["rule_id"] == "TM03"]
    near = _near_limit_txns(case)
    if not tm03 and len(near) < 2:
        return []
    tm03_tx = [t["transaction_id"] for a in tm03 for t in case.alert_txns.get(a["alert_id"], [])]
    tx_ev = list(dict.fromkeys(tm03_tx or [t["transaction_id"] for t in near]))[:4]
    m = (tm03[0].get("measured") or {}) if tm03 else {}
    out = []
    for d in case.docs:
        if d.type not in NOTE_TYPES:
            continue
        said = structuring_sentences(d.text)
        if said:
            out.append(Flag("ESC_STRUCTURING", [d.doc_id] + tx_ev,
                            {"doc_id": d.doc_id, "doc_title": d.title, "note_dated": d.dated, "quote": said[0],
                             "count": m.get("count") or m.get("payments") or len(near),
                             "total_gbp": m.get("total_gbp"), "window_days": m.get("window_days")},
                            "structuring_language", 4))
    return out
