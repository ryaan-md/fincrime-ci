"""ESC_ACCOUNT_MISUSE: someone other than the customer appears to control or use the account.

POLICY.md lists "a shared device, IP address or contact details" as context that never raises an alert on its own.
So a shared device alone does not fire. This fires only when a device export shows a device the customer logs in
from that is ALSO used by at least two other customers AND has logins from a country other than the United
Kingdom (all four institutions are UK firms). That combination points to the account being operated by someone else."""
import re
from ..model import Flag

HOME_COUNTRIES = {"united kingdom", "uk", "great britain", "england", "scotland", "wales", "northern ireland"}
MIN_OTHER_CUSTOMERS = 2
CUSTOMER_ID = re.compile(r"\b[A-Z]{2}-C\d+\b")


def _tables(text):
    block = []
    for line in (text or "").splitlines() + [""]:
        if line.strip().startswith("|"):
            block.append([c.strip() for c in line.strip().strip("|").split("|")])
            continue
        if len(block) >= 2:
            yield [h.lower() for h in block[0]], [r for r in block[1:] if not all(set(c) <= set("-: ") for c in r)]
        block = []


def _col(header, *words):
    for i, h in enumerate(header):
        if any(w in h for w in words):
            return i
    return None


def scan_account_misuse(case, policy):
    me = case.raw.get("customer_id") or case.customer.get("customer_id")
    out = []
    for d in case.docs:
        if d.type != "device_export":
            continue
        for header, rows in _tables(d.text):
            ci, ki = _col(header, "customer"), _col(header, "countr")
            di, fi = _col(header, "device"), _col(header, "first seen")
            if ci is None or ki is None:
                continue
            for r in rows:
                cell = lambda i: r[i] if i is not None and i < len(r) else ""
                users = sorted(set(CUSTOMER_ID.findall(cell(ci))))
                others = [u for u in users if u != me]
                foreign = sorted({c.strip() for c in cell(ki).split(",") if c.strip() and c.strip().lower() not in HOME_COUNTRIES})
                if (me in users or not me) and len(others) >= MIN_OTHER_CUSTOMERS and foreign:
                    out.append(Flag("ESC_ACCOUNT_MISUSE", [d.doc_id],
                                    {"doc_id": d.doc_id, "doc_title": d.title, "device": cell(di), "first_seen": cell(fi),
                                     "shared_with": len(others), "foreign_countries": ", ".join(foreign)},
                                    "account_misuse", 4))
    return out
