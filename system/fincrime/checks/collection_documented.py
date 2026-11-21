"""COLLECTION_DOCUMENTED (TM02/TM04): the customer collected money on behalf of named people and paid it on to the
documented supplier (POLICY.md reason codes: "A documented collection made on behalf of named people explains the
payments"). A booking_confirmation on file by the window end closes the alert only when ALL of these hold:
  - the booking adds up: traveller list length == stated count, per-person price x count == total, customer named;
  - every incoming payment in the TM02/TM04 alerts is from a listed traveller at exactly the per-person price (once
    each), from the customer themselves, or from their declared employer (salary);
  - one outgoing payment of exactly the booking total went to the named payee at the account the booking states,
    and every other outgoing payment in the TM02/TM04 alerts is routine personal spend (ROUTINE_OUT_CHANNELS), a
    wallet transfer to the customer themselves, or a transfer to an account a bank_statement on file shows they hold;
  - the booking is dated on or before every payment it explains.
Explanation present but incomplete -> RFI. Money that did not go to the documented supplier -> None (default rules)."""
import re

from ..model import Proof
from .util import fmt_gbp, norm_name

APPLIES_TO = {"TM02", "TM04"}
DOC_TYPE = "booking_confirmation"
ROUTINE_OUT_CHANNELS = {"direct_debit", "standing_order", "card", "cash_withdrawal"}


def _pence_eq(a, b):
    """Exact to the penny (the shared amt_eq allows a 1p difference)."""
    return a is not None and b is not None and round(float(a) * 100) == round(float(b) * 100)


def _digits(s):
    return re.sub(r"\D", "", s or "")


def _alert_txns(case):
    """Union of the TM02/TM04 alert transactions, deduped, in booking order (one verdict for the whole collection)."""
    seen = {}
    for a in case.alerts:
        if a["rule_id"] in APPLIES_TO:
            for t in case.alert_txns.get(a["alert_id"], []):
                seen[t["transaction_id"]] = t
    return sorted(seen.values(), key=lambda t: (t["booked_at"], t["transaction_id"]))


def _supplier_payment(case, d, f):
    payee, total = norm_name(f.get("payee_name")), f.get("total_amount")
    if not (f.get("payee_sort_code") and f.get("payee_account_number")):
        return None
    account = _digits(f["payee_sort_code"] + f["payee_account_number"])
    for t in case.txns:
        day = t["booked_at"][:10]
        if (t["direction"] == "out" and d.dated <= day <= case.window["to"] and _pence_eq(t["amount_gbp"], total)
                and norm_name(t.get("counterparty_name")) == payee
                and _digits(t.get("counterparty_account")) == account):
            return t
    return None


def _holds_account(case, cust, account):
    acc = _digits(account)
    for s in case.docs_by_window_end():
        f = s.fields
        if (s.type == "bank_statement" and f.get("sort_code") and f.get("account_number")
                and cust in {norm_name(h) for h in f.get("holder_names") or []}
                and _digits(f["sort_code"] + f["account_number"]) == acc):
            return True
    return False


def _allowed_outflow(case, cust, out, t):
    if t["transaction_id"] == out["transaction_id"] or t.get("channel") in ROUTINE_OUT_CHANNELS:
        return True
    if not cust or norm_name(t.get("counterparty_name")) != cust:
        return False
    return t.get("channel") == "wallet_transfer" or _holds_account(case, cust, t.get("counterparty_account"))


def _assess(case, d):
    """-> (passed, code, evidence, facts) or None when the booking does not explain where the money went."""
    f = d.fields
    names = f.get("traveller_names") or []
    price, total, count = f.get("price_per_person"), f.get("total_amount"), f.get("traveller_count")
    cust, employer = norm_name(case.customer.get("name")), norm_name(case.customer.get("employer"))
    base = {"doc_id": d.doc_id, "doc_title": d.title, "doc_date": d.dated, "payee": f.get("payee_name"),
            "travellers": len(names), "price_per_person": fmt_gbp(price) if price is not None else None}
    rfi = lambda code, ev, why: (False, code, [d.doc_id] + ev, {**base, "missing": why})

    if not (d.dated and names and price and total and count and f.get("payee_name")):
        return rfi("RFI_PURPOSE_DOCUMENTS", [], "booking_incomplete")
    out, alerted = _supplier_payment(case, d, f), _alert_txns(case)
    if out is None or any(t["direction"] == "out" and not _allowed_outflow(case, cust, out, t) for t in alerted):
        return None
    if count != len(names) or not _pence_eq(price * count, total):
        return rfi("RFI_PURPOSE_DOCUMENTS", [], "booking_does_not_add_up")
    travellers = {norm_name(n) for n in names}
    if not cust or cust not in travellers:
        return rfi("RFI_PURPOSE_DOCUMENTS", [], "customer_not_on_booking")

    paid, explained = set(), []
    for t in alerted:
        if t["direction"] != "in":
            continue
        payer = norm_name(t.get("counterparty_name"))
        if payer == cust or (employer and payer == employer):
            continue
        if payer not in travellers:
            return rfi("RFI_COUNTERPARTY_DETAILS", [t["transaction_id"]], "payer_not_on_booking")
        if not _pence_eq(t["amount_gbp"], price) or payer in paid or t["booked_at"][:10] < d.dated:
            return rfi("RFI_PURPOSE_DOCUMENTS", [t["transaction_id"]], "payment_not_as_booked")
        paid.add(payer)
        explained.append(t["transaction_id"])

    return (True, "COLLECTION_DOCUMENTED", [d.doc_id, out["transaction_id"]] + explained,
            {**base, "amount": fmt_gbp(total), "txn_id": out["transaction_id"], "txn_date": out["booked_at"][:10]})


def check_collection_documented(case, alert, policy):
    if alert["rule_id"] not in APPLIES_TO:
        return None
    bookings = [d for d in case.docs_by_window_end() if d.type == DOC_TYPE]
    results = [r for r in (_assess(case, d) for d in bookings) if r is not None]
    if not results:
        return None
    passed, code, ev, facts = next((r for r in results if r[0]), results[0])
    return Proof(alert["alert_id"], passed, code, ev, {**facts, "rule": alert["rule_id"]}, "collection_documented")
