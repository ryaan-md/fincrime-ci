"""Reason templates, one per reason code (all 26). Owner: T-RSN.

render(case, verdict, code, facts) -> 2-4 sentences that name THIS case's documents, amounts, dates and people and
state the grounds the reason code stands for. For an RFI the last sentence says exactly what to ask the customer for.

Where the facts come from, in order of preference:
  1. `facts` keys supplied by the checker (Proof.facts) or red-flag scanner (Flag.facts). FACT_KEYS lists the keys
     each template uses; any of them may be missing.
  2. The Case object itself: the alert `message` text (which carries the measured amounts, limits, counterparties and
     dates), the transactions the alerts fired on, the customer's declared profile, and the non-standard documents on
     file dated on or before the review-window end.
Never invent a fact: a clause that needs a value is dropped when the value is not available. No dev IDs, names or
amounts in this file (make leak). Deterministic: every list is sorted before use."""
import re

from ..policy.common import HIGH_RISK_COUNTRIES

RULE_NAMES = {"TM01": "activity above the declared profile", "TM02": "rapid movement of funds",
              "TM03": "repeated transactions just below a threshold", "TM04": "many-to-one or one-to-many payments",
              "TM05": "a large cash transaction", "TM06": "an undeclared higher-risk country",
              "TM07": "dormant account reactivation", "TM08": "third-party funding or payout",
              "TM10": "early fund redemption", "TM22": "an internal list match"}

# Facts keys each template reads when a checker or flag provides them (proposal for T-CHK-* / T-FLG-* owners).
# Shared by all: doc_id | doc_ids, doc_title, doc_date, amount, txn_id, txn_date, counterparty.
FACT_KEYS = {
    "COLLECTION_DOCUMENTED": ["payers", "payer_count", "total", "payee"],
    "BUSINESS_RECORDS_RECONCILE": ["period", "records_total", "deposits_total"],
    "DOCUMENTED_EVENT": ["event", "payer", "firm"],
    "INVOICE_MATCHES": ["invoice_no", "payee", "item"],
    "OWN_ACCOUNT": ["multiple", "account", "holder"],
    "GROUP_COMPANY": ["owner"],
    "THIRD_PARTY_DOCUMENTED": ["payer", "relationship"],
    "REFUND_OF_EARLIER_PAYMENT": ["original_amount", "original_date", "original_txn_id"],
    "RETURNING_CUSTOMER": ["reverified_on"],
    "LIST_MATCH_DIFFERENT_PARTY": ["entry_id", "listed_name", "difference"],
    "WITHIN_PROFILE": ["profile_note", "review_id", "review_date", "peak_months", "peak_figure"],
    "RFI_PURPOSE_DOCUMENTS": ["ask", "missing"],
    "RFI_BUSINESS_RECORDS": ["ask", "missing", "period"],
    "RFI_SOURCE_OF_FUNDS": ["ask", "missing"],
    "RFI_COUNTERPARTY_DETAILS": ["ask", "missing", "entry_id", "listed_name"],
    "RFI_UPDATED_PROFILE": ["multiple", "peak_months"],
    "ESC_MULE_ACTIVITY": ["victims", "report_count"],
    "ESC_STRUCTURING": ["quote"],
    "ESC_UNEXPLAINED_FUNDS": ["detail"],
    "ESC_PASS_THROUGH": ["linked", "officer"],
    "ESC_INVOICE_MISMATCH": ["invoice_amount", "mismatch"],
    "ESC_HIGH_RISK_CORRIDOR": ["country"],
    "ESC_ACCOUNT_MISUSE": ["detail"],
    "ESC_THIRD_PARTY_FUNDING": ["payer", "detail"],
    "ESC_EARLY_REDEMPTION": ["held_days", "detail"],
    "ESC_LISTED_PARTY": ["entry_id", "listed_name", "dob", "officer", "ceased_on", "detail"],
}

# Which alert's finding leads the reason, per code (first rule present wins, else the first alert).
LEAD_RULES = {
    "COLLECTION_DOCUMENTED": ["TM04", "TM02", "TM01"], "BUSINESS_RECORDS_RECONCILE": ["TM03", "TM01", "TM05"],
    "DOCUMENTED_EVENT": ["TM05", "TM01", "TM07", "TM08", "TM10"], "INVOICE_MATCHES": ["TM03", "TM06", "TM02", "TM01"],
    "OWN_ACCOUNT": ["TM01", "TM06", "TM02", "TM08"], "GROUP_COMPANY": ["TM08", "TM02", "TM01"],
    "THIRD_PARTY_DOCUMENTED": ["TM08", "TM01"], "REFUND_OF_EARLIER_PAYMENT": ["TM06", "TM05", "TM02", "TM08", "TM01"],
    "RETURNING_CUSTOMER": ["TM07", "TM01"], "LIST_MATCH_DIFFERENT_PARTY": ["TM22"],
    "WITHIN_PROFILE": ["TM10", "TM06", "TM01"],
    "RFI_PURPOSE_DOCUMENTS": ["TM02", "TM04", "TM03", "TM01"], "RFI_BUSINESS_RECORDS": ["TM03", "TM01", "TM05"],
    "RFI_SOURCE_OF_FUNDS": ["TM05", "TM01", "TM07", "TM08"], "RFI_COUNTERPARTY_DETAILS": ["TM22", "TM08"],
    "RFI_UPDATED_PROFILE": ["TM01"],
    "ESC_MULE_ACTIVITY": ["TM04", "TM02", "TM01"], "ESC_STRUCTURING": ["TM03", "TM05"],
    "ESC_UNEXPLAINED_FUNDS": ["TM02", "TM01", "TM05", "TM07"], "ESC_PASS_THROUGH": ["TM02", "TM08", "TM01"],
    "ESC_INVOICE_MISMATCH": ["TM03", "TM06", "TM01"], "ESC_HIGH_RISK_CORRIDOR": ["TM06"],
    "ESC_ACCOUNT_MISUSE": ["TM02", "TM04", "TM01"], "ESC_THIRD_PARTY_FUNDING": ["TM08", "TM10", "TM01"],
    "ESC_EARLY_REDEMPTION": ["TM10", "TM08"], "ESC_LISTED_PARTY": ["TM22"],
}

