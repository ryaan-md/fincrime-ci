"""TM22: escalate when the evidence shows the counterparty IS (or is controlled by) the listed party.

POLICY.md (all four institutions): "If the evidence shows the listed party is the counterparty or controls it, escalate."
"A match on one detail alone does not prove it is the same party."

Two ways the evidence can show it:
1. Date of birth: the listed person's full date of birth appears in a document on file (e.g. a gift letter giving
   the giver's DOB).
2. Control at the payment date: the counterparty's own company register or register of people with significant
   control lists the listed person (same name AND same month and year of birth, so two details) in a role that was
   still active on the payment date: appointed/notified on or before it, and resigned/ceased on or after it, or not at
   all. A director who resigned the day before the payment but stayed a controlling shareholder until after it still
   controls the counterparty.

Not used: an identifier or name match on its own. That is what raised the alert in the first place, and the policy
says one detail alone is not proof. No dev case has an identifier-only match, so this stays an open question."""
import re
from ..model import Flag
from ..checks.util import norm_name
from ..loader import parse_dates

REGISTER_TYPES = ("significant_control_register", "company_register_extract")
NAME_COLS = ("person", "officer", "name")
BORN_COLS = ("born", "date of birth", "dob")
START_COLS = ("notified", "appointed", "from")
END_COLS = ("ceased", "resigned", "to", "until")
CONTROL_COLS = ("control", "role", "nature of control")
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTH_YEAR_RE = re.compile(r"\b([a-z]{3,9})\.?\s+(\d{4})\b")
NUM_MONTH_YEAR_RE = re.compile(r"\b(\d{1,2})[/-](\d{4})\b")


def _month_year(text):
    """'August 1978', 'Aug 1978', '08/1978', '14 August 1978' or '1978-08-14' -> (1978, 8)."""
    t = (text or "").strip().lower()
    iso = parse_dates(text or "")
    if iso:
        return int(iso[0][:4]), int(iso[0][5:7])
    m = MONTH_YEAR_RE.search(t)
    if m and m.group(1) in MONTHS:
        return int(m.group(2)), MONTHS[m.group(1)]
    m = NUM_MONTH_YEAR_RE.search(t)
    if m and 1 <= int(m.group(1)) <= 12:
        return int(m.group(2)), int(m.group(1))
    return None


def _date(cell):
    d = parse_dates(cell or "")
    return d[0] if d else None


def _col(header, names):
    for i, h in enumerate(header):
        if h in names:
            return i
    return None


def _tables(text):
    """Yield (header, rows) for every markdown table, cells stripped, header lower-cased."""
    block = []
    for line in (text or "").splitlines() + [""]:
        if line.strip().startswith("|"):
            block.append([c.strip() for c in line.strip().strip("|").split("|")])
            continue
        if len(block) >= 2:
            header = [h.lower() for h in block[0]]
            rows = [r for r in block[1:] if not all(set(c) <= set("-: ") for c in r)]
            yield header, rows
        block = []


def _active_on(start, end, day):
    return (start is None or start <= day) and (end is None or end >= day)


def _payments(case, alert):
    """(date, transaction_id, counterparty name) for each payment the alert fired on, in a stable order."""
    txs = case.alert_txns.get(alert["alert_id"], []) or ([case.held_payment] if case.held_payment else [])
    return sorted((t["booked_at"][:10], t["transaction_id"], t.get("counterparty_name") or "") for t in txs if t)


def _control_rows(doc, entry):
    """Rows in this register that name the listed person with the same month and year of birth."""
    want_name, want_born = norm_name(entry.get("name")), _month_year(entry.get("date_of_birth"))
    if not want_name or not want_born:
        return []
    out = []
    for header, rows in _tables(doc.text):
        ni, bi = _col(header, NAME_COLS), _col(header, BORN_COLS)
        if ni is None or bi is None:
            continue
        si, ei, ci = _col(header, START_COLS), _col(header, END_COLS), _col(header, CONTROL_COLS)
        get = lambda r, i: r[i] if i is not None and i < len(r) else ""
        for r in rows:
            if norm_name(get(r, ni)) == want_name and _month_year(get(r, bi)) == want_born:
                out.append({"born": get(r, bi), "start": _date(get(r, si)), "end": _date(get(r, ei)),
                            "control": get(r, ci), "ended_label": header[ei] if ei is not None else ""})
    return out


def _scan_dob(case, entry):
    out = []
    for d in case.docs:
        if entry.get("date_of_birth") and entry["date_of_birth"] in d.dates:
            out.append(Flag("ESC_LISTED_PARTY", [d.doc_id, entry["entry_id"]],
                            {"doc_id": d.doc_id, "doc_title": d.title, "entry_id": entry["entry_id"],
                             "listed_name": entry["name"], "dob": entry["date_of_birth"], "variant": "dob_in_document"},
                            "listed_party", 6))
    return out


def _scan_control(case, alert, entry):
    out = []
    for pay_day, tx_id, payee in _payments(case, alert):
        payee_n = norm_name(payee)
        if not payee_n:
            continue
        registers = [d for d in case.docs if d.type in REGISTER_TYPES and payee_n in norm_name(d.title)]
        active, inactive = [], []
        for d in registers:
            for row in _control_rows(d, entry):
                (active if _active_on(row["start"], row["end"], pay_day) else inactive).append((d, row))
        if not active:
            continue
        d, row = active[0]
        ev = [d.doc_id] + [x.doc_id for x, _ in active[1:] + inactive if x.doc_id != d.doc_id] + [entry["entry_id"], tx_id]
        facts = {"doc_id": d.doc_id, "doc_title": d.title, "entry_id": entry["entry_id"], "listed_name": entry["name"],
                 "dob": row["born"], "counterparty": payee, "payment_date": pay_day, "transaction_id": tx_id,
                 "control": row["control"], "control_ended": row["end"], "variant": "control_at_payment_date"}
        for x, r in inactive:
            facts.update({"resigned_doc_id": x.doc_id, "resigned_on": r["end"], "resigned_role": r["control"]})
            break
        out.append(Flag("ESC_LISTED_PARTY", ev, facts, "listed_party", 6))
    return out


def scan_listed_party(case, policy):
    out = []
    for a in case.alerts:
        if a["rule_id"] != "TM22":
            continue
        e = case.list_entries.get((a.get("measured") or {}).get("list_entry"))
        if not e:
            continue
        out += _scan_dob(case, e)
        out += _scan_control(case, a, e)
    return out
