"""LIST_MATCH_DIFFERENT_PARTY (TM22): the evidence shows the counterparty is NOT the listed party.

POLICY.md (all four institutions): "Close a list match only when the evidence shows the counterparty is a different
party. If nothing on file settles it either way and nothing else is concerning, send an RFI. If the evidence shows the
listed party is the counterparty or controls it, escalate." and "A match on one detail alone does not prove it is the
same party."

Two ways the evidence can settle it, per payment the alert fired on:
1. Company counterparty: the payee's own company register on file (registered office, officers, people with
   significant control) shows no identity fact of the listed party: the registered office is a different address
   (e.g. another unit), no officer or PSC is the listed person while active at the payment date (a listed officer who
   resigned before it passes only when a PSC record dated on or after the payment shows no control at that date), and
   no listed identifier appears. A receipt or invoice from the payee for the exact amount, on file and not at the listed
   address, ties the payment to that company.
2. Individual counterparty: a gift letter from the payer (same name as the listed party, which is what matched) gives
   a date of birth that differs from the listed one, is addressed to the customer, states the exact amount and is dated
   on or before the payment.
Any identity fact that matches the listed party -> None (the listed_party scanner or the RFI default decides).
Explanation present but incomplete (no amount-matching payee document, letter without a DOB) -> RFI_COUNTERPARTY_DETAILS."""
import re
from ..model import Proof
from .util import amt_eq, fmt_gbp, norm_name

REGISTER_TYPES = ("company_register_extract", "significant_control_register")
PAYEE_DOC_TYPES = ("receipt", "sale_receipt", "invoice", "supplier_invoice")
LETTER_TYPES = ("gift_letter",)
RFI = "RFI_COUNTERPARTY_DETAILS"


