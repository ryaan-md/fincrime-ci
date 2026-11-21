"""Generic baseline: a decisive document (by TYPE) dated on/before the window end states the exact amount of the
alert's transactions. Owners of T-CHK-* tickets replace this with code-specific checkers that also verify
payee/seller/holder identity and date order. Never applies to rules that need identity proof (TM22) or pattern
rules (TM02, TM03, TM04, TM06, TM08) - those need their own checkers."""
from ..model import Proof
from .util import amt_eq, fmt_gbp

APPLIES_TO = {"TM01", "TM05", "TM07"}

def check_documented_amount(case, alert, policy):
    if alert["rule_id"] not in APPLIES_TO:
        return None
    txs = case.alert_txns.get(alert["alert_id"], [])
    if not txs:
        return None
    for d in case.docs_by_window_end():
        code = policy.EXPLAINING_DOCS.get(d.type)
        if not code:
            continue
        for t in sorted(txs, key=lambda t: -t["amount_gbp"]):
            if any(amt_eq(a, t["amount_gbp"]) for a in d.amounts) and d.dated <= t["booked_at"][:10]:
                return Proof(alert["alert_id"], True, code, [d.doc_id, t["transaction_id"]],
                             {"doc_id": d.doc_id, "doc_title": d.title, "doc_date": d.dated,
                              "amount": fmt_gbp(t["amount_gbp"]), "txn_id": t["transaction_id"],
                              "txn_date": t["booked_at"][:10], "rule": alert["rule_id"]},
                             "documented_amount")
    return None
