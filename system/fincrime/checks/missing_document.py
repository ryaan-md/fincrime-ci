"""NOTHING ON FILE -> RFI. Never produces a CLOSE (always passed=False). Owner: T-RFI.

When every document on file for the customer is a standard onboarding/filler type (policy.STANDARD_DOC_TYPES, plus
the customer's OWN company register extract), no document explains the alert and none contradicts it. For the alert
patterns listed in policy.RFI_WHEN_NOTHING_ON_FILE for this institution, one document or answer from the customer
would settle the case, so the proof fails with the RFI code naming what to ask for (arbiter step E).
Red flags still win first (arbiter step A). The first listed rule present in the case picks the code, so every alert
of the case gets the same code and evidence. A case with any rule not listed for its institution -> None, and so is a
solo-only rule (policy.RFI_WHEN_NOTHING_ON_FILE_SOLO_ONLY) next to another rule."""
from ..model import Proof
from .util import norm_name


def _is_standard(doc, customer_name, policy):
    if doc.type in policy.STANDARD_DOC_TYPES:
        return True
    return doc.type in policy.OWN_ONLY_DOC_TYPES and bool(customer_name) and customer_name in norm_name(doc.text)


def _case_code(case, policy):
    inst = getattr(policy, "NAME", "")
    table = policy.RFI_WHEN_NOTHING_ON_FILE.get(inst, ())
    rules = {a["rule_id"] for a in case.alerts}
    if not table or not rules or not rules <= {r for r, _ in table}:
        return None, None
    if len(rules) > 1 and rules & set(policy.RFI_WHEN_NOTHING_ON_FILE_SOLO_ONLY.get(inst, ())):
        return None, None
    name = norm_name(case.customer.get("name"))
    if not all(_is_standard(d, name, policy) for d in case.docs):
        return None, None
    return next((r, c) for r, c in table if r in rules)


def check_missing_document(case, alert, policy):
    rule, code = _case_code(case, policy)
    if not code:
        return None
    txs = []
    for a in case.alerts:
        for t in case.alert_txns.get(a["alert_id"], []):
            if t["transaction_id"] not in txs:
                txs.append(t["transaction_id"])
    entries = sorted({a.get("measured", {}).get("list_entry") for a in case.alerts
                      if a["rule_id"] == "TM22" and a.get("measured", {}).get("list_entry")})
    ev = txs[:6] + [e for e in entries if e in case.list_entries]
    facts = {"rule": rule, "missing": policy.RFI_ASK.get(code, ""),
             "docs_on_file": sorted({d.type for d in case.docs})}
    return Proof(alert["alert_id"], False, code, ev, facts, "missing_document")