def _addr(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _same_address(a, b):
    """True when one address contains the other once case, punctuation and spacing are ignored."""
    a, b = _addr(a), _addr(b)
    return bool(a and b) and (a in b or b in a)


def _issuer(d):
    seller = d.fields.get("seller") or d.fields.get("seller_name")
    return norm_name(seller or (d.title.rsplit(" from ", 1)[1] if " from " in d.title else ""))


def _amount_matches(d, amount):
    stated = next((v for v in (d.fields.get("amount"), d.fields.get("total_amount"))
                   if isinstance(v, (int, float)) and not isinstance(v, bool)), None)
    if stated is not None:
        return amt_eq(stated, amount)
    return any(amt_eq(a, amount) for a in d.amounts)


def _payments(case, alert):
    txs = case.alert_txns.get(alert["alert_id"], []) or ([case.held_payment] if case.held_payment else [])
    return sorted((t for t in txs if t), key=lambda t: (t["booked_at"], t["transaction_id"]))


def _active_on(start, end, day):
    return (start is None or start <= day) and (end is None or end >= day)


def _identifier_hit(entry, docs):
    ids = [str(v) for v in (entry.get("identifiers") or {}).values() if v]
    return any(i in d.text for i in ids for d in docs)


def _listed_rows(rows, want_name, want_born):
    """Register rows that could be the listed person: same name and the same (or no stated) month of birth."""
    return [r for r in rows if norm_name(r.get("name")) == want_name and (not want_born or not r.get("born") or r["born"] == want_born)]


def _company_proof(case, alert, entry, tx, end):
    day, payee = tx["booked_at"][:10], tx.get("counterparty_name") or ""
    payee_n = norm_name(payee)
    regs = [d for d in end if d.type in REGISTER_TYPES and payee_n and norm_name(d.fields.get("company_name")) == payee_n]
    if not regs or norm_name(entry.get("name")) == payee_n or _identifier_hit(entry, regs):
        return None
    want_name, want_born = norm_name(entry.get("name")), (entry.get("date_of_birth") or "")[:7] or None
    officers = [o for d in regs for o in d.fields.get("officers") or []]
    pscs = [(d, p) for d in regs for p in d.fields.get("psc") or []]
    if not officers and not pscs:
        return None
    for d in regs:
        if entry.get("address") and _same_address(d.fields.get("registered_office"), entry["address"]):
            return None
    if "address" in (alert.get("measured") or {}).get("matched_on", "") and not any(
            d.fields.get("registered_office") for d in regs):
        return None
    listed_off = _listed_rows(officers, want_name, want_born)
    listed_psc = _listed_rows([p for _, p in pscs], want_name, want_born)
    if any(_active_on(o.get("appointed"), o.get("resigned"), day) for o in listed_off):
        return None
    if any(_active_on(p.get("notified"), p.get("ceased"), day) for p in listed_psc):
        return None
    if listed_off and not any(d.dated >= day for d, _ in pscs):
        return None      # a former officer: need a PSC record covering the payment date to show no control then
    if not pscs:
        return None      # who controls the payee is not on file
    reg = regs[0]
    facts = {"entry_id": entry["entry_id"], "listed_name": entry.get("name"), "counterparty": payee,
             "register_id": reg.doc_id, "registered_office": reg.fields.get("registered_office"),
             "listed_address": entry.get("address"), "payment_date": day, "txn_id": tx["transaction_id"],
             "txn_date": day, "amount": fmt_gbp(tx["amount_gbp"]), "variant": "company_register",
             "rule": alert["rule_id"]}
    if listed_off:
        facts["listed_officer_resigned"] = max(o.get("resigned") or "" for o in listed_off)
    pay_docs = [d for d in end if d.type in PAYEE_DOC_TYPES and _issuer(d) == payee_n]
    if any(_same_address(d.fields.get("seller_address"), entry.get("address")) for d in pay_docs) or \
            _identifier_hit(entry, pay_docs):
        return None
    pd = next((d for d in pay_docs if d.dated <= day and _amount_matches(d, tx["amount_gbp"])), None)
    ev = [r.doc_id for r in regs]
    if not pd:
        return Proof(alert["alert_id"], False, RFI, ev + [entry["entry_id"], tx["transaction_id"]],
                     {**facts, "doc_id": reg.doc_id, "doc_title": reg.title, "doc_date": reg.dated},
                     "list_match_different_party")
    facts.update({"doc_id": pd.doc_id, "doc_title": pd.title, "doc_date": pd.dated})
    return Proof(alert["alert_id"], True, "LIST_MATCH_DIFFERENT_PARTY",
                 [pd.doc_id] + ev + [entry["entry_id"], tx["transaction_id"]], facts, "list_match_different_party")


def _letter_proof(case, alert, entry, tx, end):
    day, payer = tx["booked_at"][:10], tx.get("counterparty_name") or ""
    want_name, dob = norm_name(entry.get("name")), entry.get("date_of_birth")
    if not want_name or norm_name(payer) != want_name:
        return None
    letters = [d for d in end if d.type in LETTER_TYPES and norm_name(d.fields.get("giver_name") or d.fields.get("issuer")) == want_name]
    if not letters:
        return None
    if not dob or any(dob in d.dates or d.fields.get("giver_dob") == dob for d in letters) or \
            _identifier_hit(entry, letters):
        return None
    who = norm_name(case.customer.get("name"))
    for d in letters:
        if not d.fields.get("giver_dob"):
            continue
        if d.dated <= day and who and norm_name(d.fields.get("recipient_name")) == who and \
                amt_eq(d.fields.get("amount"), tx["amount_gbp"]):
            facts = {"doc_id": d.doc_id, "doc_title": d.title, "doc_date": d.dated, "entry_id": entry["entry_id"],
                     "listed_name": entry.get("name"), "counterparty": payer, "letter_dob": d.fields["giver_dob"],
                     "listed_dob": dob, "amount": fmt_gbp(tx["amount_gbp"]), "txn_id": tx["transaction_id"],
                     "txn_date": day, "payment_date": day, "variant": "dob_differs", "rule": alert["rule_id"]}
            return Proof(alert["alert_id"], True, "LIST_MATCH_DIFFERENT_PARTY",
                         [d.doc_id, entry["entry_id"], tx["transaction_id"]], facts, "list_match_different_party")
    d = letters[0]
    return Proof(alert["alert_id"], False, RFI, [d.doc_id, entry["entry_id"], tx["transaction_id"]],
                 {"doc_id": d.doc_id, "doc_title": d.title, "doc_date": d.dated, "entry_id": entry["entry_id"],
                  "listed_name": entry.get("name"), "counterparty": payer, "rule": alert["rule_id"]},
                 "list_match_different_party")


def check_list_match_different_party(case, alert, policy):
    if alert["rule_id"] != "TM22":
        return None
    entry = case.list_entries.get((alert.get("measured") or {}).get("list_entry"))
    txs = _payments(case, alert)
    if not entry or not txs:
        return None
    end = case.docs_by_window_end()
    proofs = []
    for tx in txs:
        p = _company_proof(case, alert, entry, tx, end) or _letter_proof(case, alert, entry, tx, end)
        if p is None:
            return None
        proofs.append(p)
    failed = next((p for p in proofs if not p.passed), None)
    if failed:
        return failed
    first = proofs[0]
    ev = [i for p in proofs for i in p.evidence]
    return Proof(alert["alert_id"], True, first.code, list(dict.fromkeys(ev)), first.facts, first.checker)
