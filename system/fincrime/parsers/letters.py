"""Letters parser (T-PRS-LETTERS): prose letters and emails -> structured Doc.fields.

parse(doc) -> dict of extra fields; loader merges them into Doc.fields, where they win over the generic
`**Field:**` text (kept as <key>_raw when it differs). Missing facts are left out (never guessed).
Amounts are floats rounded to the penny, dates are ISO strings, flags are booleans that are True only
when the letter says so explicitly.

Keys by doc type (all types may also carry `issuer` from the title "... from <issuer>" and
`addressee` from a named "Dear <name>,"):
  solicitor_letter          amount (entitlement), deceased_name
  insurer_letter            amount (settlement), claim_ref ("CLAIM nnnnn"), policy_type ("critical illness" |
                            "cargo"), payee (named payee, or addressee when paid "to you")
  settlement_letter         amount, payer (company paying; defaults to issuer), loss_of_office (bool)
  completion_statement      client_name, property_sold, sale_price, mortgage_redeemed, fees_total,
                            paid_to_client, amount (= paid_to_client),
                            completion_reconciles (bool: sale - mortgage - fees == paid, to the penny)
  redundancy_letter         employer (defaults to issuer), redundancy_confirmed (bool), last_working_day
  gift_letter               giver_name, giver_dob, recipient_name, relationship ("child" | "relative" | ...),
                            amount, gift_not_repayable (bool), giver_no_interest (bool)
  cancellation_confirmation product, within_cancellation_period (bool), amount (subscription returned),
                            subscription_received, refund_bank, refund_sort_code, refund_account_number,
                            refund_in_own_name (bool)
  customer_letter           relocated_to, withdrawal_bank, account_ending, account_in_own_name (bool)
  customer_email, email     sender, subject, amount, made_redundant (bool), mistaken_transfer (bool),
                            return_bank, return_sort_code, return_account_number,
                            funding_company, supplier, owns_both_companies (bool)
"""
import re

TYPES = ("solicitor_letter", "insurer_letter", "settlement_letter", "completion_statement", "redundancy_letter",
         "gift_letter", "cancellation_confirmation", "customer_letter", "customer_email", "email")

MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}
_MONEY = r"£\s?([\d,]+(?:\.\d{1,2})?)"
_DATE = r"(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(\d{4})"
_NAME = r"([A-Z][\w'’&.\-]*(?:[ \t]+(?:[A-Z][\w'’&.\-]*|&|of|and|de|du|la|von|van|z|o\.o\.))*)"
_SORT_ACCT = r"(\d{2}-\d{2}-\d{2})\s+(\d{6,10})"
_GENERIC_ADDRESSEE = {"sir/madam", "sir or madam", "customer", "employee", "former employee", "team", "client",
                      "colleague", "beneficiary", "investor", "policyholder", "claimant", "the team", "support team",
                      "the support team", "payments team", "the payments team"}
I = re.I
_NEGATOR = re.compile(r"\b(?:not|no|never|neither|cannot)\b|n't\b", I)
_CLAUSE_END = re.compile(r"[.;!?](?!\d)|\n\s*\n|,\s*(?:and|but|nor|or)\b|\b(?:and|but) that\b", I)


def _money(s):
    try:
        return round(float(s.replace(",", "")), 2)
    except ValueError:
        return None


def _date(d, mo, y):
    return f"{int(y):04d}-{MONTHS[mo.lower()]:02d}-{int(d):02d}"


def _clean(s):
    """Collapse whitespace and cut a name that ran on past a sentence end ("Name. Next sentence")."""
    s = re.split(r"\.\s+(?=[A-Z])", re.sub(r"\s+", " ", s))[0]
    return s.strip(" ,.;:")


def _body(text):
    """Text after the header block (title, Document ID, Dated) so header dates and IDs don't leak into facts."""
    return re.sub(r"^\s*#.*$|^\s*\*\*(?:document id|dated):?\*\*:?.*$", "", text, flags=I | re.M)


def _first(pattern, text, flags=I):
    m = re.search(pattern, text, flags)
    return m if m else None


def _first_amount(body):
    m = re.search(_MONEY, body)
    return _money(m.group(1)) if m else None


def _title_party(doc):
    m = re.search(r"\bfrom\s+(.+?)\s*$", doc.title or "", I)
    if not m or re.fullmatch(r"the customer", m.group(1).strip(), I):
        return None
    return _clean(m.group(1))


def _addressee(body):
    m = re.search(r"^\s*dear\s+(.+?)\s*,?\s*$", body, I | re.M)
    if not m:
        return None
    name = _clean(m.group(1))
    return None if name.lower() in _GENERIC_ADDRESSEE else name


