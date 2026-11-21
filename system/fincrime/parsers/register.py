"""T-PRS-REGISTER: fields from company_register_extract, significant_control_register, group_structure and
reverification_record. parse(doc) -> dict merged into Doc.fields. Regex on **Field:** lines and tables; no LLM.

Keys (absent when the document does not state the fact; never guessed). Dates are ISO "YYYY-MM-DD",
birth months are "YYYY-MM", percentages are floats.

company_register_extract
  company_name         str   **Company:** line, else the "Company register extract for <name>" heading
  company_number       str   digits only
  incorporated_on      date
  registered_office    str
  nature_of_business   str
  company_status       str   the Status / Company status line, lower-cased with whitespace collapsed and qualifiers
                             kept (e.g. "active", "active - proposal to strike off"); "dissolved" when the line
                             starts with "Dissolved"; absent when the extract has no status line
  dissolved_on         date  only when dissolved and the date is given
  officers             list  [{name, role, born, appointed, resigned}] in table order; missing cells are None
  psc                  list  [{name, born, control, pct, pct_min, pct_max, notified, ceased}] (see below)

significant_control_register
  company_name         str   from the "... significant control for <name>" heading
  psc                  list  [{name, born, control, pct, pct_min, pct_max, notified, ceased}]
                             control = raw text; pct = the exact share when stated, e.g. "(60%)", else None;
                             pct_min/pct_max = the band ("75% or more" -> 75/100,
                             "more than 50% but less than 75%" -> 50/75); notified/ceased dates or None

group_structure
  group                list  [{company, owner, share_pct}] in table order ("Owner" or "Owned by" column)
  group_companies      list  sorted unique names in the Company column

reverification_record
  reverified_on        date  "re-verified on <date>"
  live_selfie          bool  a live selfie was used
  registered_device    bool  done on the device already registered to the customer/directors
  verified_party       str   who the device is registered to: "customer" or "directors"
  address_confirmed    bool
  stated_reason        str   what the customer/directors said, e.g. "... had been working in ... for the past year."
  stated_location      str   place in the stated reason, as written
                             ("working / living / based / staying / studying in <Place>")
  stated_country       str   last part of a stated "<City>, <Country>" place (never inferred from a city alone)
"""
import re

TYPES = ("company_register_extract", "significant_control_register", "group_structure", "reverification_record")

MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
DAY_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+" + _MON + r",?\s+(\d{4})\b", re.I)
ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
MONTH_YEAR_RE = re.compile(r"^\s*" + _MON + r"\s+(\d{4})\s*$", re.I)
ISO_MONTH_RE = re.compile(r"^\s*(\d{4})-(\d{2})\s*$")
FIELD_RE = re.compile(r"^\s*[-*]?\s*\*\*\s*([^*:]+?)\s*:?\s*\*\*\s*:?\s*(.*?)\s*$", re.M)
HEADING_RE = re.compile(r"^\s*#+\s*(.+?)\s*$", re.M)
PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _month(tok):
    tok = tok.lower().rstrip(".")
    for name, n in MONTHS.items():
        if name.startswith(tok[:3]):
            return n
    return None