# Non-standard document types that usually carry each code's grounds (fallback when facts name no document).
CODE_DOC_TYPES = {
    "COLLECTION_DOCUMENTED": {"booking_confirmation", "payment_file"},
    "BUSINESS_RECORDS_RECONCILE": {"cash_collection_reports", "cash_reconciliation", "ticket_report", "takings_book",
                                   "management_accounts", "cash_handling_procedure", "freight_invoices"},
    "DOCUMENTED_EVENT": {"solicitor_letter", "insurer_letter", "vehicle_sale_receipt", "sale_receipt",
                         "settlement_letter", "completion_statement", "redundancy_letter", "advert",
                         "employment_contract", "insurance_schedule"},
    "INVOICE_MATCHES": {"invoice", "dealer_invoice", "hospital_invoice", "supplier_invoice", "supplier_invoices",
                        "carrier_invoice", "contract_schedule", "supply_contract", "purchase_order", "loan_agreement",
                        "payment_file"},
    "OWN_ACCOUNT": {"bank_statement"}, "GROUP_COMPANY": {"group_structure", "company_register_extract", "email"},
    "THIRD_PARTY_DOCUMENTED": {"gift_letter", "factoring_agreement", "remittance_advice"},
    "REFUND_OF_EARLIER_PAYMENT": {"venue_email", "credit_note", "cancellation_confirmation", "deposit_return_letter",
                                  "holding_deposit_receipt"},
    "RETURNING_CUSTOMER": {"reverification_record", "bank_statement"},
    "LIST_MATCH_DIFFERENT_PARTY": {"company_register_extract", "receipt", "invoice", "gift_letter"},
    "WITHIN_PROFILE": {"employment_contract", "customer_email", "customer_letter", "redundancy_letter"},
    "RFI_UPDATED_PROFILE": {"bank_statement"},
    "ESC_MULE_ACTIVITY": {"sending_bank_fraud_report", "support_chat"}, "ESC_STRUCTURING": {"staff_note"},
    "ESC_UNEXPLAINED_FUNDS": {"company_register_extract", "support_chat"},
    "ESC_PASS_THROUGH": {"company_register_extract", "consulting_contract"},
    "ESC_INVOICE_MISMATCH": {"invoice", "supplier_invoice", "supplier_invoices", "purchase_order", "payment_file"},
    "ESC_HIGH_RISK_CORRIDOR": {"support_chat", "invoice", "consulting_contract"},
    "ESC_ACCOUNT_MISUSE": {"device_export", "support_chat"}, "ESC_THIRD_PARTY_FUNDING": {"support_chat"},
    "ESC_EARLY_REDEMPTION": {"support_chat", "customer_email"},
    "ESC_LISTED_PARTY": {"significant_control_register", "company_register_extract", "gift_letter", "invoice"},
}
ANY_TYPE = frozenset()   # sentinel for _docs(): every non-standard type
FILLER_TYPES = {"due_diligence_summary", "proof_of_address", "contact_note"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
_SENT_SPLIT = re.compile(r"(?<=[.!?])(?<!\b[a-z]\.)\s+(?=[A-Z£(])")   # not after "o.o." style abbreviations
_HR = {c.lower() for c in HIGH_RISK_COUNTRIES}
_ESC_END = "so it is escalated to the MLRO without contacting the customer"


# ---------------------------------------------------------------- small formatters
def _gbp(x):
    if x is None or x == "" or isinstance(x, bool):
        return None
    if isinstance(x, str):
        s = x.strip()
        if s.startswith("£"):
            return s
        try:
            x = float(s.replace(",", ""))
        except ValueError:
            return None
    return f"£{float(x):,.2f}"


def _date(s):
    """ISO date or datetime -> '11 August 2026'. Anything else is returned unchanged."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(s or ""))
    if not m:
        return str(s) if s else None
    return f"{int(m.group(3))} {MONTHS[int(m.group(2)) - 1]} {m.group(1)}"


def _join(items):
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _cap(s):
    return s[:1].upper() + s[1:] if s else s


def _poss(name):
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def _v(docs, plural, singular):
    return plural if len(docs) > 1 else singular


def _sentences(text):
    return [s.strip() for s in _SENT_SPLIT.split(text.strip()) if s.strip()]


def _finish(parts):
    out = []
    for p in parts:
        p = (p or "").strip()
        if not p or re.search(r"\bNone\b", p):
            continue
        out.append(p if p[-1] in ".!?" else p + ".")
    if len(out) > 4:   # keep the conclusion / the ask, drop a middle detail
        out = out[:3] + out[-1:]
    return " ".join(out)


# ---------------------------------------------------------------- case accessors (facts derived from the Case only)
def _who(case):
    return case.customer.get("name") or ("the client" if case.institution == "payments_fx" else "the customer")


def _rules(case):
    return sorted({a["rule_id"] for a in case.alerts})


def _rules_phrase(case, exclude=()):
    return _join([f"{r} ({RULE_NAMES.get(r, r)})" for r in _rules(case) if r not in exclude])


def _lead_alert(case, code):
    alerts = sorted(case.alerts, key=lambda a: (a["rule_id"], a["alert_id"]))
    for r in LEAD_RULES.get(code, []):
        for a in alerts:
            if a["rule_id"] == r:
                return a
    return alerts[0] if alerts else None


def _finding(case, alert):
    """The alert's own first sentence (its message carries the measured amounts), personalised to the customer."""
    if not alert or not alert.get("message"):
        return None
    sents = _sentences(alert["message"])
    s = sents[0]
    if alert["rule_id"] in ("TM04", "TM07") and len(sents) > 1 and "£" not in s:
        s = s.rstrip(".") + "; " + sents[1][0].lower() + sents[1][1:]
    if alert["rule_id"] == "TM08" and s.count(";") >= 2:
        parts = [p.strip() for p in s.rstrip(".").split(";")]
        s = "; ".join(parts[:2]) + f"; and {len(parts) - 2} more payers did the same"
    who = case.customer.get("name")
    if who:
        s = re.sub(r"\bthe (account|client)\b", f"{_poss(who)} account", s, count=1)
    return _cap(s)


def _alert_txns(case):
    seen, out = set(), []
    for a in sorted(case.alerts, key=lambda a: a["alert_id"]):
        for t in case.alert_txns.get(a["alert_id"], []):
            if t["transaction_id"] not in seen:
                seen.add(t["transaction_id"]); out.append(t)
    return out


def _txn(case, txn_id):
    return next((t for t in case.txns if t["transaction_id"] == txn_id), None) if txn_id else None


def _biggest(txns, direction=None, pred=None):
    c = [t for t in txns if (direction is None or t.get("direction") == direction) and (pred is None or pred(t))]
    c.sort(key=lambda t: (-float(t.get("amount_gbp") or 0), t.get("booked_at", ""), t["transaction_id"]))
    return c[0] if c else None


_CHANNEL = {"cash_deposit": "cash deposit", "cash_topup": "cash top-up", "cash_withdrawal": "cash withdrawal",
            "fund_redemption": "fund redemption", "fund_subscription": "fund purchase", "card": "card payment",
            "direct_debit": "direct debit", "standing_order": "standing order"}


def _txn_phrase(t, with_id=False):
    """'the £13,000.00 cash deposit on 11 August 2026' / 'the £2,400.00 payment to X (Lebanon) on 8 August 2026'."""
    if not t:
        return None
    amt, when = _gbp(t.get("amount_gbp")), _date(t.get("booked_at"))
    ch = t.get("channel", "")
    cp = t.get("counterparty_name")
    ctry = t.get("counterparty_country")
    where = f" ({ctry})" if ctry and ctry != "United Kingdom" else ""
    if ch in _CHANNEL:
        kind = _CHANNEL[ch]
        if cp and ch in ("card", "direct_debit", "standing_order"):
            kind += f" to {cp}{where}"
    elif t.get("direction") == "in":
        kind = ("international transfer" if ch == "swift" else "transfer") + (f" from {cp}{where}" if cp else "")
    else:
        kind = ("international payment" if ch == "swift" else "payment") + (f" to {cp}{where}" if cp else "")
    s = f"the {amt} {kind} on {when}"
    return s + (f" ({t['transaction_id']})" if with_id else "")


def _docs(case, f, code, types=None):
    """Documents the reason relies on: facts doc_ids first; else non-standard docs of the code's types on file by the
    window end. Only documents that exist in the case are ever returned."""
    by_id = {d.doc_id: d for d in case.docs}
    ids = list(f.get("doc_ids") or []) + ([f["doc_id"]] if f.get("doc_id") else [])
    picked = []
    for i in ids:
        if i in by_id and by_id[i] not in picked:
            picked.append(by_id[i])
    if picked:
        return picked
    want = types if types is not None else CODE_DOC_TYPES.get(code, set())
    any_type = want is ANY_TYPE
    own = (case.customer.get("name") or "").lower()
    end = case.window.get("to", "9999")
    out = []
    for d in case.docs:
        if d.type in FILLER_TYPES or d.dated > end or (not any_type and d.type not in want):
            continue
        if d.type == "company_register_extract" and own and own in (d.title or "").lower():
            continue
        out.append(d)
    return sorted(out, key=lambda d: (d.dated, d.doc_id))


def _fact_docs(case, f):
    """Only the documents a checker or flag named in facts (no type fallback)."""
    return _docs(case, f, "", set()) if (f.get("doc_id") or f.get("doc_ids")) else []


def _nothing(case, f, positive="shows", negative="does not show", plural_negative="do not show"):
    """'Nothing on file shows' -> or, when the checker named the documents it read, '<docs> do(es) not show'."""
    docs = _fact_docs(case, f)
    if docs:
        return f"{_docs_phrase(docs)} {_v(docs[:2], plural_negative, negative)}"
    return f"Nothing on file {positive}"


def _doc_phrase(d, dated=True):
    title = (d.title or d.type.replace("_", " ")).strip()
    return f"{_cap(title)} ({d.doc_id}{', dated ' + _date(d.dated) if dated and d.dated else ''})"


def _docs_phrase(docs, limit=2):
    return _join([_doc_phrase(d, dated=(i == 0)) for i, d in enumerate(docs[:limit])])


def _profile(case):
    dec = case.customer.get("declared") or {}
    if dec.get("monthly_income_gbp"):
        return f"declared monthly income of {_gbp(dec['monthly_income_gbp'])}"
    if dec.get("annual_turnover_gbp"):
        noun = "annual payment volume" if case.institution == "payments_fx" else "annual turnover"
        return f"declared {noun} of {_gbp(dec['annual_turnover_gbp'])}"
    return "declared profile"


def _multiple_text(case, mult=None):
    """'7.0 times the declared monthly income of £3,050.00', worded as the TM01 alert words it (the baseline differs by
    institution: monthly income, monthly turnover, or the monthly share of the annual payment volume)."""
    for a in sorted(case.alerts, key=lambda a: a["alert_id"]):
        if a["rule_id"] == "TM01":
            m = re.search(r"([\d.]+) times (the [^.]*? of £[\d,]+\.\d\d)", a.get("message", ""))
            if m:
                return f"{m.group(1)} times {m.group(2)}"
    if isinstance(mult, (int, float)) and not isinstance(mult, bool) and mult:
        return f"{mult:.1f} times the {_profile(case)}"
    return None


def _is_business(case):
    return case.customer.get("kind") == "business" or case.institution == "payments_fx"


def _measured(case, rule, key):
    for a in sorted(case.alerts, key=lambda a: a["alert_id"]):
        if a["rule_id"] == rule and (a.get("measured") or {}).get(key) is not None:
            return a["measured"][key]
    return None


def _list_entry(case, f):
    eid = f.get("entry_id") or _measured(case, "TM22", "list_entry")
    return eid, (case.list_entries.get(eid) or {}) if eid else {}


def _main_txn(case, f, direction=None):
    return _txn(case, f.get("txn_id")) or _biggest(_alert_txns(case), direction) or _biggest(_alert_txns(case))


def _others(case, lead):
    return _join([f"{r} ({RULE_NAMES.get(r, r)})" for r in _rules(case) if not lead or r != lead["rule_id"]])


def _amount(f, t):
    return _gbp(f.get("amount")) or (_gbp(t.get("amount_gbp")) if t else None)


# ---------------------------------------------------------------- CLOSE templates
def _close_doc_match(case, f, code, lead, noun, ground):
    """Document-backed close: finding -> the document records <noun> of the amount that matches the transaction ->
    the code's ground. Payee/payer/issuer are named only when the checker supplied them."""
    docs = _docs(case, f, code)
    t = _main_txn(case, f)
    amt = _amount(f, t)
    parts = [_finding(case, lead)]
    if docs:
        s = f"{_doc_phrase(docs[0])} records {noun}"
        if amt:
            s += f" {'for' if noun in _FOR_NOUNS else 'of'} {amt}"
        extra = [f"{lab} {f[k]}" for k, lab in (("payee", "payable to"), ("payer", "from"), ("firm", "issued by"),
                                                 ("item", "for")) if f.get(k)]
        if extra:
            s += " " + _join(extra)
        if t:
            s += f", matching {_txn_phrase(t, with_id=True)}"
        if len(docs) > 1:
            s += f", supported by {_doc_phrase(docs[1], dated=False)}"
        parts.append(s)
    elif t:
        parts.append(f"The documents on file account for {_txn_phrase(t, with_id=True)}")
    others = _others(case, lead)
    parts.append(_cap(ground) + (f", which also covers {others}" if others else ""))
    return parts


_EVENT_NOUN = {"solicitor_letter": "an estate or legal payment", "insurer_letter": "an insurance payout",
               "vehicle_sale_receipt": "the sale of a vehicle", "sale_receipt": "a private sale",
               "settlement_letter": "a settlement payment", "completion_statement": "a property sale completion",
               "redundancy_letter": "a redundancy payment", "employment_contract": "employment pay",
               "advert": "a private sale"}
_FOR_NOUNS = ("the sale of a vehicle", "a private sale", "a property sale completion", "employment pay")


def t_documented_event(case, f, code, lead):
    docs = _docs(case, f, code)
    noun = f.get("event") or (_EVENT_NOUN.get(docs[0].type, "a one-off event") if docs else "a one-off event")
    return _close_doc_match(case, f, code, lead, noun,
                            f"That documented one-off event explains the funds for {_who(case)}")


def t_invoice_matches(case, f, code, lead):
    docs = _docs(case, f, code)
    kind = "a contract payment" if docs and ("contract" in docs[0].type or "schedule" in docs[0].type) else \
        "an amount due"
    if f.get("invoice_no"):
        kind += f" under invoice {f['invoice_no']}"
    return _close_doc_match(case, f, code, lead, kind,
                            "The invoice matches the payment's amount and is dated on or before it, so it explains "
                            "the activity")


def t_collection(case, f, code, lead):
    docs = _docs(case, f, code)
    t = _main_txn(case, f, "out")
    parts = [_finding(case, lead)]
    if docs:
        s = f"{_doc_phrase(docs[0])} documents a collection made on behalf of"
        payers = f.get("payers")
        if isinstance(payers, (list, tuple)) and payers:
            names = sorted(payers)
            s += " " + _join(names[:3] + ([f"{len(names) - 3} others"] if len(names) > 3 else []))
        elif f.get("payer_count"):
            s += f" {f['payer_count']} named people"
        else:
            s += " the named people who paid in"
        tot = _gbp(f.get("total") or f.get("amount"))
        if tot:
            s += f", totalling {tot}"
        parts.append(s)
    if t:
        parts.append(f"The collected money went on in {_txn_phrase(t)}" +
                     (f" to {f['payee']}" if f.get("payee") and f["payee"] not in _txn_phrase(t) else ""))
    parts.append("The payers match the people named, so the collection explains " + _rules_phrase(case))
    return parts


def t_business_records(case, f, code, lead):
    docs = _docs(case, f, code)
    parts = [_finding(case, lead)]
    if docs:
        s = f"{_docs_phrase(docs)} {_v(docs[:2], 'record', 'records')} the business takings"
        if f.get("period"):
            s += f" for {f['period']}"
        if f.get("records_total"):
            s += f" of {_gbp(f['records_total'])}"
        parts.append(s)
    tot = _gbp(f.get("deposits_total")) or _gbp(_measured(case, "TM03", "total_gbp"))
    parts.append(f"The deposits{' of ' + tot if tot else ''} reconcile to those business records, so they explain "
                 f"the activity for {_who(case)}")
    return parts


def t_own_account(case, f, code, lead):
    docs = _docs(case, f, code)
    t = _main_txn(case, f)
    who = _who(case)
    parts = [_finding(case, lead)]
    if docs:
        s = f"{_doc_phrase(docs[0])} shows {f.get('holder') or who} holds the account"
        if f.get("account"):
            s += f" {f['account']}"
        if t:
            s += f" behind {_txn_phrase(t)}"
        parts.append(s)
    mt = _multiple_text(case, f.get("multiple"))
    if mt and lead and lead["rule_id"] == "TM01":
        parts.append(f"The money moved between {_poss(who)} own accounts, and activity is below the 5 times level "
                     "that would need an updated profile")
    elif mt:
        parts.append(f"The money moved between {_poss(who)} own accounts and takes activity to {mt}, below the 5 "
                     "times level that would need an updated profile")
    else:
        parts.append(f"The money moved between accounts {who} is shown to hold, so it explains the activity")
    return parts


def t_group_company(case, f, code, lead):
    docs = _docs(case, f, code)
    t = _main_txn(case, f)
    cp = f.get("counterparty") or (t.get("counterparty_name") if t else None)
    parts = [_finding(case, lead)]
    if docs:
        s = f"{_docs_phrase(docs)} {_v(docs[:2], 'show', 'shows')} {cp or 'the counterparty'} and {_who(case)} are " \
            "in the same group"
        if f.get("owner"):
            s += f" under common owner {f['owner']}"
        parts.append(s)
    parts.append(f"{_cap(_txn_phrase(t)) if t else 'The payment'} is between verified group companies, so it "
                 "explains the activity")
    return parts


def t_third_party(case, f, code, lead):
    docs = _docs(case, f, code)
    t = _main_txn(case, f, "in")
    payer = f.get("payer") or (t.get("counterparty_name") if t else None)
    parts = [_finding(case, lead)]
    if docs:
        s = f"{_doc_phrase(docs[0])} documents why {payer or 'the third party'} paid"
        if f.get("relationship"):
            s += f" ({f['relationship']})"
        amt = _amount(f, t)
        if amt:
            s += f" the {amt}"
        parts.append(s)
    parts.append("That documented relationship explains the third-party payment" +
                 (f", {_txn_phrase(t)}" if t else ""))
    return parts


def t_refund(case, f, code, lead):
    docs = _docs(case, f, code)
    t = _main_txn(case, f, "in")
    parts = [_finding(case, lead)]
    if docs:
        s = f"{_docs_phrase(docs)} {_v(docs[:2], 'confirm', 'confirms')} a refund"
        amt = _amount(f, t)
        if amt:
            s += f" of {amt}"
        orig = _txn(case, f.get("original_txn_id"))
        if orig:
            s += f" of the earlier {_txn_phrase(orig)[4:]}"
        elif f.get("original_amount"):
            s += f" of an earlier payment of {_gbp(f['original_amount'])}" + \
                 (f" on {_date(f['original_date'])}" if f.get("original_date") else "")
        parts.append(s)
    parts.append(f"{_cap(_txn_phrase(t, with_id=True)) if t else 'The money'} is the return of an earlier payment, "
                 "so it explains the activity")
    return parts


def t_returning(case, f, code, lead):
    docs = _docs(case, f, code)
    who = _who(case)
    parts = [_finding(case, lead)]
    if docs:
        s = f"{_docs_phrase(docs)} {_v(docs[:2], 'show', 'shows')} {who} was re-verified"
        if f.get("reverified_on"):
            s += f" on {_date(f['reverified_on'])}"
        if any(d.type == "bank_statement" for d in docs[:2]):
            s += " and the money came from an account in their own name"
        parts.append(s)
    parts.append("A verified customer has resumed using a dormant account, so the activity is explained")
    return parts


def t_list_different(case, f, code, lead):
    eid, entry = _list_entry(case, f)
    docs = _docs(case, f, code)
    parts = [_finding(case, lead)]
    listed = f.get("listed_name") or entry.get("name")
    if docs:
        s = f"{_docs_phrase(docs)} {_v(docs[:2], 'show', 'shows')} the counterparty is a different party from " + \
            (f"listed party {listed}" if listed else "the listed party") + (f" ({eid})" if eid else "")
        if f.get("difference"):
            s += f": {f['difference']}"
        parts.append(s)
    on = _measured(case, "TM22", "matched_on")
    parts.append((f"A match on {on} alone" if on else "A single matching detail") +
                 " does not make them the same party, so the list match is closed")
    return parts


def t_within_profile(case, f, code, lead):
    dec = case.customer.get("declared") or {}
    who = _who(case)
    if f.get("review_id"):     # earlier CLOSE review that describes this activity (checks/within_profile.py)
        return [_finding(case, lead),
                f"Earlier review {f['review_id']}, dated {_date(f.get('review_date'))}, already describes the "
                f"{f.get('amount')} from {f.get('counterparty')} that raised the alert",
                f"The activity is within the profile reviewed for {who}"]
    if f.get("peak_months"):   # document dated before the activity declares a seasonal peak with a figure
        return [_finding(case, lead),
                f"{f.get('doc_title')} ({f.get('doc_id')}), dated {_date(f.get('doc_date'))}, declares a seasonal peak "
                f"in {_join(list(f['peak_months']))} of up to {f.get('peak_figure')}",
                f"The {f.get('amount')} that raised the alert falls in those months and within that figure, "
                f"so it is within the declared profile for {who}"]
    docs = _docs(case, f, code)
    parts = [_finding(case, lead)]
    bits = [f"source of funds {dec['source_of_funds'].lower()}" if dec.get("source_of_funds") else None,
            f"purpose {dec['account_purpose'].lower()}" if dec.get("account_purpose") else None]
    s = f"This fits {_poss(who)} declared profile"
    if any(bits):
        s += " (" + _join(bits) + ")"
    if f.get("profile_note"):
        s += f": {f['profile_note']}"
    parts.append(s)
    parts.append(f"{_doc_phrase(docs[0])} is consistent with that activity, so it is within profile" if docs else
                 "The activity is in line with what the customer declared, so it is within profile")
    return parts


# ---------------------------------------------------------------- RFI templates
def _rfi(case, f, lead, missing, ask):
    return [_finding(case, lead), f.get("missing") or missing, f.get("ask") or f"Ask {_who(case)} for {ask}"]


def t_rfi_purpose(case, f, code, lead):
    tin, tout = _biggest(_alert_txns(case), "in"), _biggest(_alert_txns(case), "out")
    pay = _join([_txn_phrase(tin), _txn_phrase(tout)]) or "these payments"
    ref = next((t.get("reference") for t in (tin, tout) if t and t.get("reference")), None)
    missing = f"{_nothing(case, f)} what {pay} {'were' if tin and tout else 'was'} for" + \
              (f" (reference \"{ref}\")" if ref else "") + ", and nothing suspicious is recorded"
    return _rfi(case, f, lead, missing,
                "documents showing what the payments were for, such as the invoice, contract, loan agreement or "
                "booking confirmation behind them, naming the payers and payees")


def t_rfi_business(case, f, code, lead):
    w = case.window
    period = f.get("period") or (f"{_date(w.get('from'))} to {_date(w.get('to'))}" if w.get("from") else None)
    fd = _fact_docs(case, f)
    missing = (f"{_docs_phrase(fd)} {_v(fd[:2], 'do', 'does')} not reconcile the deposits to the business" if fd else
               "No takings, till or collection records are on file to reconcile the deposits to the business") + \
        ", and nothing suspicious is recorded"
    return _rfi(case, f, lead, missing,
                "the business records behind the deposits" + (f" for {period}" if period else "") +
                ", such as daily takings or till records, cash collection reports or management accounts")


def t_rfi_source(case, f, code, lead):
    t = _biggest(_alert_txns(case), "in")
    missing = f"{_nothing(case, f)} where {_txn_phrase(t) or 'the money'} came from, and nothing suspicious is " \
              "recorded"
    eg = "the sales invoices, contracts or bank statements behind the receipts" if _is_business(case) else \
        "a sale receipt, a solicitor's or insurer's letter, or a statement for the paying account"
    return _rfi(case, f, lead, missing, "proof of the source of funds" +
                (f" for the {_gbp(t.get('amount_gbp'))}" if t else "") + f", such as {eg}")


def t_rfi_counterparty(case, f, code, lead):
    eid, entry = _list_entry(case, f)
    t = _main_txn(case, f)
    cp = f.get("counterparty") or (t.get("counterparty_name") if t else None) or "the counterparty"
    listed = f.get("listed_name") or entry.get("name")
    pay = f"the {_gbp(t.get('amount_gbp'))} payment" if t else "the payment"
    if eid:
        on = _measured(case, "TM22", "matched_on")
        same = listed and re.sub(r"\W", "", cp.lower()) == re.sub(r"\W", "", listed.lower())
        rel = "the same person as" if same else ", or is controlled by,"
        missing = f"{_nothing(case, f)} whether {cp} is{'' if rel[0] == ',' else ' '}{rel} " + \
            (f"listed party {listed}" if listed else "the listed party") + f" ({eid})" + \
            (f"; the match is on {on} only" if on else "") + ", and nothing else is concerning"
        ask = f"Ask {_who(case)} to confirm who {cp} is, with full name, date of birth and address (or company " \
              f"number and officers), and what {pay} was for"
    else:
        missing = f"{_nothing(case, f)} who {cp} is or why they paid {_who(case)}, and nothing suspicious is recorded"
        ask = f"Ask {_who(case)} to confirm who {cp} is, their relationship to the customer, and what {pay} was for"
    return [_finding(case, lead), f.get("missing") or missing, f.get("ask") or ask]


def t_rfi_profile(case, f, code, lead):
    docs = _docs(case, f, code)
    who = _who(case)
    if f.get("peak_months"):   # a declared seasonal peak with no figure (checks/within_profile.py)
        return [_finding(case, lead),
                f"{f.get('doc_title')} ({f.get('doc_id')}), dated {_date(f.get('doc_date'))}, declares a seasonal peak "
                f"in {_join(list(f['peak_months']))} but gives no figure to cover the {f.get('amount')} that raised "
                f"the alert",
                f"Ask {who} for an updated expected turnover covering the peak period, with supporting evidence"]
    mt = _multiple_text(case, f.get("multiple") or _measured(case, "TM01", "multiple"))
    s = f"{_doc_phrase(docs[0])} shows {who} holds the other account" if docs else \
        f"The transfers are between {_poss(who)} own accounts"
    if mt and lead and lead["rule_id"] == "TM01":
        s += ", but those own-account transfers take activity to at least 5 times the declared level, the SOP " \
             "threshold for an updated profile"
    elif mt:
        s += f", but the transfers take activity to {mt}, at or above the 5 times threshold for an updated profile"
    if case.institution == "payments_fx":
        ask = "an updated expected annual payment volume, with the contracts or forecasts behind the higher volume"
    elif _is_business(case):
        ask = "an updated expected annual turnover, with management accounts or contracts behind the higher activity"
    else:
        ask = "an updated expected monthly income, with evidence such as payslips, an employer letter or the reason " \
              "for the higher activity"
    return [_finding(case, lead), s, f"Ask {who} for {ask}"]


# ---------------------------------------------------------------- ESCALATE templates
def t_esc_mule(case, f, code, lead):
    who = _who(case)
    tout = _biggest(_alert_txns(case), "out")
    docs = _docs(case, f, code, {"sending_bank_fraud_report"}) or _docs(case, f, code)
    parts = [_finding(case, lead)]
    if docs:
        frauds = [d for d in docs if d.type == "sending_bank_fraud_report"]
        s = f"{_docs_phrase(docs)} {_v(docs[:2], 'report', 'reports')}"
        s += " that payers into the account were victims of fraud" if (frauds or f.get("report_count")) else \
            " how the money arrived"
        if f.get("victims"):
            s += f" ({_join(sorted(f['victims'])[:3])})"
        parts.append(s)
    if tout:
        parts.append(f"The money was passed on, including {_txn_phrase(tout)}")
    parts.append(f"{_cap(_poss(who))} account is receiving and passing on other people's money, {_ESC_END}")
    return parts


def t_esc_structuring(case, f, code, lead):
    docs = _docs(case, f, code)
    who = _who(case)
    parts = [_finding(case, lead)]
    if docs:
        s = f"{_doc_phrase(docs[0])} records a conversation about keeping amounts under the reporting limit"
        if f.get("quote"):
            s += f" (\"{f['quote']}\")"
        parts.append(s)
    parts.append(f"The transactions look deliberately split to stay below the threshold, {_ESC_END}")
    return parts


def t_esc_unexplained(case, f, code, lead):
    tin, tout = _biggest(_alert_txns(case), "in"), _biggest(_alert_txns(case), "out")
    docs = _docs(case, f, code, ANY_TYPE)
    parts = [_finding(case, lead)]
    if f.get("detail"):
        parts.append(_cap(f["detail"]))
    elif docs:
        parts.append(f"{_docs_phrase(docs)} {_v(docs[:2], 'do', 'does')} not credibly explain "
                     f"{_txn_phrase(tin) or 'the payments'}")
    elif tin:
        parts.append(f"No document on file explains {_txn_phrase(tin)}")
    if tin and tout:
        parts.append(f"The money was moved on, including {_txn_phrase(tout)}, with no credible explanation, {_ESC_END}")
    elif tout:
        parts.append(f"The payments, including {_txn_phrase(tout)}, have no credible explanation, {_ESC_END}")
    else:
        parts.append(f"The money has no credible explanation, {_ESC_END}")
    return parts


def t_esc_pass_through(case, f, code, lead):
    txs = _alert_txns(case)
    tin, tout = _biggest(txs, "in"), _biggest(txs, "out")
    docs = _docs(case, f, code)
    parts = [_finding(case, lead)]
    if tin and tout:
        s = f"Money in, such as {_txn_phrase(tin)}, was paid straight back out, including {_txn_phrase(tout)}"
        if f.get("linked"):
            s += f", to {_join(sorted(f['linked'])[:3])}, linked to the customer"
            if f.get("officer"):
                s += f" through {f['officer']}"
        parts.append(s)
    if docs:
        parts.append(f"{_docs_phrase(docs)} {_v(docs[:2], 'are', 'is')} on file for the counterparties, and nothing "
                     "shows a commercial purpose for the flows")
    parts.append(f"Money passes through the account with no credible purpose, {_ESC_END}")
    return parts


def t_esc_invoice_mismatch(case, f, code, lead):
    t = _main_txn(case, f, "out")
    docs = _docs(case, f, code)
    parts = [_finding(case, lead)]
    if docs:
        s = f"{_docs_phrase(docs)} {_v(docs[:2], 'do', 'does')} not support {_txn_phrase(t) or 'the payments'}"
        if f.get("mismatch"):
            s += f": {f['mismatch']}"
        elif f.get("invoice_amount"):
            s += f": the invoice is for {_gbp(f['invoice_amount'])}"
        parts.append(s)
    parts.append(f"The invoices do not explain the payments, {_ESC_END}")
    return parts


def t_esc_corridor(case, f, code, lead):
    hr = sorted([t for t in _alert_txns(case) if (t.get("counterparty_country") or "").lower() in _HR],
                key=lambda t: (-float(t["amount_gbp"]), t["transaction_id"]))
    country = f.get("country") or _measured(case, "TM06", "countries") or \
        (hr[0]["counterparty_country"] if hr else None)
    parts = [_finding(case, lead)]
    if hr:
        parts.append(f"The payments include {_join([_txn_phrase(t) for t in hr[:2]])}")
    fd = _fact_docs(case, f)
    expl = f"{_docs_phrase(fd)} {_v(fd[:2], 'do', 'does')} not explain" if fd else "nothing on file explains"
    parts.append(f"{_who(case)} did not declare {country or 'the higher-risk country'} and {expl} the payments, "
                 f"{_ESC_END}")
    return parts


def t_esc_misuse(case, f, code, lead):
    docs = _docs(case, f, code, {"device_export"}) or _docs(case, f, code)
    parts = [_finding(case, lead)]
    if docs:
        s = f"{_docs_phrase(docs)} {_v(docs[:2], 'show', 'shows')} how the account was accessed"
        if f.get("detail"):
            s += f": {f['detail']}"
        parts.append(s)
    parts.append(f"Someone other than {_who(case)} appears to control or use the account, {_ESC_END}")
    return parts


def t_esc_third_party(case, f, code, lead):
    t = _biggest(_alert_txns(case), "in", lambda t: t.get("counterparty_name")) or _main_txn(case, f)
    payer = f.get("payer") or (t.get("counterparty_name") if t else None)
    docs = _docs(case, f, code)
    parts = [_finding(case, lead)]
    if docs:
        s = f"{_docs_phrase(docs)} {_v(docs[:2], 'do', 'does')} not show a relationship that explains " \
            f"{payer or 'the third party'}"
        if f.get("detail"):
            s += f"; {f['detail']}"
        parts.append(s)
    parts.append(f"Unrelated third parties{' such as ' + payer if payer else ''} fund the account or receive money "
                 f"from it, {_ESC_END}")
    return parts


def t_esc_early_redemption(case, f, code, lead):
    tout = _biggest(_alert_txns(case), "out", lambda t: t.get("channel") != "fund_subscription")
    parts = [_finding(case, lead)]
    fd = _fact_docs(case, f)
    if fd:
        parts.append(f"{_docs_phrase(fd)} {_v(fd[:2], 'record', 'records')} the redemption" +
                     (f": {f['detail']}" if f.get("detail") else ""))
    if tout:
        parts.append(f"The proceeds left the account, including {_txn_phrase(tout)}")
    held = f.get("held_days") or _measured(case, "TM10", "held_days")
    parts.append("Fund units were bought and sold back" + (f" within {held} days" if held else " quickly") +
                 f" to move money, {_ESC_END}")
    return parts


def t_esc_listed(case, f, code, lead):
    eid, entry = _list_entry(case, f)
    listed = f.get("listed_name") or entry.get("name")
    party = f"listed party {listed}" if listed else "the listed party"
    docs = _docs(case, f, code)
    parts = [_finding(case, lead)]
    if docs:
        s = f"{_docs_phrase(docs)} "
        if f.get("detail"):
            s += f"{_v(docs[:2], 'show', 'shows')} {f['detail']}"
        elif f.get("officer") and f.get("ceased_on"):
            s += f"{_v(docs[:2], 'show', 'shows')} {f['officer']} still held control until {_date(f['ceased_on'])}, " \
                 "after the payment"
        elif f.get("dob"):
            s += f"{_v(docs[:2], 'give', 'gives')} a date of birth of {_date(f['dob'])}, the same as {party}"
        else:
            s += f"{_v(docs[:2], 'link', 'links')} the counterparty to {party}"
        if eid:
            s += f" ({eid})"
        parts.append(s)
    parts.append(f"The counterparty is the listed party or is controlled by it, {_ESC_END}")
    return parts


TEMPLATES = {
    "COLLECTION_DOCUMENTED": t_collection, "BUSINESS_RECORDS_RECONCILE": t_business_records,
    "DOCUMENTED_EVENT": t_documented_event, "INVOICE_MATCHES": t_invoice_matches, "OWN_ACCOUNT": t_own_account,
    "GROUP_COMPANY": t_group_company, "THIRD_PARTY_DOCUMENTED": t_third_party, "REFUND_OF_EARLIER_PAYMENT": t_refund,
    "RETURNING_CUSTOMER": t_returning, "LIST_MATCH_DIFFERENT_PARTY": t_list_different,
    "WITHIN_PROFILE": t_within_profile,
    "RFI_PURPOSE_DOCUMENTS": t_rfi_purpose, "RFI_BUSINESS_RECORDS": t_rfi_business, "RFI_SOURCE_OF_FUNDS": t_rfi_source,
    "RFI_COUNTERPARTY_DETAILS": t_rfi_counterparty, "RFI_UPDATED_PROFILE": t_rfi_profile,
    "ESC_MULE_ACTIVITY": t_esc_mule, "ESC_STRUCTURING": t_esc_structuring, "ESC_UNEXPLAINED_FUNDS": t_esc_unexplained,
    "ESC_PASS_THROUGH": t_esc_pass_through, "ESC_INVOICE_MISMATCH": t_esc_invoice_mismatch,
    "ESC_HIGH_RISK_CORRIDOR": t_esc_corridor, "ESC_ACCOUNT_MISUSE": t_esc_misuse,
    "ESC_THIRD_PARTY_FUNDING": t_esc_third_party, "ESC_EARLY_REDEMPTION": t_esc_early_redemption,
    "ESC_LISTED_PARTY": t_esc_listed,
}


def _fallback(case, verdict, code):
    who, al = _who(case), _rules_phrase(case) or "the alerts"
    if verdict == "CLOSE":
        return f"The documents on file explain the activity behind {al} for {who}. The case is closed."
    if verdict == "RFI":
        return (f"Nothing on file explains the activity behind {al} for {who}, and nothing suspicious is recorded. "
                f"Ask {who} for documents showing the source and purpose of the funds.")
    return f"The activity behind {al} for {who} has no credible explanation on file. It is escalated to the MLRO."


def render(case, verdict, code, facts):
    f = {k: v for k, v in (facts or {}).items() if v not in (None, "", [], {})}
    fn = TEMPLATES.get(code)
    if fn is None:
        return _fallback(case, verdict, code)
    text = _finish(fn(case, f, code, _lead_alert(case, code)))
    if len(_sentences(text)) < 2:
        return _fallback(case, verdict, code)
    return text
