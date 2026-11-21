"""ESC_INVOICE_MISMATCH (payments_fx): the invoice that matches the held payment is billed to someone else.

POLICY.md (payments_fx) "Policy settings": "Payments of £10,000 or more to anyone other than the client need an
invoice or contract on file before they are released." Code table: ESC_INVOICE_MISMATCH = "The invoices do not
support the payments." An invoice made out to a buyer other than the client is not the client's invoice, so it does
not support the client's payment, and a payment backed only by someone else's paperwork is not explained.
Fires only when the institution sets INVOICE_BUYER_MUST_BE_CLIENT and ALL hold for an invoice on file by the window end:
  1. it states the exact amount (to the penny) of an outgoing payment an alert fired on, so it is the paperwork
     offered for that payment;
  2. it names a buyer (Bill to / Buyer / Customer), and that buyer is not the client.
Names compare tolerant of noise: case, punctuation, "Ltd"/"Limited", a trailing legal form, and a ", note" or
"(note)" after the name. Money coming in is not checked: an invoice the client issued names its own customer."""
import re
from ..model import Flag
from ..checks.util import amt_eq, fmt_gbp, norm_name

INVOICE_TYPES = ("supplier_invoice", "invoice", "carrier_invoice", "dealer_invoice")
LEGAL_FORMS = ("ltd", "plc", "llp", "llc", "inc", "co", "gmbh", "sarl", "sa", "bv")


def _party(name):
    """Comparable party name: the part before any ', note' or '(note)', normalised, trailing legal form dropped."""
    s = norm_name(re.split(r"[,(]", name or "", maxsplit=1)[0])
    words = s.split()
    while len(words) > 1 and words[-1] in LEGAL_FORMS:
        words.pop()
    return " ".join(words)


def scan_invoice_buyer(case, policy):
    if not getattr(policy, "INVOICE_BUYER_MUST_BE_CLIENT", False):
        return []
    client = _party(case.customer.get("name"))
    pays = sorted({t["transaction_id"]: t for a in case.alerts for t in case.alert_txns.get(a["alert_id"], [])
                   if t.get("direction") == "out"}.values(), key=lambda t: (t["booked_at"], t["transaction_id"]))
    if not client or not pays:
        return []
    out = []
    for d in case.docs_by_window_end():
        buyer = d.fields.get("buyer_name")
        if d.type not in INVOICE_TYPES or not buyer or _party(buyer) == client:
            continue
        t = next((t for t in pays if amt_eq(d.fields.get("total_amount"), t["amount_gbp"])), None)
        if t is None:
            continue
        out.append(Flag("ESC_INVOICE_MISMATCH", [d.doc_id, t["transaction_id"]],
                        {"doc_id": d.doc_id, "doc_title": d.title, "doc_date": d.dated, "buyer": buyer,
                         "amount": fmt_gbp(t["amount_gbp"]), "txn_id": t["transaction_id"],
                         "txn_date": t["booked_at"][:10]},
                        "payments_fx_invoice_buyer", 3))
    return out
