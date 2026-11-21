"""Load an institution folder into Case objects. Reads ONLY the folder it is given."""
import json, os, re
from collections import defaultdict
from .model import Case, Doc
from .parsers import business, contract, extras, invoice, letters, notes, receipts, register, statement

AMOUNT_RE = re.compile(r"£\s?([\d,]+(?:\.\d{1,2})?)")
FIELD_RE = re.compile(r"^\s*[-*]?\s*\*\*([^*:]+):?\*\*:?\s*(.+?)\s*$", re.M)
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}
DATE_RE = re.compile(r"\b(\d{1,2}) (" + "|".join(MONTHS) + r") (\d{4})\b", re.I)
ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")

# doc type -> [parse(doc) returning extra Doc.fields] (T-PRS-*). A type may have several parsers (email:
# letters then notes); they run in this module order. Add a parser module to the tuple.
PARSERS = {}
for _mod in (business, contract, invoice, letters, notes, receipts, register):
    for _t in _mod.TYPES:
        PARSERS.setdefault(_t, []).append(_mod.parse)
PARSERS.setdefault("bank_statement", []).append(statement.parse)   # statement.py declares no TYPES
# Run after the typed parsers for EVERY doc type: Doc.fields["_extras"] keeps every label line (duplicates too),
# table row and shape-certain token, so nothing a document states is dropped (parsers/extras.py).
GENERIC = (extras.parse,)


def merge_parsed(fields, parsed):
    """Merge (parser name, output) pairs into fields, in registry order. A parsed value wins over the raw
    **Field:** text, which is kept as <key>_raw when it differs. Between parsers the first one to set a key
    keeps it, and a later parser's different value is kept as <key>_<parser name>. Equal values add no key."""
    raw, owner = dict(fields), set()
    for name, out in parsed:
        for k, v in out.items():
            if k in owner:
                if fields[k] != v:
                    fields.setdefault(k + "_" + name, v)
                continue
            owner.add(k)
            if k in raw and raw[k] != v:
                fields[k + "_raw"] = raw[k]
            fields[k] = v


def _jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def parse_amounts(text):
    out = []
    for m in AMOUNT_RE.finditer(text):
        try:
            out.append(round(float(m.group(1).replace(",", "")), 2))
        except ValueError:
            pass
    return out


def parse_dates(text):
    out = [f"{int(y):04d}-{MONTHS[mo.lower()]:02d}-{int(d):02d}" for d, mo, y in DATE_RE.findall(text)]
    out += [f"{y}-{m}-{d}" for y, m, d in ISO_RE.findall(text)]
    return out


def parse_fields(text):
    return {k.strip().lower(): v.strip() for k, v in FIELD_RE.findall(text)}


def load(folder):
    folder = os.path.abspath(folder)
    inst = os.path.basename(folder.rstrip("/"))
    J = lambda n: _jsonl(os.path.join(folder, n))
    cases, alerts, customers = J("cases.jsonl"), J("alerts.jsonl"), J("customers.jsonl")
    txns, evidence, prev, lst = J("transactions.jsonl"), J("evidence.jsonl"), J("previous_reviews.jsonl"), J("internal_list.jsonl")

    cust = {c["customer_id"]: c for c in customers}
    alert_by_id = {a["alert_id"]: a for a in alerts}
    tx_by_id = {t["transaction_id"]: t for t in txns}
    tx_by_cust = defaultdict(list)
    for t in txns:
        tx_by_cust[t["customer_id"]].append(t)
    for v in tx_by_cust.values():
        v.sort(key=lambda t: (t["booked_at"], t["transaction_id"]))
    docs_by_cust = defaultdict(list)
    for e in evidence:
        p = os.path.join(folder, e["file"])
        text = open(p, encoding="utf-8").read() if os.path.exists(p) else ""
        doc = Doc(e["doc_id"], e["type"], e.get("title", ""), e.get("dated", ""),
                  text, parse_fields(text), parse_amounts(text), parse_dates(text))
        fns = [*PARSERS.get(doc.type, ()), *GENERIC]
        merge_parsed(doc.fields, [(p.__module__.rsplit(".", 1)[-1], p(doc)) for p in fns])
        docs_by_cust[e["customer_id"]].append(doc)
    for v in docs_by_cust.values():
        v.sort(key=lambda d: (d.dated, d.doc_id))
    prev_by_cust = defaultdict(list)
    for r in prev:
        prev_by_cust[r["customer_id"]].append(r)
    list_entries = {e["entry_id"]: e for e in lst}

    out = []
    for c in cases:
        cid = c["customer_id"]
        al = [alert_by_id[a] for a in c["alert_ids"] if a in alert_by_id]
        out.append(Case(
            institution=inst, raw=c, customer=cust.get(cid, {}), alerts=al,
            txns=tx_by_cust.get(cid, []),
            alert_txns={a["alert_id"]: [tx_by_id[t] for t in a.get("transaction_ids", []) if t in tx_by_id] for a in al},
            docs=docs_by_cust.get(cid, []), prev_reviews=prev_by_cust.get(cid, []), list_entries=list_entries,
            held_payment=tx_by_id.get(c.get("held_payment_id")) if c.get("held_payment_id") else None))
    return inst, out
