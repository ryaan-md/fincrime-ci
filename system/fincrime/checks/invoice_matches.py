"""INVOICE_MATCHES: an invoice, contract or payment file matches the amount, payee and date of the alert's money.

Every transaction the alert fired on must be explained, or the alert is not proven:
  out  -> an invoice-family document (dealer / hospital / supplier / carrier invoice): payee = counterparty, buyer
          (if stated) = customer, amount equal to the penny (paid amount too, if stated), pay-to account and
          invoice / PO reference consistent with the transaction, document dated on/before the payment;
       -> a contract_schedule stage: supplier = counterparty, stage amount equal, paid on its due date;
       -> a payment_file row (contractor files only): payee name and amount equal.
  in   -> a loan_agreement advance: lender = payer, borrower = customer, principal equal, and an invoice-matched
          payment on/after it (bank car loan);
       -> a freight_invoices line: exporter = payer, amount equal, invoice ref in the payment reference;
       -> client's own funding that is paid on (within 2 days) to an invoice-matched payment.
TM01 is proven when the inflows left unexplained stay within the alert's own limit (limit_multiple x baseline).
Every document must be on file by the window end and explains at most one transaction. Invoice amounts must EQUAL the payment ("covers" is used only
for PO value >= supplier invoice and freight receipts >= carrier payout), so a shifted amount never closes.
payments_fx: a held payment >= policy.INVOICE_REQUIRED_GBP to anyone but the client must itself be invoice-matched.
Car loan with the loan verified but no dealer invoice -> RFI_PURPOSE_DOCUMENTS (POLICY_TABLE: bare loan + dealer).
Any substantive mismatch -> None, so the arbiter falls back to its default (no close)."""
import re
from datetime import date
from ..model import Proof
from .util import amt_eq, fmt_gbp, norm_name

RULES = {"TM01", "TM02", "TM03", "TM04", "TM06", "TM08"}
INVOICE_TYPES = ("dealer_invoice", "hospital_invoice", "supplier_invoice", "carrier_invoice")
OWN_FUNDING_DAYS = 2
_SUFFIX = {"company": "co", "incorporated": "inc", "corporation": "corp"}
REF_RE = re.compile(r"\b(?:INV(?:OICE)?|PO|CARRIER|FREIGHT|ORDER)\.?\s*(?:NO\.?\s*)?#?\s*(\d{3,})\b", re.I)
ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$", re.M)
GBP_RE = re.compile(r"£\s?(\d[\d,]*(?:\.\d{1,2})?)")


def _n(s):
    words = norm_name(s).split()
    return " ".join(_SUFFIX.get(w, w) for w in words)


def _same(a, b):
    return bool(a) and bool(b) and _n(a) == _n(b)


def _names(*vals):
    """Candidate party names from doc fields; a trailing ', <place>' left by a parser is dropped."""
    out = []
    for v in vals:
        if isinstance(v, str) and v.strip():
            out += [v, v.split(",")[0]]
    return out


def _any_same(cands, name):
    return any(_same(c, name) for c in cands)


def _day(t):
    return t["booked_at"][:10]


