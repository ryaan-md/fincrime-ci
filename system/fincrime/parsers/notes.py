"""T-PRS-NOTES: fields for free-text notes, chats and letters. Regex only, no LLM.

parse(doc) -> dict, merged into Doc.fields by the loader (parsed keys win; differing **Field:** text is kept as <key>_raw).
Values are str unless noted: (float) money rounded to pennies, (int) counts, (list) str in text order.
A key is present only when the document states it.

Every type
  note_class            "decisive" (content that bears on an alert) | "filler" (routine admin, no explanation)

support_chat
  customer_explanation  every **Customer:** turn, joined: the customer's stated explanation, verbatim
  agent_query           first **Agent:** turn
  customer_turns (int)  number of **Customer:** turns
  stated_explanations (list)  payments_job | sale_proceeds | cash_redeposit | family_support | gift | medical |
                        loan_from_friend | investing_for_others | friends_money
  stated_relation       parent | uncle | cousins | friend | family | mate (first "my <relation>")
  stated_person         person named after "my parent"
  stated_provider       hospital / medical centre / clinic the customer names
  stated_location       place after the provider ("... in <Place>")
  stated_sale_item      what the customer says they sold
  stated_gift_from      who the customer says gave the gift
  stated_amount (float) first £ amount in the customer's turns
  stated_withdrawal_month  YYYY-MM of "I withdrew in <Month> <YYYY>"
  queried_counterparty  company named in the agent's query (Ltd, Limited, Exchange, FZE, ...)
  queried_country       country named in "payments to <Country>" in the agent's query
  queried_from_abroad   "yes" when the agent asks about payments from abroad
  chat_cues (list)      crypto | commission | urgency | met_online | sending_on | cannot_open_account

contact_note
  note_text             body text
  note_topic            standing_order | address_confirmed | statement_copies | charges_query | mobile_update |
                        lost_card | other

staff_note
  note_text             body text
  note_author, note_branch   from the title "Note by <author>, <branch>"
  note_topic            structuring_query | unexplained_routing | other
  structuring_threshold (float)  the limit the customer asked about keeping under
  note_amount (float)   the deposit / top-up the note records
  deposit_channel       cash_deposit | cash_topup
  intends_other_branch  "yes" when the customer says they will use another branch
  named_company         company the note is about
  payment_counterparty  company the customer pays / transfers to ("payments to <Company>")
  named_companies (list)  every company named in the note, first-mention order, no repeats
  stated_goods_origin   country the goods are said to come from
  routing_country       country the funds are routed to / through
  explanation_refused   "yes" when the note says the customer would not or could not explain

venue_email
  venue_name, email_subject, booking_ref ("BOOKING <n>"), refund_amount (float),
  refund_scope (full | partial), event_type (wedding | event), cancellation ("yes")

email
  sender_name, email_subject, paying_company, beneficiary_company (both from the body; the Subject line is not used),
  common_ownership ("yes": the sender says both companies are theirs),
  stated_reason (awaiting_receivables | other), customer_explanation (body text)

hospital_invoice
  issuer, patient_name, treatment, amount_due (float), pay_to_name, pay_to_city

sending_bank_fraud_report
  reporting_bank, report_subject, fraud_type (app_scam | other), victim_name, reported_amount (float),
  reported_date (ISO), account_ending, payment_reference,
  scam_story (vehicle_deposit | rental_deposit | tickets | marketplace_goods | other), freeze_requested ("yes")
"""
import re

TYPES = ("support_chat", "contact_note", "staff_note", "venue_email", "email", "hospital_invoice",
         "sending_bank_fraud_report")

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
_MON = "|".join(MONTHS)
_AMT = r"£\s?([\d,]+(?:\.\d{1,2})?)"
_CO_SUFFIX = r"(?:Ltd|Limited|LLP|PLC|plc|Inc|Co|FZE|GmbH|Srl|Sp\. z o\.o\.|Exchange)"
# A run of capitalised words ending in a company suffix ("Sample Distribution Ltd", "Example Exchange Ltd").
_COMPANY = r"((?:[A-Z][\w&'.-]*\s+){1,5}?" + _CO_SUFFIX + r"(?:\s+Ltd)?)(?![\w])"
_COMPANY_RE = re.compile(r"(?<![\w])" + _COMPANY)
# Sentence words that can start a capitalised run before a company name ("However Example Ltd").
_LEAD_WORD_RE = re.compile(r"^(?:(?:The|A|An|I|We|However|When|During|Today|Spoke|Called|Met|Our|Their)\s+)+")
_NAME = r"([A-Z][a-z'-]+(?:\s+[A-Z][a-z'-]+){0,3})"