def _date(s):
    """First full date in s as ISO, else None."""
    if not s:
        return None
    m = DAY_RE.search(s)
    if m:
        mo = _month(m.group(2))
        if mo and 1 <= int(m.group(1)) <= 31:
            return f"{int(m.group(3)):04d}-{mo:02d}-{int(m.group(1)):02d}"
    m = ISO_RE.search(s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def _born(s):
    """'March 1976' / 'Mar 1976' / '1976-03' -> '1976-03', else None."""
    if not s:
        return None
    m = MONTH_YEAR_RE.match(s)
    if m:
        mo = _month(m.group(1))
        return f"{int(m.group(2)):04d}-{mo:02d}" if mo else None
    m = ISO_MONTH_RE.match(s)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{m.group(1)}-{m.group(2)}"
    return None


def _clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def _fields(text):
    out = {}
    for k, v in FIELD_RE.findall(text):
        out.setdefault(_clean(k).lower(), _clean(v))
    return out


def _tables(text):
    """[(headers lower-cased, [row dict header -> cell or None])] for every markdown table."""
    tables, cur = [], None
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            cur = None
            continue
        cells = [_clean(c) for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c) and any(cells):
            continue
        if cur is None:
            cur = ([c.lower() for c in cells], [])
            tables.append(cur)
        else:
            cur[1].append({h: (cells[i] if i < len(cells) and cells[i] else None) for i, h in enumerate(cur[0])})
    return tables


def _col(row, *names):
    for n in names:
        if row.get(n):
            return row[n]
    return None


def _control(text):
    """Control text -> (pct, pct_min, pct_max)."""
    t = (text or "").lower()
    pct = None
    m = re.search(r"\(\s*(\d+(?:\.\d+)?)\s*%\s*\)", t)
    if m:
        pct = float(m.group(1))
    nums = [float(x) for x in PCT_RE.findall(re.sub(r"\([^)]*\)", "", t))]
    lo = hi = None
    if "or more" in t and nums:
        lo, hi = nums[0], 100.0
    elif ("more than" in t or "over" in t) and ("less than" in t or "under" in t) and len(nums) >= 2:
        lo, hi = nums[0], nums[1]
    elif len(nums) == 1 and not re.search(r"more|less|over|under|up to", t):
        lo = hi = nums[0]
    if pct is None and lo is not None and lo == hi:
        pct = lo
    return pct, lo, hi


def _psc_rows(rows):
    out = []
    for r in rows:
        name = _col(r, "person with significant control", "person", "name")
        if not name:
            continue
        control = _col(r, "control", "nature of control")
        pct, lo, hi = _control(control)
        out.append({"name": name, "born": _born(_col(r, "born", "date of birth", "dob")), "control": control,
                    "pct": pct, "pct_min": lo, "pct_max": hi,
                    "notified": _date(_col(r, "notified")), "ceased": _date(_col(r, "ceased"))})
    return out


def _heading_name(text, marker):
    m = HEADING_RE.search(text)
    if not m:
        return None
    h = m.group(1)
    i = h.lower().find(marker)
    if i < 0:
        return None
    return _clean(h[i + len(marker):].split(":")[0]) or None


def _company_register(text):
    f, out = _fields(text), {}
    name = f.get("company") or _heading_name(text, "extract for ")
    if name:
        out["company_name"] = name
    num = re.sub(r"\D", "", f.get("company number", ""))
    if num:
        out["company_number"] = num
    inc = _date(f.get("incorporated") or f.get("incorporated on") or f.get("date of incorporation"))
    if inc:
        out["incorporated_on"] = inc
    for k, key in (("registered office", "registered_office"), ("nature of business", "nature_of_business")):
        if f.get(k):
            out[key] = f[k]
    status = _clean(f.get("status") or f.get("company status")).lower()
    if status.startswith("dissolved"):
        out["company_status"] = "dissolved"
        d = _date(status)
        if d:
            out["dissolved_on"] = d
    elif status:
        out["company_status"] = status
    for headers, rows in _tables(text):
        if headers and headers[0] in ("officer", "officers", "name") and "role" in headers:
            out["officers"] = [{"name": _col(r, *headers[:1]), "role": _col(r, "role"),
                                "born": _born(_col(r, "born", "date of birth", "dob")),
                                "appointed": _date(_col(r, "appointed")), "resigned": _date(_col(r, "resigned"))}
                               for r in rows if _col(r, *headers[:1])]
        elif headers and headers[0] in ("person with significant control", "person"):
            out["psc"] = _psc_rows(rows)
    return out


def _psc_register(text):
    out = {}
    name = _heading_name(text, "significant control for ")
    if name:
        out["company_name"] = name
    for headers, rows in _tables(text):
        if headers and headers[0] in ("person", "person with significant control", "name"):
            out["psc"] = _psc_rows(rows)
    return out


def _group(text):
    out = {}
    for headers, rows in _tables(text):
        if headers and headers[0] == "company":
            group = []
            for r in rows:
                if not r.get("company"):
                    continue
                share = PCT_RE.search(_col(r, "share", "shareholding", "%") or "")
                group.append({"company": r["company"], "owner": _col(r, "owner", "owned by", "parent"),
                              "share_pct": float(share.group(1)) if share else None})
            out["group"] = group
            out["group_companies"] = sorted({g["company"] for g in group})
    return out


def _reverification(text):
    out, body = {}, _clean(text)
    low = body.lower()
    m = re.search(r"re-?verified on ([^.,;]+)", body, re.I)
    d = _date(m.group(1)) if m else None
    if d:
        out["reverified_on"] = d
    out["live_selfie"] = "live selfie" in low
    m = re.search(r"device already registered to the (\w+)", low)
    out["registered_device"] = bool(m)
    if m:
        out["verified_party"] = m.group(1)
    out["address_confirmed"] = bool(re.search(r"address (?:was )?confirmed", low))
    m = re.search(r"\bsaid (?:that )?([^.]+\.)", body, re.I)
    if m:
        out["stated_reason"] = m.group(1)
        place = re.search(r"\b(?i:working|living|based|staying|studying)\s+(?i:in|abroad in)\s+"
                          r"([A-Z][\w'-]*(?:[ -][A-Z][\w'-]*)*(?:,\s*[A-Z][\w'-]*(?:[ -][A-Z][\w'-]*)*)?)",
                          m.group(1))
        if place:
            out["stated_location"] = place.group(1)
            if "," in place.group(1):
                out["stated_country"] = place.group(1).rsplit(",", 1)[1].strip()
    return out


_BY_TYPE = {"company_register_extract": _company_register, "significant_control_register": _psc_register,
            "group_structure": _group, "reverification_record": _reverification}


def parse(doc):
    fn = _BY_TYPE.get(doc.type)
    return fn(doc.text or "") if fn else {}
