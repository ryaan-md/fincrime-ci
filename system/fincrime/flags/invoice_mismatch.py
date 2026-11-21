"""ESC_INVOICE_MISMATCH: the invoices on file do not support the payments.

POLICY.md code table: ESC_INVOICE_MISMATCH = "The invoices do not support the payments."
An invoice that merely differs in amount is not enough (a purchase can be funded with a margin on top of the invoice),
and neither is a late date on its own. This fires only when ALL THREE hold for an invoice document on file by the
window end:
  1. every invoice in it is dated AFTER the largest payment the alerts fired on (the payment the invoices would have
     to explain), so the paperwork post-dates the money;
  2. no invoice amount, and not their total, equals any flagged payment to the penny; and
  3. the goods invoiced share no word with the customer's declared business activity (e.g. mobile phone handsets
     for a firm that imports catering equipment). Not checked, so no flag, when the business activity is unknown.
Paperwork produced after the money moved, for amounts that match none of it, for goods outside the customer's
business, does not support the payments."""
import re
from ..model import Flag
from ..checks.util import amt_eq
from ..loader import parse_amounts, parse_dates

INVOICE_TYPES = {"invoice", "supplier_invoice", "supplier_invoices"}
STOP = {"with", "from", "that", "this", "their", "into", "for", "and", "the", "other", "order", "orders", "goods",
        "supply", "supplies", "supplier", "suppliers", "services", "service", "items", "item", "products", "product",
        "stock", "units", "unit", "sells", "buys", "imports", "import", "exports", "export", "trading", "trade",
        "company", "business", "overseas", "manufacturers", "manufacturer", "first", "shipment", "consignment"}


def _words(text):
    out = set()
    for w in re.findall(r"[a-z]+", (text or "").lower()):
        if len(w) >= 4 and w not in STOP:
            out.add(w[:-1] if w.endswith("s") and len(w) > 4 else w)
    return out


def _tables(text):
    block = []
    for line in (text or "").splitlines() + [""]:
        if line.strip().startswith("|"):
            block.append([c.strip() for c in line.strip().strip("|").split("|")])
            continue
        if len(block) >= 2:
            yield [h.lower() for h in block[0]], [r for r in block[1:] if not all(set(c) <= set("-: ") for c in r)]
        block = []


def _invoice_lines(doc):
    """[(date, amount, goods)] per invoice line: table rows if there is an amount column, else the document itself."""
    lines = []
    for header, rows in _tables(doc.text):
        ai = next((i for i, h in enumerate(header) if "amount" in h or "total" in h), None)
        if ai is None:
            continue
        di = next((i for i, h in enumerate(header) if "date" in h), None)
        gi = next((i for i, h in enumerate(header) if "goods" in h or "description" in h or "item" in h), None)
        for r in rows:
            cell = lambda i: r[i] if i is not None and i < len(r) else ""
            amts, dates = parse_amounts(cell(ai)), parse_dates(cell(di))
            if amts:
                lines.append((dates[0] if dates else doc.dated, amts[0], cell(gi)))
    if not lines and doc.amounts:
        goods = doc.fields.get("goods") or doc.fields.get("description") or ""
        lines = [(doc.dated, a, goods) for a in doc.amounts]
    return lines


def scan_invoice_mismatch(case, policy):
    txs = sorted({t["transaction_id"]: t for a in case.alerts for t in case.alert_txns.get(a["alert_id"], [])}.values(),
                 key=lambda t: (t["booked_at"], t["transaction_id"]))
    business = _words(case.customer.get("business_activity"))
    if not txs or not business:
        return []
    big = max(txs, key=lambda t: (float(t.get("amount_gbp") or 0), t["transaction_id"]))
    big_day = big["booked_at"][:10]
    pays = [float(t["amount_gbp"]) for t in txs if t.get("amount_gbp") is not None]
    out = []
    for d in case.docs_by_window_end():
        if d.type not in INVOICE_TYPES:
            continue
        lines = _invoice_lines(d)
        goods = _words(" ".join(g for _, _, g in lines))
        if not lines or not goods:
            continue
        all_after = all(day and day > big_day for day, _, _ in lines)
        total = round(sum(a for _, a, _ in lines), 2)
        amounts = [a for _, a, _ in lines] + ([total] if len(lines) > 1 else [])
        any_match = any(amt_eq(a, p) for a in amounts for p in pays)
        unrelated = not (goods & business)
        if all_after and not any_match and unrelated:
            out.append(Flag("ESC_INVOICE_MISMATCH", [d.doc_id, big["transaction_id"]],
                            {"doc_id": d.doc_id, "doc_title": d.title, "invoice_count": len(lines), "invoice_total": total,
                             "first_invoice_date": min(x for x, _, _ in lines), "goods": lines[0][2],
                             "business_activity": case.customer.get("business_activity"),
                             "payment_date": big_day, "payment_gbp": float(big.get("amount_gbp") or 0),
                             "payment_id": big["transaction_id"]},
                            "invoice_mismatch", 3))
    return out