_HEADER_RE = re.compile(r"^\s*(?:#.*|\*\*(?:Document ID|Dated):?\*\*:?.*)$", re.M)
_TURN_RE = re.compile(r"^\s*\*\*(Customer|Agent):?\*\*:?\s*(.+?)\s*$", re.M | re.I)
_FIELD_RE = re.compile(r"^\s*[-*]?\s*\*\*([^*:]+):?\*\*:?\s*(.+?)\s*$", re.M)
_SUBJECT_RE = re.compile(r"^\s*(?:\*\*)?Subject(?:\*\*)?:(?:\*\*)?\s*(.+?)\s*$", re.M | re.I)


def _norm(text):
    """Cosmetic noise out: curly quotes to straight, runs of spaces to one."""
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"[ \t ]+", " ", text)


def _money(s):
    try:
        return round(float(s.replace(",", "").replace(" ", "")), 2)
    except ValueError:
        return None


def _iso(day, month, year):
    return f"{int(year):04d}-{MONTHS.index(month.lower()) + 1:02d}-{int(day):02d}"


def _body(text, drop_subject=False):
    """Text after the header block (title, Document ID, Dated), whitespace-collapsed."""
    text = _HEADER_RE.sub("", text)
    if drop_subject:
        text = _SUBJECT_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _fields(text):
    return {k.strip().lower(): v.strip() for k, v in _FIELD_RE.findall(text)}


def _first(pattern, text, flags=re.I):
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def _put(out, key, value):
    if value not in (None, "", []):
        out[key] = value


def _title_after(title, prefix):
    return _first(r"^\s*" + prefix + r"\s+(.+?)\s*$", title or "")


# ---------------------------------------------------------------- support_chat
_EXPLANATIONS = [  # (category, pattern) checked against the customer's turns
    ("payments_job", r"payments? assistant|remote job|clients pay into my account|as my wages"),
    ("sale_proceeds", r"\bfrom selling (?:my|the|a)\b|\bsold (?:my|the)\b"),
    ("cash_redeposit", r"\b(?:deposited|paid|put|dropped)\b[^.]{0,30}\bcash back\b|\bcash back in\b"
                       r"|\bthe £[\d,.]+ I withdrew\b"),
    ("family_support", r"\bfamily support\b|\bhelp(?:ing)? my family\b|\bsupport my family\b"),
    ("gift", r"\bgift from\b|\bgifted\b"),
    ("medical", r"\bsurgery\b|\bmedical cent(?:er|re)\b|\bhospital\b|\btreatment\b|\boperation\b"),
    ("loan_from_friend", r"\bloan\b[^.]{0,40}\bfriend\b|\bfriend\b[^.]{0,40}\bloan\b"),
    ("investing_for_others", r"\binvest(?:ing)? (?:for|through|on behalf of)\b|\binvesting through mine\b"),
    ("friends_money", r"\bfriends sent me money\b|\bmoney (?:from|for) (?:some )?friends\b"),
]
_CUES = [  # (cue, pattern) checked against the whole chat
    ("crypto", r"\bcrypto|\bbitcoin\b|\bwallet\b"),
    ("commission", r"\bkeep \d+(?:\.\d+)?\s?%|\bcommission\b"),
    ("urgency", r"\bneeds? it (?:to go )?today\b|\bplease release\b|\burgent(?:ly)?\b"),
    ("met_online", r"\ball online\b|\bsocial media\b|\bnot met\b|\bnever met\b"),
    ("sending_on", r"\bsend(?:ing)? it on\b|\bpass(?:ing)? it on\b"),
    ("cannot_open_account", r"\bcannot open an account\b|\bcan't open an account\b"),
]
_RELATION_RE = re.compile(r"\bmy (parent|uncle|cousins?|friend|family|mate)\b", re.I)


