"""4. EXPLAINER. Runs AFTER the arbiter has fixed verdict, code and evidence. Owner: Rio (T-EXPLAIN).
  explain(case, decision) -> reason text (2-4 sentences)

Path: template (reasons/templates.py, deterministic, always available)
   -> if FINCRIME_LLM_REASONS=1 and a key is set: LLM rewrites it from the FACTS BUNDLE ONLY (temperature 0)
   -> validator: every ID-like token and every £ amount in the LLM text must appear in the facts/case/template;
      2-4 sentences; RFI must say what to ask for. Any failure -> keep the template.
The LLM never changes verdict, code or evidence (those are the reproducibility-scored fields)."""
import json, os, re
from .reasons import render
from . import llm

ID_RE = re.compile(r"\b[A-Z]{2,4}-[A-Z]{0,3}\d{3,}\b")
GBP_RE = re.compile(r"£\s?[\d,]+(?:\.\d{1,2})?")


def _allowed_tokens(case, decision, template):
    blob = json.dumps(decision.facts, default=str) + " " + " ".join(decision.evidence) + " " + template
    blob += " " + " ".join(d.doc_id for d in case.docs) + " " + " ".join(a["alert_id"] for a in case.alerts)
    blob += " " + " ".join(f"£{t['amount_gbp']:,.2f}" for t in case.txns)
    return set(ID_RE.findall(blob)), {g.replace(" ", "") for g in GBP_RE.findall(blob)}


def validate(text, case, decision, template):
    if not text or not (1 <= len(re.findall(r"[.!?](\s|$)", text)) <= 5):
        return False
    ids, gbp = _allowed_tokens(case, decision, template)
    if any(i not in ids for i in ID_RE.findall(text)):
        return False
    if any(g.replace(" ", "") not in gbp for g in GBP_RE.findall(text)):
        return False
    if decision.verdict == "RFI" and not re.search(r"\b(ask|request|provide|send)\b", text, re.I):
        return False
    return True


def explain(case, decision):
    facts = dict(decision.facts)
    facts.pop("llm_reason", None)
    template = decision.facts.get("llm_reason") or render(case, decision.verdict, decision.reason_code, facts)
    if os.environ.get("FINCRIME_LLM_REASONS") == "1":
        text = llm.rewrite_reason(decision.verdict, decision.reason_code, decision.evidence, facts, template)
        if validate(text, case, decision, template):
            return text.strip()
    return template
