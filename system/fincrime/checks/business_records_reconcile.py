"""BUSINESS_RECORDS_RECONCILE: business records (collection reports, reconciliation sheets, takings book) on file by
the window end reconcile, one row per deposit, to every cash deposit / top-up in a TM03 or TM05 alert.
A row reconciles a deposit when: amount equal to the penny; the row's date covers the deposit (collections: a site the
cash handling procedure lists, emptied before it, paid in the next morning when the procedure says so, at the branch
it names for that site, sites never combined; reconciliation: banked that day, after the end of the same-named event
in a ticket report on file; takings: same day, top-up not above that day's takings); notes + coins == total; collection
reports and procedures must name the customer as operator, and no record may name anyone else.
Records on file that leave a deposit with no row for its date -> RFI_BUSINESS_RECORDS. Any contradiction -> None."""
from datetime import date, timedelta
from ..model import Proof
from .util import amt_eq, fmt_gbp, norm_name

RECORD_TYPES = ("cash_collection_reports", "cash_reconciliation", "takings_book")
APPLIES_TO = {"TM03", "TM05"}
CASH_IN = {"cash_deposit", "cash_topup"}
NAMED = {"cash_collection_reports", "cash_handling_procedure"}


def _next_day(iso):
    return (date.fromisoformat(iso) + timedelta(days=1)).isoformat()


def _same_operator(case, docs):
    name = norm_name(case.customer.get("name"))
    named = [d.fields.get("business_name") for d in docs if d.type in NAMED or d.fields.get("business_name")]
    return bool(name) and all(n and norm_name(n) == name for n in named)


def _consistent(doc):
    """Row arithmetic inside the records themselves: notes + coins must equal the bag total."""
    for r in doc.fields.get("collections", []):
        if None in (r["notes"], r["coins"], r["total"]) or not amt_eq(r["notes"] + r["coins"], r["total"]):
            return False
    return True


def _site_rules(procs):
    return {norm_name(s["site"]): s for p in procs for s in p.fields.get("procedure_sites", [])}


def _covers(doc, row, t, sites, ends):
    """None if the row's date cannot cover the deposit; False if it could but disagrees; True if it reconciles."""
    day = t["booked_at"][:10]
    if not row["date"] or row["date"] > day:
        return None
    if doc.type == "cash_collection_reports":
        rule = sites.get(norm_name(row["location"]))
        if rule is None or (rule["banked_next_morning"] and _next_day(row["date"]) != day):
            return None
        branch = norm_name(rule["bank_branch"])
        ok = bool(branch) and branch in norm_name(t.get("location"))
    else:
        if row["date"] != day:
            return None
        ok = True
        if doc.type == "cash_reconciliation" and ends[doc.doc_id] > day:
            ok = False
        if doc.type == "takings_book" and (row["cap"] is None or row["amount"] is None or row["amount"] > row["cap"]):
            ok = False
    return ok and amt_eq(row["amount"], t["amount_gbp"])


def check_business_records(case, alert, policy):
    if alert["rule_id"] not in APPLIES_TO:
        return None
    txs = case.alert_txns.get(alert["alert_id"], [])
    if not txs or any(t.get("channel") not in CASH_IN or t.get("direction") != "in" for t in txs):
        return None
    on_file = case.docs_by_window_end()
    records = [d for d in on_file if d.type in RECORD_TYPES and d.fields.get("daily_totals")]
    if not records:
        return None
    procs = [d for d in on_file if d.type == "cash_handling_procedure"]
    tickets, ends = [], {}
    for d in records:
        if d.type == "cash_reconciliation":
            event = norm_name(d.fields.get("event_name"))
            same = [k for k in on_file if k.type == "ticket_report" and k.fields.get("event_end")
                    and event and norm_name(k.fields.get("event_name")) == event]
            if not same:
                return None
            tickets += [k for k in same if k not in tickets]
            ends[d.doc_id] = max(k.fields["event_end"] for k in same)
    if not _same_operator(case, records + procs + tickets) or not all(_consistent(d) for d in records):
        return None
    if any(p.fields.get("sites_combined") is not False for p in procs):
        return None
    sites = _site_rules(procs)
    rows = [(d, i, r) for d in records for i, r in enumerate(d.fields["daily_totals"])]
    used, matched, missing = set(), [], []
    for t in sorted(txs, key=lambda t: (t["booked_at"], t["transaction_id"])):
        hit, dated = None, False
        for d, i, r in rows:
            ok = _covers(d, r, t, sites, ends)
            dated = dated or ok is not None
            if ok and (d.doc_id, i) not in used:
                hit = (d, i, r)
                break
        if hit:
            used.add((hit[0].doc_id, hit[1]))
            matched.append((t, hit[0], hit[2]))
        elif dated:
            return None
        else:
            missing.append(t)
    docs = sorted({d.doc_id: d for _, d, _ in matched}.values(), key=lambda d: d.doc_id)
    ev = [d.doc_id for d in records + procs + tickets] + [t["transaction_id"] for t in txs]
    days = sorted(t["booked_at"][:10] for t in txs)
    first = docs[0] if docs else records[0]
    facts = {"doc_id": first.doc_id, "doc_title": first.title, "doc_date": first.dated, "rule": alert["rule_id"],
             "deposits": len(txs), "reconciled": len(matched),
             "amount": fmt_gbp(sum(t["amount_gbp"] for t in txs)),
             "txn_id": ", ".join(t["transaction_id"] for t in txs),
             "txn_date": days[0] if days[0] == days[-1] else f"{days[0]} to {days[-1]}",
             "rows": [(t["transaction_id"], t["booked_at"][:10], fmt_gbp(t["amount_gbp"]), d.doc_id, r["location"])
                      for t, d, r in matched]}
    if missing:
        facts["missing"] = [(t["transaction_id"], t["booked_at"][:10], fmt_gbp(t["amount_gbp"])) for t in missing]
        return Proof(alert["alert_id"], False, "RFI_BUSINESS_RECORDS", ev, facts, "business_records_reconcile")
    return Proof(alert["alert_id"], True, "BUSINESS_RECORDS_RECONCILE", ev, facts, "business_records_reconcile")