def _support_chat(doc, text):
    out = {}
    turns = _TURN_RE.findall(text)
    cust = [t for who, t in turns if who.lower() == "customer"]
    agent = [t for who, t in turns if who.lower() == "agent"]
    said = " ".join(cust)
    _put(out, "customer_explanation", said)
    _put(out, "agent_query", agent[0] if agent else None)
    out["customer_turns"] = len(cust)
    _put(out, "stated_explanations",
         [c for _, c in sorted((m.start(), c) for c, p in _EXPLANATIONS for m in [re.search(p, said, re.I)] if m)])
    rel = _RELATION_RE.search(said)
    if rel:
        out["stated_relation"] = rel.group(1).lower()
    _put(out, "stated_person", _first(r"\bmy parent,?\s+([A-Z][a-z-]+(?:\s+[A-Z][a-z-]+)?)", said, 0))
    prov = re.search(r"\bat (?:the )?((?:[A-Z][\w'-]*\s+){0,4}(?:Medical Cent(?:er|re)|Hospital|Clinic))"
                     r"(?:,?\s+in\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?))?", said)
    if prov:
        _put(out, "stated_provider", prov.group(1).strip())
        _put(out, "stated_location", prov.group(2))
    _put(out, "stated_sale_item", _first(r"\bfrom selling (?:my|the|a) ((?:\w+ ){0,1}\w+?)\b(?: to\b|[.,]|$)", said))
    _put(out, "stated_gift_from", _first(r"\bgift from (?:my )?(\w+(?: \w+)?)\b(?= to\b|[.,]|$)", said))
    amt = re.search(_AMT, said)
    if amt:
        _put(out, "stated_amount", _money(amt.group(1)))
    wd = re.search(r"\bwithdrew (?:back )?in (" + _MON + r") (\d{4})\b", said, re.I)
    if wd:
        out["stated_withdrawal_month"] = f"{wd.group(2)}-{MONTHS.index(wd.group(1).lower()) + 1:02d}"
    query = agent[0] if agent else ""
    _put(out, "queried_counterparty", _first(r"\b(?:from|to)\s+" + _COMPANY, query, 0))
    country = re.search(r"\b(?:payments?|transfer|withdrawal)\b[^.?]{0,25}?\bto ([A-Z][a-z]+(?: [A-Z][a-z]+)?)"
                        r"(?=\s*[.?,])", query)
    if country and not re.search(_CO_SUFFIX + r"$", country.group(1)):
        out["queried_country"] = country.group(1)
    if re.search(r"\bfrom abroad\b", query, re.I):
        out["queried_from_abroad"] = "yes"
    _put(out, "chat_cues", [c for c, p in _CUES if re.search(p, text, re.I)])
    out["note_class"] = "decisive" if out.get("stated_explanations") or out.get("chat_cues") else "filler"
    return out


# ---------------------------------------------------------------- contact_note
_CONTACT_TOPICS = [  # routine admin templates: filler unless something substantive is also present
    ("standing_order", r"\bstanding order\b"),
    ("address_confirmed", r"\baddress remains unchanged\b|\bconfirmed (?:that )?their address\b"),
    ("statement_copies", r"\bcopies of their statements\b|\bpaper (?:copies|statements)\b"),
    ("charges_query", r"\bcharges applied\b|\btariff\b"),
    ("mobile_update", r"\bmobile number\b"),
    ("lost_card", r"\bcard has been (?:lost|stolen)\b|\breplacement card\b|\border for a replacement\b"),
]
_SUBSTANCE_RE = re.compile(r"£|\bcash\b|\bdeposit|\btransfer|\bpayment|\bsource of\b|\bloan\b|\bgift\b|\binvoice\b"
                           r"|\bthreshold\b|\bcrypto|\bsuspicio|\bfraud", re.I)


def _contact_note(doc, text):
    body = _body(text)
    out = {"note_text": body} if body else {}
    topic = next((t for t, p in _CONTACT_TOPICS if re.search(p, body, re.I)), "other")
    out["note_topic"] = topic
    out["note_class"] = "filler" if topic != "other" and not _SUBSTANCE_RE.search(body) else "decisive"
    return out


# ---------------------------------------------------------------- staff_note
_THRESHOLD_RE = re.compile(r"\b(?:below|under|less than|beneath)\s+(?:the\s+)?" + _AMT, re.I)
_STRUCTURING_RE = re.compile(r"\baggregat|\btallied\b|\bcombined\b|\bcumulative\b|\bbreaking down\b|\bsplit"
                             r"|\binstall?ments? of (?:under|less than)\b|\bsegments under\b"
                             r"|\bexempt|\bbypass|\bavoid|\bcircumvent", re.I)
_REFUSED_RE = re.compile(r"\bunwilling\b|\bunable\b|\bdeclined\b|\bevasive\b|\brefused\b|\bwould not explain\b",
                         re.I)