def _days(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _acct(s):
    d = re.sub(r"\D", "", s or "")
    return d[-4:] if len(d) >= 4 else ""


def _refs(s):
    return {m.group(1) for m in REF_RE.finditer(s or "")}


def _on_file(case, d, t):
    return d.dated and d.dated <= case.window["to"] and d.dated <= _day(t)


# ---- out: invoice-family documents ------------------------------------------------------------------------------
def _invoice_matches(case, d, t):
    f = d.fields
    if t["direction"] != "out" or not _on_file(case, d, t):
        return False
    amount = f.get("total_amount", f.get("amount_due"))
    if not amt_eq(amount, t["amount_gbp"]):
        return False
    if f.get("paid_amount") is not None and not amt_eq(f["paid_amount"], t["amount_gbp"]):
        return False
    if not _any_same(_names(f.get("pay_to_name"), f.get("seller_name"), f.get("doc_issuer"), f.get("issuer")),
                     t["counterparty_name"]):
        return False
    buyer = f.get("buyer_name")
    if buyer and not _same(buyer, case.customer.get("name")):
        return False
    pay_acct, tx_acct = _acct(f.get("pay_to_account")), _acct(t.get("counterparty_account"))
    if pay_acct and tx_acct and pay_acct != tx_acct:
        return False
    doc_refs = _refs(f.get("invoice_ref")) | _refs(f.get("po_ref"))
    tx_refs = _refs(t.get("reference"))
    if doc_refs and tx_refs and not doc_refs & tx_refs:
        return False
    if d.type == "hospital_invoice" and not _hospital_consistent(case, d, t):
        return False
    return True


def _hospital_consistent(case, d, t):
    """Patient must be named; a support chat on file must not name another patient or provider."""
    patient = d.fields.get("patient_name")
    if not patient:
        return False
    for chat in case.docs_of("support_chat"):
        cf = chat.fields
        if cf.get("stated_person") and not _same(cf["stated_person"], patient):
            return False
        if cf.get("stated_provider") and not _same(cf["stated_provider"], t["counterparty_name"]):
            return False
        if cf.get("stated_location") and d.fields.get("pay_to_city") and not _same(cf["stated_location"],
                                                                                    d.fields["pay_to_city"]):
            return False
    return True


def _po_backs(case, inv):
    """A supplier invoice that names a PO needs that PO on file, worth at least the invoice and not addressed to
    anyone but the client (a PO to "Sales Team" names no addressee)."""
    po_ref = inv.fields.get("po_ref")
    if inv.type != "supplier_invoice" or not po_ref:
        return True
    for po in case.docs_of("purchase_order"):
        pf = po.fields
        if (_refs(pf.get("po_ref")) & _refs(po_ref) and po.dated and po.dated <= case.window["to"] and po.dated <= inv.dated
                and (not pf.get("seller_name") or _same(pf["seller_name"], case.customer.get("name")))
                and pf.get("total_amount") is not None and pf["total_amount"] + 0.005 >= inv.fields["total_amount"]):
            return True
    return False


def _supply_contract_ok(case, inv, t):
    """A supply contract on file with this supplier must name the client as buyer and predate the payment."""
    for con in case.docs_of("supply_contract"):
        cf = con.fields
        if _any_same(_names(cf.get("supplier_name"), cf.get("counterparty_name")), t["counterparty_name"]):
            if not (_same(cf.get("buyer_name"), case.customer.get("name")) and _on_file(case, con, t)):
                return False
    return True


# ---- out: contract schedule and payment file --------------------------------------------------------------------
def _stage_for(case, t, used):
    for d in case.docs_of("contract_schedule"):
        f = d.fields
        if not (_on_file(case, d, t) and _any_same(_names(f.get("supplier_name"), f.get("counterparty_name")),
                                                    t["counterparty_name"])):
            continue
        country = f.get("supplier_country")
        if country and t.get("counterparty_country") and not _same(country, t["counterparty_country"]):
            continue
        for s in f.get("stages") or []:
            key = (d.doc_id, s.get("stage"))
            if key not in used and amt_eq(s.get("amount"), t["amount_gbp"]) and s.get("due") == _day(t):
                return d, key
    return None, None


def _table(text):
    rows = [[c.strip() for c in m.group(1).split("|")] for m in ROW_RE.finditer(text or "")]
    rows = [r for r in rows if not all(set(c) <= set("-: ") for c in r)]
    if len(rows) < 2:
        return [], []
    return [h.lower() for h in rows[0]], rows[1:]


def _payment_file_rows(d):
    """Contractor payment files only: (name, amount) per row. A file with an address column is not a contractor
    file (commission files share addresses across payees), so it explains nothing here."""
    head, rows = _table(d.text)
    if not head or head[0] not in ("contractor", "payee") or "contract" not in head or "address" in head:
        return []
    out = []
    for r in rows:
        if len(r) != len(head):
            return []
        m = GBP_RE.search(r[head.index("amount")]) if "amount" in head else None
        if not m:
            return []
        out.append((r[0], round(float(m.group(1).replace(",", "")), 2)))
    return out


def _file_row_for(case, t, used):
    for d in case.docs_of("payment_file"):
        if not _on_file(case, d, t):
            continue
        for i, (name, amount) in enumerate(_payment_file_rows(d)):
            key = (d.doc_id, i)
            if key not in used and _same(name, t["counterparty_name"]) and amt_eq(amount, t["amount_gbp"]):
                return d, key
    return None, None


# ---- in: loan advance and freight receipts ----------------------------------------------------------------------
def _loan_for(case, t, used):
    for d in case.docs_of("loan_agreement"):
        f = d.fields
        if (d.doc_id not in used and _on_file(case, d, t) and _same(f.get("borrower_name"), case.customer.get("name"))
                and _any_same(_names(f.get("lender_name"), f.get("counterparty_name")), t["counterparty_name"])
                and amt_eq(f.get("principal_amount"), t["amount_gbp"])):
            return d
    return None


def _freight_line_for(case, t, used):
    for d in case.docs_of("freight_invoices"):
        f = d.fields
        # The client's own invoice list may be compiled after the receipts; the payer quoting the line's invoice
        # reference shows the invoice existed when it paid, so only "on file by the window end" is required.
        if not (d.dated and d.dated <= case.window["to"] and _same(f.get("doc_issuer"), case.customer.get("name"))):
            continue
        for i, li in enumerate(f.get("line_items") or []):
            key = (d.doc_id, i)
            if (key not in used and _same(li.get("party"), t["counterparty_name"])
                    and amt_eq(li.get("amount"), t["amount_gbp"]) and _refs(li.get("ref")) & _refs(t.get("reference"))):
                return d, key
    return None, None


# ---- per-case matching ------------------------------------------------------------------------------------------
def _match_outs(case, outs):
    """{txn_id: doc} for out transactions matched to an invoice, a schedule stage or a payment-file row."""
    matched, used = {}, set()
    invoices = [d for d in case.docs_of(*INVOICE_TYPES)]
    for t in outs:
        inv = next((d for d in invoices if d.doc_id not in used and _invoice_matches(case, d, t)
                    and _po_backs(case, d) and _supply_contract_ok(case, d, t)), None)
        if inv:
            used.add(inv.doc_id)
            matched[t["transaction_id"]] = inv
            continue
        d, key = _stage_for(case, t, used)
        if not d:
            d, key = _file_row_for(case, t, used)
        if d:
            used.add(key)
            matched[t["transaction_id"]] = d
    return matched


def _client(case, t):
    return _same(t["counterparty_name"], case.customer.get("name"))


def check_invoice_matches(case, alert, policy):
    rule = alert["rule_id"]
    txs = case.alert_txns.get(alert["alert_id"], [])
    if rule not in RULES or not txs:
        return None
    if not case.docs_of(*INVOICE_TYPES, "contract_schedule", "payment_file", "loan_agreement"):
        return None
    all_out = [t for t in case.txns if t["direction"] == "out" and case.window["from"] <= _day(t) <= case.window["to"]]
    matched_out = _match_outs(case, all_out)
    held = case.held_payment
    if (case.institution == "payments_fx" and held and held["amount_gbp"] >= policy.INVOICE_REQUIRED_GBP
            and not _client(case, held) and held["transaction_id"] not in matched_out):
        return None

    explained, used, funded, unexplained_in = {}, set(), set(), 0.0    # txn_id -> (doc, the transaction the doc states)
    for t in txs:
        tid = t["transaction_id"]
        if t["direction"] == "out":
            if tid in matched_out:
                explained[tid] = (matched_out[tid], t)
            else:
                return _loan_rfi(case, alert, txs)
            continue
        loan = _loan_for(case, t, used)
        if loan:
            spent = [x for x in all_out if x["transaction_id"] in matched_out and x["transaction_id"] not in funded
                     and _day(x) >= _day(t) and matched_out[x["transaction_id"]].type in INVOICE_TYPES]
            if not spent:             # a loan alone is not an invoice: it must fund an invoice-matched payment
                return _loan_rfi(case, alert, txs)
            used.add(loan.doc_id)
            funded.add(spent[0]["transaction_id"])
            explained[tid] = (loan, t)
            continue
        d, key = _freight_line_for(case, t, used)
        if d:
            used.add(key)
            explained[tid] = (d, t)
            continue
        if _client(case, t) and rule in ("TM01", "TM02"):
            paid_on = [x for x in all_out if x["transaction_id"] in matched_out and x["transaction_id"] not in funded
                       and 0 <= _days(_day(t), _day(x)) <= OWN_FUNDING_DAYS
                       and x["amount_gbp"] <= t["amount_gbp"] + 0.005]
            if paid_on:
                funded.add(paid_on[0]["transaction_id"])
                explained[tid] = (matched_out[paid_on[0]["transaction_id"]], paid_on[0])
                continue
            if rule == "TM02":
                continue          # TM02 counts only money from other parties
        if rule != "TM01":
            return None
        unexplained_in += t["amount_gbp"]

    if not explained:
        return None
    if rule == "TM01":
        m = alert.get("measured", {})
        if unexplained_in > (m.get("limit_multiple") or 0) * (m.get("baseline_gbp") or 0):
            return None
    if rule == "TM02" and not _payout_within_receipts(txs, explained):
        return None
    return _proof(alert, txs, explained)


def _payout_within_receipts(txs, explained):
    """Freight forwarder: what goes to the carrier must not exceed the documented exporter receipts."""
    def total(direction, dtype):
        return sum(t["amount_gbp"] for t in txs if t["direction"] == direction and t["transaction_id"] in explained
                   and explained[t["transaction_id"]][0].type == dtype)
    carrier = total("out", "carrier_invoice")
    return not carrier or carrier <= total("in", "freight_invoices") + 0.005


def _loan_rfi(case, alert, txs):
    """Car loan: the advance is verified but the dealer payment has no invoice on file -> ask for it."""
    if case.docs_of(*INVOICE_TYPES):
        return None
    loans = [(t, _loan_for(case, t, set())) for t in txs if t["direction"] == "in"]
    loans = [(t, d) for t, d in loans if d]
    if not loans:
        return None
    t, d = loans[0]
    return Proof(alert["alert_id"], False, "RFI_PURPOSE_DOCUMENTS", [d.doc_id, t["transaction_id"]],
                 {"doc_id": d.doc_id, "doc_title": d.title, "doc_date": d.dated, "rule": alert["rule_id"]},
                 "invoice_matches")


def _proof(alert, txs, explained):
    """Evidence: payment documents first (their amount is the one that must match), then supporting documents,
    then the transactions. Facts name the lead document and the transaction whose amount it states."""
    order = sorted([t for t in txs if t["transaction_id"] in explained], key=lambda t: (-t["amount_gbp"],
                                                                                        t["transaction_id"]))
    pairs = [explained[t["transaction_id"]] for t in order]
    docs = []
    for d, _ in sorted(pairs, key=lambda p: p[0].type not in INVOICE_TYPES + ("contract_schedule", "payment_file")):
        if d not in docs:
            docs.append(d)
    top = docs[0]
    stated = next(x for d, x in pairs if d is top)
    tx_ids = []
    for t in [stated] + order + [x for _, x in pairs]:
        if t["transaction_id"] not in tx_ids:
            tx_ids.append(t["transaction_id"])
    return Proof(alert["alert_id"], True, "INVOICE_MATCHES", [d.doc_id for d in docs] + tx_ids,
                 {"doc_id": top.doc_id, "doc_title": top.title, "doc_date": top.dated,
                  "amount": fmt_gbp(stated["amount_gbp"]), "txn_id": stated["transaction_id"],
                  "txn_date": _day(stated), "rule": alert["rule_id"]}, "invoice_matches")