def _says(pattern, body):
    """True when the letter states `pattern` affirmatively: no negator earlier in the same clause and no
    "unless"/"except" later in it ("not held in your name", "no repayment unless ..." stay False)."""
    for m in re.finditer(pattern, body, I):
        start = max([e.end() for e in _CLAUSE_END.finditer(body, 0, m.start())] or [0])
        end = _CLAUSE_END.search(body, m.end())
        rest = body[m.end():end.start() if end else len(body)]
        if not _NEGATOR.search(body[start:m.start()]) and not re.search(r"\b(?:unless|except)\b", rest, I):
            return True
    return False


def _put(out, key, value):
    if value is not None and value != "":
        out[key] = value


def _solicitor(body, out):
    _put(out, "amount", _first_amount(body))
    m = _first(r"estate of the late\s+" + _NAME, body, I)
    _put(out, "deceased_name", m and _clean(m.group(1)))


def _insurer(body, out):
    _put(out, "amount", _first_amount(body))
    m = _first(r"\b(CLAIM\s+\d+)\b", body)
    _put(out, "claim_ref", m and re.sub(r"\s+", " ", m.group(1).upper()))
    if re.search(r"critical illness", body, I):
        out["policy_type"] = "critical illness"
    elif re.search(r"\bcargo\b", body, I):
        out["policy_type"] = "cargo"
    m = _first(r"\bto\s+(?!you\b|your\b|reach\b|appear\b|reflect\b)" + _NAME + r"(?=\s+(?:via|by)\b|\s*\.)",
               body, 0)
    if m and not re.match(r"(?:Claims|Please)\b", m.group(1)):
        out["payee"] = _clean(m.group(1))
    elif re.search(r"\bto you\b|\byour (?:nominated )?account\b", body, I) and out.get("addressee"):
        out["payee"] = out["addressee"]


def _settlement(body, out):
    _put(out, "amount", _first_amount(body))
    m = _first(r"(?:,|advised that)\s+" + _NAME + r"\s+will\s+(?:be\s+)?(?:pay|issu|remit|process)", body, 0)
    _put(out, "payer", (m and _clean(m.group(1))) or out.get("issuer"))
    out["loss_of_office"] = _says(r"loss of office", body)


def _completion(body, out, fields):
    def money(*labels):
        for k, v in fields.items():
            if any(re.fullmatch(lab, k) for lab in labels):
                m = re.search(_MONEY, v)
                if m:
                    return _money(m.group(1))
        return None
    for k, v in fields.items():
        if k in ("client", "client name"):
            _put(out, "client_name", _clean(v))
        elif k in ("property sold", "property"):
            _put(out, "property_sold", _clean(v))
    _put(out, "sale_price", money(r"sale price"))
    _put(out, "mortgage_redeemed", money(r"mortgage redeemed", r"mortgage"))
    _put(out, "fees_total", money(r"fees", r"total fees", r"legal fees"))
    _put(out, "paid_to_client", money(r"paid to client.*", r"balance paid.*"))
    _put(out, "amount", out.get("paid_to_client"))
    parts = [out.get(k) for k in ("sale_price", "mortgage_redeemed", "fees_total", "paid_to_client")]
    if all(p is not None for p in parts):
        out["completion_reconciles"] = round(parts[0] - parts[1] - parts[2], 2) == parts[3]


def _redundancy(body, out):
    m = _first(r"(?:position|role)\s+(?:at|with)\s+" + _NAME + r"\s+has been", body, 0)
    _put(out, "employer", (m and _clean(m.group(1))) or out.get("issuer"))
    out["redundancy_confirmed"] = _says(r"\bmade redundant\b", body)
    m = _first(r"(?:last working day|final working day|final day of employment)[^.]*?\bwill be\s+" + _DATE, body)
    _put(out, "last_working_day", m and _date(*m.groups()))


def _gift(body, out):
    m = _first(r"\bI,\s+" + _NAME + r"\s*[,(]", body, 0)
    _put(out, "giver_name", m and _clean(m.group(1)))
    m = _first(r"\b(?:born(?:\s+on)?|DOB:?|date of birth:?)\s+" + _DATE, body)
    _put(out, "giver_dob", m and _date(*m.groups()))
    m = _first(r"\bto my\s+([a-z\- ]+?),\s+" + _NAME, body)
    if m:
        out["relationship"] = _clean(m.group(1)).lower()
        out["recipient_name"] = _clean(m.group(2))
    _put(out, "amount", _first_amount(body))
    out["gift_not_repayable"] = _says(
        r"non-repayable|not (?:subject to (?:any )?repayment|a loan)|no (?:requirement|expectation|obligation)[^.]*repa"
        r"|(?:does|do|will) not (?:require repayment|need to be repaid|have to be repaid)|never (?:need to )?be repaid",
        body)
    out["giver_no_interest"] = _says(
        r"\b(?:hold|retain|have|maintain) no\b[^.]*\binterest\b"
        r"|\b(?:not|do I|will I) (?:retain|hold|have|maintain) any\b[^.]*\binterest\b", body)