def _staff_note(doc, text):
    body = _body(text)
    out = {"note_text": body} if body else {}
    by = re.match(r"^\s*Note by\s+([^,]+?)\s*,\s*(.+?)\s*$", doc.title or "")
    if by:
        out["note_author"], out["note_branch"] = by.group(1), by.group(2)
    thr = _THRESHOLD_RE.search(body)
    if thr:
        _put(out, "structuring_threshold", _money(thr.group(1)))
    amounts = [_money(m.group(1)) for m in re.finditer(_AMT, body)]
    amounts = [a for a in amounts if a is not None and a != out.get("structuring_threshold")]
    if amounts:
        out["note_amount"] = amounts[0]
    if re.search(r"\btop(?:-|\s)?(?:up|ped up)\b", body, re.I):
        out["deposit_channel"] = "cash_topup"
    elif re.search(r"\bcash deposit|\bdeposit\b[^.]{0,40}\bin cash\b|\bin cash\b", body, re.I):
        out["deposit_channel"] = "cash_deposit"
    if re.search(r"\b(?:different|another|other) branch\b", body, re.I):
        out["intends_other_branch"] = "yes"
    _put(out, "named_company", _first(r"\b(?:director (?:of|at)|representative (?:from|at)|account for|on behalf of"
                                      r"|spoke with|directors of|management at|call with)\s+(?:the\s+)?" + _COMPANY, body, 0)
         or _first(r"\bthe\s+" + _COMPANY + r"\s+account\b", body, 0))
    _put(out, "payment_counterparty", _first(r"\b(?:payments?|transfers?)\s+to\s+" + _COMPANY, body, 0))
    named = []
    for m in _COMPANY_RE.finditer(body):
        name = _LEAD_WORD_RE.sub("", m.group(1))
        if name not in named:
            named.append(name)
    _put(out, "named_companies", named)
    _put(out, "stated_goods_origin", _first(r"\b(?:imported|originating|sourced|arriving|shipped)\s+from\s+"
                                            r"([A-Z][a-z]+(?: [A-Z][a-z]+)?)", body, 0))
    _put(out, "routing_country", _first(r"\b(?:rout(?:ed|ing)|directed|destination of (?:the |these |those )?funds)\b"
                                        r"[^.]{0,60}?\b(?:to|through|via|in)\s+([A-Z][a-z]+(?: [A-Z][a-z]+)?)",
                                        body, 0))
    if _REFUSED_RE.search(body):
        out["explanation_refused"] = "yes"
    if thr and _STRUCTURING_RE.search(body):
        out["note_topic"] = "structuring_query"
    elif out.get("routing_country") and out.get("explanation_refused"):
        out["note_topic"] = "unexplained_routing"
    else:
        out["note_topic"] = "other"
    out["note_class"] = "decisive" if out["note_topic"] != "other" or _SUBSTANCE_RE.search(body) else "filler"
    return out


# ---------------------------------------------------------------- venue_email
def _venue_email(doc, text):
    body = _body(text)
    out = {}
    _put(out, "venue_name", _title_after(doc.title, "Email from"))
    _put(out, "email_subject", _first(_SUBJECT_RE.pattern, text, re.M | re.I))
    ref = re.search(r"\bBOOKING\s*(\d+)\b", body, re.I)
    if ref:
        out["booking_ref"] = "BOOKING " + ref.group(1)
    refund = re.search(r"\brefund\b[^.]{0,80}?" + _AMT, body, re.I)
    if refund:
        _put(out, "refund_amount", _money(refund.group(1)))
    if re.search(r"\bfull refund\b|\brefund of your full\b|\bfull deposit\b", body, re.I):
        out["refund_scope"] = "full"
    elif re.search(r"\bpartial refund\b|\brefund of part\b", body, re.I):
        out["refund_scope"] = "partial"
    if re.search(r"\bwedding\b", body, re.I):
        out["event_type"] = "wedding"
    elif re.search(r"\bevent\b|\bbooking\b", body, re.I):
        out["event_type"] = "event"
    if re.search(r"\bcancel", body, re.I):
        out["cancellation"] = "yes"
    out["note_class"] = "decisive" if "refund_amount" in out or "booking_ref" in out else "filler"
    return out


