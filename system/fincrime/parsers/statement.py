"""bank_statement parser (T-PRS-STATEMENT). parse(doc) -> dict merged into Doc.fields.

Keys (absent when the document does not state them; never guessed):
  holder_name        str   "Account holder(s)" line as written (joint: "A and B")
  holder_names       list  individual holders, split on "and" / "&" / "," (one entry for a sole holder)
  account_name       str   "Account name" line (joint accounts), as written
  bank_name          str   "Bank" line, as written; else the bank in a "... statement from <Bank>" title
  sort_code          str   "NN-NN-NN" (separators normalised to "-")
  account_number     str   8-digit UK account number
  account_last4      str   last 4 digits of the account ("account ending NNNN", or from account_number)
  account_desc       str   non-numeric account description, e.g. "Spanish euro account"
  iban               str   full IBAN, spaces removed, upper-case
  iban_last4         str   last 4 characters of the IBAN ("IBAN ending NNNN", or from iban)
  iban_holder        str   name after "IBAN ... is held by"
  period_from        str   ISO date: start of the stated "Period" / "Statement period" line
  period_to          str   ISO date: end of the stated "Period" / "Statement period" line
  transfers_first_date str ISO date: earliest listed transfer date (derived from transfers, not a stated period)
  transfers_last_date  str ISO date: latest listed transfer date (derived from transfers, not a stated period)
  transfers          list  one dict per dated entry, in document order:
                           {"date": ISO, "amount": float, "direction": "in"|"out"|"",
                            "counterparty": str, "sender": str, "text": str}
                           counterparty = the "to ..." / "from ..." / "into ..." target; sender = "sent by ...".
Names are kept as written; normalising ("Ltd" vs "Limited", case) is the checker's job (checks/util.norm_name).
"""
import re

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december"]
_DATE = r"\d{1,2} (?:" + "|".join(MONTHS) + r") \d{4}"
DATE_RE = re.compile(r"\b(\d{1,2}) (" + "|".join(MONTHS) + r") (\d{4})\b", re.I)
ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
ENTRY_RE = re.compile(r"(?:^|(?<=\s))(" + _DATE + r"|\d{4}-\d{2}-\d{2}):\s*(.+?)(?=\s+(?:" + _DATE
                      + r"|\d{4}-\d{2}-\d{2}):|\s*$)", re.I | re.M)
AMOUNT_RE = re.compile(r"£\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?(?!\d|,\d)")
FIELD_RE = re.compile(r"^\s*[-*]?\s*\*\*([^*:]+?)\s*:?\s*\*\*\s*:?\s*(.+?)\s*$", re.M)
SORT_ACC_RE = re.compile(r"(?<!\d)(\d{2})[- ]?(\d{2})[- ]?(\d{2})\s+(\d{8})(?!\d)")
ENDING_RE = re.compile(r"\bending\s+(\d{4})\b", re.I)
IBAN_RE = re.compile(r"\b([A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?)\b")
IBAN_END_RE = re.compile(r"\bIBAN\s+ending\s+([A-Z0-9]{4})\b", re.I)
IBAN_HOLDER_RE = re.compile(r"\bIBAN\b[^.]*?\bis held by\s+(.+?)\s*(?:\.(?:\s|$)|$)", re.I)
SENT_BY_RE = re.compile(r"\bsent by\s+(.+?)\s*(?:\.(?:\s|$)|$)", re.I)
COUNTERPARTY_RE = re.compile(r"\b(?:to|from|into)\s+(.+?)\s*(?:,|\.(?:\s|$)|$)", re.I)
TITLE_BANK_RE = re.compile(r"^#\s*(?:[\w'-]+\s+)*?statement\s+from\s+(.+?)\s*$", re.I | re.M)
HOLDER_SPLIT_RE = re.compile(r"\s*(?:,|&|\band\b)\s*", re.I)


def _iso(text):
    m = DATE_RE.search(text)
    if m:
        return f"{int(m.group(3)):04d}-{MONTHS.index(m.group(2).lower()) + 1:02d}-{int(m.group(1)):02d}"
    m = ISO_RE.search(text)
    return "-".join(m.groups()) if m else ""


def _amount(text):
    m = AMOUNT_RE.search(text)
    if not m:
        return None
    pence = (m.group(2) or "0").ljust(2, "0")
    return round(int(m.group(1).replace(",", "")) + int(pence) / 100, 2)


def _fields(text):
    return {re.sub(r"\s+", " ", k).strip().lower(): v.strip() for k, v in FIELD_RE.findall(text)}


def _direction(body):
    b = body.lower()
    if re.search(r"\breceived\b|\bcredit(?:ed)?\b|\bpaid in\b", b):
        return "in"
    if re.search(r"\btransfer(?:red)? of\b|\bsent\b|\bpaid out\b|\bdebit(?:ed)?\b|\bout\b", b):
        return "out"
    return ""


def _transfers(text):
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "**", "|")):
            continue
        for date, body in ENTRY_RE.findall(line):
            amount = _amount(body)
            if amount is None:
                continue
            sender = SENT_BY_RE.search(body)
            # "IBAN ... is held by X" is an account fact, not the counterparty of the transfer.
            cp_body = re.split(r"\.\s+Account\b", body, maxsplit=1)[0]
            cp = COUNTERPARTY_RE.search(cp_body)
            out.append({"date": _iso(date), "amount": amount, "direction": _direction(body),
                        "counterparty": cp.group(1).strip() if cp else "",
                        "sender": sender.group(1).strip() if sender else "", "text": body.strip()})
    return out


def parse(doc):
    text = doc.text or ""
    f = _fields(text)
    out = {}

    holder = f.get("account holder") or f.get("account holders")
    if holder:
        out["holder_name"] = holder
        out["holder_names"] = [h for h in HOLDER_SPLIT_RE.split(holder) if h]
    for src, key in (("account name", "account_name"), ("bank", "bank_name")):
        if f.get(src):
            out[key] = f[src]
    if "bank_name" not in out:
        m = TITLE_BANK_RE.search(text) or TITLE_BANK_RE.search("# " + (doc.title or ""))
        if m:
            out["bank_name"] = m.group(1)

    account = f.get("account", "")
    m = SORT_ACC_RE.search(account)
    if m:
        out["sort_code"] = "-".join(m.group(1, 2, 3))
        out["account_number"] = m.group(4)
        out["account_last4"] = m.group(4)[-4:]
    else:
        e = ENDING_RE.search(account)
        if e:
            out["account_last4"] = e.group(1)
        elif account and not re.search(r"\d", account):
            out["account_desc"] = account

    iban = IBAN_RE.search(f.get("iban", "") + " " + account)
    if iban:
        out["iban"] = iban.group(1).replace(" ", "").upper()
        out["iban_last4"] = out["iban"][-4:]
    else:
        e = IBAN_END_RE.search(text)
        if e:
            out["iban_last4"] = e.group(1).upper()
    h = IBAN_HOLDER_RE.search(text)
    if h:
        out["iban_holder"] = h.group(1).strip()

    transfers = _transfers(text)
    if transfers:
        out["transfers"] = transfers
        ds = sorted(t["date"] for t in transfers if t["date"])
        if ds:
            out["transfers_first_date"], out["transfers_last_date"] = ds[0], ds[-1]
    period = f.get("period") or f.get("statement period") or ""
    pdates = [_iso(p) for p in re.split(r"\s+(?:to|-|–|until)\s+", period, maxsplit=1)] if period else []
    if len(pdates) == 2 and all(pdates):
        out["period_from"], out["period_to"] = pdates
    return out
