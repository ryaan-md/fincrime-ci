"""T-PRS-RECEIPTS: fields for receipt-like documents. parse(doc) -> dict merged into Doc.fields.

Types: vehicle_sale_receipt, sale_receipt, receipt, advert, insurance_schedule.
Regex only, on `**Field:**` / `Field:` lines first, then the stock prose sentences. A key is set only
when it was actually found; nothing is guessed. Values are as written (whitespace collapsed), except:
amounts are floats, dates are ISO, registration is upper-case "AB12 CDE", payment_method is lower-case.

Keys (any type, when present):
  seller          str    seller / issuer of the doc (advert seller, receipt merchant)
  seller_address  str    seller's address (vehicle receipt) or the merchant premises ("Unit 4A, ...")
  buyer           str    buyer named on the doc (vehicle and private sale receipts)
  payer           str    "Received from X" on a vehicle sale receipt
  item            str    what was sold / paid for / insured ("2019 saloon", "carbon road bike, size 56")
  registration    str    vehicle registration, normalised ("AB12 CDE")
  amount          float  sale price, payment received, or advert asking price
  payment_method  str    "cash", ... (lower-case)
  mileage         int    vehicle receipt mileage
  near_offer      bool   advert price is "or near offer" (advert only)
  policyholder    str    insurance schedule holder
  insurer         str    insurance schedule insurer
  cover           str    cover type ("comprehensive")
  cover_from      str    ISO date cover starts
  cover_to        str    ISO date cover ends
"""
import re

TYPES = ("vehicle_sale_receipt", "sale_receipt", "receipt", "advert", "insurance_schedule")

_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"], 1)}
_DATE = r"\d{1,2} (?:" + "|".join(_MONTHS) + r") \d{4}"
_MONEY = r"£\s?([\d,]+(?:\.\d{1,2})?)"
_NAME = r"[A-Z][A-Za-z'\-]+(?: [A-Z][A-Za-z'\-]+)+"
_REG = re.compile(r"\b([A-Z]{2}\d{2}\s*[A-Z]{3})\b", re.I)
# `Label: value` with optional bold and optional list marker; label is matched case-insensitively.
_LINE = re.compile(r"^\s*[-*]?\s*(?:\*\*)?([A-Za-z][A-Za-z ]*?)(?:\*\*)?\s*:\s*(?:\*\*)?\s*(.+?)\s*$", re.M)
_LABELS = {
    "seller": "seller", "buyer": "buyer", "item": "item", "item description": "item", "vehicle": "vehicle",
    "registration": "registration", "amount": "amount", "price": "amount", "sale price": "amount",
    "total price": "amount", "total amount": "amount", "total amount paid": "amount", "amount paid": "amount",
    "purchase price": "amount", "payment method": "payment_method", "policyholder": "policyholder",
    "insurer": "insurer", "cover": "cover",
}

# sale prose: "... sale of one <item> from <seller> to <buyer>."
_FROM_TO = re.compile(r"\bsale of (?:one |a |an )?(.+?),? from (" + _NAME + r") to (" + _NAME + r")\b")
# "I, <seller>, have today sold / hereby confirm the sale of one <item> to <buyer>"
_I_SOLD = re.compile(r"\bI, (" + _NAME + r"), (?:have (?:today|this day) sold|(?:hereby )?(?:confirm|acknowledge) "
                     r"the sale of) (?:one |a |an )?(.+?),? to (" + _NAME + r")\b")
# "<seller> has sold one <item> to <buyer>"
_HAS_SOLD = re.compile(r"\b(" + _NAME + r") has sold (?:one |a |an )?(.+?),? to (" + _NAME + r")\b")
_RECEIVED = re.compile(r"\bReceived from (" + _NAME + r") the sum of " + _MONEY + r"(?: in (\w+))? for an? (.+?), "
                       r"registration ([A-Z]{2}\d{2}\s*[A-Z]{3})(?:, ([\d,]+) miles)?", re.I)
_ADVERT = re.compile(r"^(.+?)\.\s*" + _MONEY + r"( or near offer)?\.", re.M)
_SELLER_PROSE = re.compile(r"\bSeller: (" + _NAME + r")")
_PAYMENT = re.compile(r"\b(?:payment|paid)\b[^.£]*?" + _MONEY, re.I)
_PREMISES = re.compile(r"\b(Unit \d+[A-Z]?, [A-Z][A-Za-z']+(?: [A-Z][A-Za-z']+)*)")
_COURSE = re.compile(r"\b((?:[A-Za-z]+-week )?(?:[a-z]+ )*course)\b")
_TITLE_FROM = re.compile(r"^#?\s*Receipt from (.+?)\s*$", re.M)


def _clean(s):
    return re.sub(r"\s+", " ", s).strip(" .,*")