# ---------------------------------------------------------------- email
def _email(doc, text):
    body = _body(text, drop_subject=True)
    out = {}
    _put(out, "sender_name", _title_after(doc.title, "Email from"))
    _put(out, "email_subject", _first(_SUBJECT_RE.pattern, text, re.M | re.I))
    _put(out, "paying_company", _first(_COMPANY + r"\s+will be\s+(?:sending|processing|initiating|making)", body, 0))
    _put(out, "beneficiary_company", _first(r"\bpayment (?:to|for)\s+" + _COMPANY, body, 0))
    if re.search(r"\bboth (?:companies|businesses)\b[^.]{0,40}\b(?:are mine|under my (?:ownership|control)|I own)\b"
                 r"|\bboth\b[^.]{0,80}\bare under my ownership\b|\bI own both\b", body, re.I):
        out["common_ownership"] = "yes"
    if re.search(r"\breceivables\b", body, re.I):
        out["stated_reason"] = "awaiting_receivables"
    elif body:
        out["stated_reason"] = "other"
    _put(out, "customer_explanation", body)
    out["note_class"] = "decisive" if out.get("paying_company") or out.get("common_ownership") else "filler"
    return out


# ---------------------------------------------------------------- hospital_invoice
def _hospital_invoice(doc, text):
    f = _fields(text)
    out = {}
    _put(out, "issuer", _title_after(doc.title, "Invoice from"))
    _put(out, "patient_name", f.get("patient"))
    _put(out, "treatment", f.get("treatment"))
    amt = re.search(_AMT, f.get("amount due", "") or f.get("amount", "") or f.get("total", ""))
    if amt:
        _put(out, "amount_due", _money(amt.group(1)))
    pay_to = f.get("pay to") or f.get("payee")
    if pay_to:
        name, _, city = pay_to.rpartition(",")
        out["pay_to_name"] = (name or city).strip()
        if name:
            out["pay_to_city"] = city.strip()
    out["note_class"] = "decisive" if "amount_due" in out else "filler"
    return out


# ---------------------------------------------------------------- sending_bank_fraud_report
_SCAM_STORIES = [  # first match wins: "marketplace listing" is goods, not a rental listing
    ("marketplace_goods", r"\bmarketplace\b|\bnothing arrived\b|\bnever (?:arrived|delivered)\b"),
    ("vehicle_deposit", r"\b(?:used )?car\b|\bvehicle\b"),
    ("rental_deposit", r"\bflat\b|\bholding deposit\b|\brent(?:al)?\b"),
    ("tickets", r"\btickets?\b"),
]


def _fraud_report(doc, text):
    f = _fields(text)
    body = _body(text)
    out = {}
    sender = f.get("from", "")
    _put(out, "reporting_bank", sender.rpartition(",")[2].strip() if sender else _title_after(doc.title,
                                                                                            "Fraud report from"))
    _put(out, "report_subject", f.get("subject"))
    out["fraud_type"] = "app_scam" if (re.search(r"\bauthori[sz]ed push payment\b", text, re.I)
                                       or re.search(r"\bAPP\b", text)) else "other"
    sent = re.search(r"\bOur customer\s+" + _NAME + r"\s+sent\s+" + _AMT
                     + r"(?:\s+on\s+(\d{1,2})\s+(" + _MON + r")\s+(\d{4}))?", body, re.I)
    if sent:
        out["victim_name"] = sent.group(1)
        _put(out, "reported_amount", _money(sent.group(2)))
        if sent.group(3):
            out["reported_date"] = _iso(sent.group(3), sent.group(4), sent.group(5))
    _put(out, "account_ending", _first(r"\baccount ending (?:in )?(\d{3,4})\b", body))
    _put(out, "payment_reference", _first(r"\breference\s+[\"']([^\"']+)[\"']", body))
    story = _first(r"\bThey tell us (.+?)(?:\.|$)", body) or ""
    out["scam_story"] = next((s for s, p in _SCAM_STORIES if re.search(p, story, re.I)), "other")
    if re.search(r"\bfreeze\b", body, re.I):
        out["freeze_requested"] = "yes"
    out["note_class"] = "decisive"
    return out


_BY_TYPE = {
    "support_chat": _support_chat,
    "contact_note": _contact_note,
    "staff_note": _staff_note,
    "venue_email": _venue_email,
    "email": _email,
    "hospital_invoice": _hospital_invoice,
    "sending_bank_fraud_report": _fraud_report,
}


def parse(doc):
    fn = _BY_TYPE.get(doc.type)
    return fn(doc, _norm(doc.text or "")) if fn else {}