def _cancellation(body, out):
    m = _first(r"cancel(?:led|lation of)?\s+(?:your\s+)?(stocks and shares ISA|cash ISA|[A-Za-z ]*ISA)", body) \
        or _first(r"your\s+(stocks and shares ISA|cash ISA)\s+has been\b[^.]*\bcancelled", body)
    _put(out, "product", m and _clean(m.group(1)))
    out["within_cancellation_period"] = _says(
        r"(?:within|in accordance with) the \d+-day (?:cancellation|cooling-off)", body)
    m = _first(r"subscription(?: payment)?(?: of|,? totalling)\s+" + _MONEY, body)
    _put(out, "amount", _money(m.group(1)) if m else _first_amount(body))
    m = _first(r"received on\s+" + _DATE, body)
    _put(out, "subscription_received", m and _date(*m.groups()))
    m = _first(r"account (?:held )?at\s+" + _NAME + r"(?=[,.]|\s+(?:account|in|held|registered)\b)", body, 0) \
        or _first(r"(?:your|the)\s+" + _NAME + r"\s+account\b", body, 0) \
        or _first(r"^\s*bank:\s*(.+?)\s*$", body, re.M | I) \
        or _first(r"on file with us:\s*" + _NAME + r"\s*,\s*\d{2}-", body, 0)
    _put(out, "refund_bank", m and _clean(m.group(1)))
    m = _first(_SORT_ACCT, body)
    if m:
        out["refund_sort_code"], out["refund_account_number"] = m.group(1), m.group(2)
    # "your account held at <bank>" states the customer holds the refund account (POLICY.md own-account rule);
    # a bare "your account at <bank>" or "your nominated account" does not.
    out["refund_in_own_name"] = _says(
        r"\b(?:in|under) your (?:own )?name\b|\byour\s+(?:own\s+)?account\s+held\s+(?:at|with)\b", body)


def _customer_letter(body, out):
    m = _first(r"\b(?:relocated|moved)\s+to\s+" + _NAME, body, 0)
    _put(out, "relocated_to", m and _clean(m.group(1)))
    m = _first(r"account\s+(?:held\s+)?at\s+" + _NAME + r"\s*,\s*account ending\s+(\d+)", body, 0)
    if m:
        out["withdrawal_bank"], out["account_ending"] = _clean(m.group(1)), m.group(2)
    else:
        m = _first(r"account ending\s+(\d+)", body)
        _put(out, "account_ending", m and m.group(1))
    out["account_in_own_name"] = _says(r"\bin my (?:own )?name\b", body)


def _email(doc, body, out):
    sender = _title_party(doc)
    out["sender"] = sender or ("customer" if re.search(r"from the customer", doc.title or "", I) else None)
    if out["sender"] is None:
        del out["sender"]
    out.pop("issuer", None)
    m = _first(r"^\s*subject:\s*(.+?)\s*$", body, I | re.M)
    _put(out, "subject", m and _clean(m.group(1)))
    _put(out, "amount", _first_amount(body))
    out["made_redundant"] = _says(r"\bmade redundant\b", body)
    out["mistaken_transfer"] = _says(
        r"\bmistake(?:n|nly)?\b|\baccidentally\b|\bin error\b|\breversal\b|\bincorrect transfer\b", body)
    m = _first(r"account at\s+" + _NAME + r"(?=[,?.]|\s)", body, 0) or _first(r"\bmy\s+" + _NAME + r"\s+account\b",
                                                                              body, 0)
    _put(out, "return_bank", m and _clean(m.group(1)))
    m = _first(_SORT_ACCT, body)
    if m:
        out["return_sort_code"], out["return_account_number"] = m.group(1), m.group(2)
    m = _first(r"(?:\bthat\s+|^[ \t]*)" + _NAME + r"\s+will be\s+(?:sending|processing|initiating)", body, re.M)
    _put(out, "funding_company", m and _clean(m.group(1)))
    m = _first(r"supplier (?:payment|obligation)\s+(?:to|for)\s+" + _NAME, body, 0) \
        or _first(r"processing a payment to\s+" + _NAME, body, 0)
    _put(out, "supplier", m and _clean(m.group(1)))
    out["owns_both_companies"] = _says(r"both (?:companies|[^.]*?) are (?:mine|under my ownership)", body)


def parse(doc):
    if doc.type not in TYPES:
        return {}
    body = _body(doc.text)
    out = {}
    _put(out, "issuer", _title_party(doc))
    _put(out, "addressee", _addressee(body))
    if doc.type == "solicitor_letter":
        _solicitor(body, out)
    elif doc.type == "insurer_letter":
        _insurer(body, out)
    elif doc.type == "settlement_letter":
        _settlement(body, out)
    elif doc.type == "completion_statement":
        _completion(body, out, doc.fields)
    elif doc.type == "redundancy_letter":
        _redundancy(body, out)
    elif doc.type == "gift_letter":
        _gift(body, out)
    elif doc.type == "cancellation_confirmation":
        _cancellation(body, out)
    elif doc.type == "customer_letter":
        _customer_letter(body, out)
    else:
        _email(doc, body, out)
    return out