def _money(s):
    m = re.search(_MONEY, s)
    return round(float(m.group(1).replace(",", "")), 2) if m else None


def _reg(s):
    m = _REG.search(s)
    if not m:
        return None
    r = re.sub(r"\s+", "", m.group(1)).upper()
    return r[:4] + " " + r[4:]


def _iso(s):
    d, mo, y = s.split()
    return f"{int(y):04d}-{_MONTHS[mo.lower()]:02d}-{int(d):02d}"


def _body(text):
    """Text after the `**Dated:**` header line (or the whole text if there is none)."""
    m = re.search(r"^\s*\*\*Dated:\*\*.*$", text, re.M)
    return text[m.end():] if m else text


def _labelled(body):
    out = {}
    for label, value in _LINE.findall(body):
        key = _LABELS.get(_clean(label).lower())
        value = _clean(value)
        if key and value and key not in out:
            out[key] = value
    return out


def _put(out, key, value):
    if value not in (None, "") and key not in out:
        out[key] = value


def _sale_prose(body, out):
    for rx, order in ((_FROM_TO, ("item", "seller", "buyer")), (_I_SOLD, ("seller", "item", "buyer")),
                      (_HAS_SOLD, ("seller", "item", "buyer"))):
        m = rx.search(body)
        if m:
            for k, v in zip(order, m.groups()):
                _put(out, k, _clean(v))
            return


def parse(doc):
    body = _body(doc.text)
    flat = " ".join(body.split())                           # prose regexes: whitespace/line breaks are noise
    lab = _labelled(body)
    out = {}

    for k in ("seller", "buyer", "item", "policyholder", "insurer", "cover"):
        if k in lab:
            out[k] = lab[k]
    if "seller" in out and "," in out["seller"]:            # "**Seller:** Name, 1 Some Street, Town, AB1 2CD"
        name, addr = out["seller"].split(",", 1)
        out["seller"], out["seller_address"] = _clean(name), _clean(addr)
    if "amount" in lab:
        _put(out, "amount", _money(lab["amount"]))
    if "payment_method" in lab:
        out["payment_method"] = lab["payment_method"].lower()
    if "registration" in lab:
        _put(out, "registration", _reg(lab["registration"]))
    if "vehicle" in lab:                                     # "2019 saloon, registration AB12 CDE"
        _put(out, "item", _clean(re.split(r",?\s*registration\b", lab["vehicle"], flags=re.I)[0]))
        _put(out, "registration", _reg(lab["vehicle"]))

    if doc.type == "vehicle_sale_receipt":
        m = _RECEIVED.search(flat)
        if m:
            payer, amount, method, item, reg, miles = m.groups()
            _put(out, "payer", _clean(payer))
            _put(out, "amount", round(float(amount.replace(",", "")), 2))
            _put(out, "payment_method", method.lower() if method else None)
            _put(out, "item", _clean(item))
            _put(out, "registration", _reg(reg))
            _put(out, "mileage", int(miles.replace(",", "")) if miles else None)
    elif doc.type == "sale_receipt":
        _sale_prose(flat, out)
    elif doc.type == "advert":
        m = _ADVERT.search(body)
        if m:
            _put(out, "item", _clean(m.group(1)))
            _put(out, "amount", round(float(m.group(2).replace(",", "")), 2))
            out["near_offer"] = bool(m.group(3))
        m = _SELLER_PROSE.search(flat)
        if m:
            _put(out, "seller", _clean(m.group(1)))
    elif doc.type == "receipt":
        m = _TITLE_FROM.search(doc.title) or _TITLE_FROM.search(doc.text)
        if m:
            _put(out, "seller", _clean(m.group(1)))
        m = _PREMISES.search(body)
        if m:
            _put(out, "seller_address", _clean(m.group(1)))
        m = _COURSE.search(body)
        if m:
            _put(out, "item", _clean(m.group(1)).lower())
        elif re.search(r"\bbrochures?\b", body, re.I):
            _put(out, "item", "printed brochures")
    elif doc.type == "insurance_schedule":
        m = re.search(r"(" + _DATE + r") to (" + _DATE + r")", lab.get("cover", ""), re.I)
        if m:
            out["cover_from"], out["cover_to"] = _iso(m.group(1)), _iso(m.group(2))
            out["cover"] = _clean(lab["cover"][:m.start()])

    if doc.type in ("sale_receipt", "receipt"):
        m = _PAYMENT.search(body) if "amount" not in out else None
        if m:
            _put(out, "amount", round(float(m.group(1).replace(",", "")), 2))
        if "amount" not in out:
            _put(out, "amount", _money(body))
    if doc.type in ("vehicle_sale_receipt", "sale_receipt", "advert") and re.search(r"\bcash\b", body, re.I):
        _put(out, "payment_method", "cash")
    return out
