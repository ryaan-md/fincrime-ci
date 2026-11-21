"""RETURNING_CUSTOMER: "A verified customer resumed using a dormant account" (POLICY.md reason-code table).
Applies to the TM07 (dormant reactivation) alert, and to a TM01 alert in the same case, because the reactivating
inflow is what pushes TM01 over its limit. The proof passes only when EVERY substantive fact is verified:
  1. identity: a reverification_record on file by the window end, done with a live selfie on the device already
     registered to the customer, dated ON OR BEFORE the first reactivating inflow;
  2. source: every incoming TM07 transaction comes from the customer's own name and is matched, to the penny and
     within the date gap, by a bank_statement the customer is shown to hold for that account (POLICY.md: "An account
     in the customer's own name elsewhere counts as their own account only when a document on file shows the customer
     holds it"; the line matching itself is shared with checks/own_account.py);
  3. nothing moved on: TM07's outgoing transactions are card spending only (no transfer or cash out after waking);
  4. TM01 (if alerted): without the proven inflows the account would have stayed within its limit, and the
     own-account limit holds: measured multiple >= OWN_ACCOUNT_RFI_MULTIPLE -> RFI_UPDATED_PROFILE (SOP), below -> close.
Anything missing returns None, so the rule's RFI default (RFI_SOURCE_OF_FUNDS) applies. Register BEFORE
check_own_account so a TM01 alert in a returning case gets this code, not OWN_ACCOUNT."""
from ..model import Proof
from .util import fmt_gbp, norm_name
from .own_account import _explained, _holds, _proof_for

APPLIES_TO = {"TM07", "TM01"}
SPENDING_CHANNELS = {"card"}


def _reverification(case, first_in):
    for d in sorted(case.docs_by_window_end(), key=lambda d: (d.dated, d.doc_id)):
        f = d.fields
        if d.type == "reverification_record" and f.get("live_selfie") and f.get("registered_device") \
                and f.get("reverified_on") and f["reverified_on"] <= first_in:
            return d
    return None


def _returning(case, policy):
    """The TM07 proof shared by both alerts: (passed, rfi_code, evidence, facts) or None when it does not apply."""
    tm07 = [a for a in case.alerts if a["rule_id"] == "TM07"]
    if not tm07:
        return None
    cust = norm_name(case.customer.get("name"))
    txs = [t for a in tm07 for t in case.alert_txns.get(a["alert_id"], [])]
    ins = sorted((t for t in txs if t["direction"] == "in"), key=lambda t: (t["booked_at"], t["transaction_id"]))
    if not cust or not ins:
        return None
    if any(t["direction"] == "out" and (t.get("channel") or "") not in SPENDING_CHANNELS for t in txs):
        return None                                   # money moved on after reactivation: not a simple return
    if any(norm_name(t.get("counterparty_name")) != cust for t in ins):
        return None                                   # someone else's money woke the account
    stmts = [d for d in case.docs_by_window_end() if d.type == "bank_statement" and _holds(d, cust)]
    proven, consumed = {}, set()
    for t in ins:
        s, line = _proof_for(t, stmts, consumed) if t.get("counterparty_account") else (None, None)
        if not line:
            return None                               # source account not shown to be the customer's
        proven[t["transaction_id"]] = (s, line)
    rv = _reverification(case, ins[0]["booked_at"][:10])
    if not rv:
        return None                                   # not re-verified before the money arrived
    docs = [rv.doc_id] + sorted({s.doc_id for s, _ in proven.values()})
    key = ins[0]
    stmt = proven[key["transaction_id"]][0]
    multiple = max([a.get("measured", {}).get("multiple") or 0 for a in case.alerts if a["rule_id"] == "TM01"] or [0])
    facts = {"doc_id": rv.doc_id, "doc_title": rv.title, "doc_date": rv.dated, "statement_id": stmt.doc_id,
             "statement_title": stmt.title, "reverified_on": rv.fields.get("reverified_on"),
             "txn_id": key["transaction_id"], "txn_date": key["booked_at"][:10], "amount": fmt_gbp(key["amount_gbp"]),
             "multiple": multiple, "dormant_days": (tm07[0].get("measured") or {}).get("dormant_days")}
    ev = docs + [t["transaction_id"] for t in ins]
    if multiple >= policy.OWN_ACCOUNT_RFI_MULTIPLE:
        return False, "RFI_UPDATED_PROFILE", ev, facts, proven
    return True, "RETURNING_CUSTOMER", ev, facts, proven


def check_returning_customer(case, alert, policy):
    if alert["rule_id"] not in APPLIES_TO:
        return None
    r = _returning(case, policy)
    if r is None:
        return None
    passed, code, ev, facts, proven = r
    if alert["rule_id"] == "TM01":
        ok, _ = _explained(alert, case.alert_txns.get(alert["alert_id"], []), proven, case,
                           norm_name(case.customer.get("name")))
        if not ok:
            return None                               # TM01 has other unexplained money: leave it to other checkers
    return Proof(alert["alert_id"], passed, code, ev, {**facts, "rule": alert["rule_id"]}, "returning_customer")
