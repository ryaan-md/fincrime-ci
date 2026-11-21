"""Optional OpenRouter fallback (README: only OpenRouter chat completions, key in OPENROUTER_API_KEY).
OFF unless FINCRIME_LLM=1. Temperature 0, fixed seed, strict JSON, NEVER returns CLOSE.
Budget: README says < ~$4 and < 30 min per 200 cases. Owner: T-LLM."""
import json, os, urllib.request

URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.environ.get("FINCRIME_LLM_MODEL", "openai/gpt-4o-mini")
TIMEOUT = 25


def _chat(messages):
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    body = json.dumps({"model": MODEL, "messages": messages, "temperature": 0, "seed": 7,
                       "response_format": {"type": "json_object"}}).encode()
    req = urllib.request.Request(URL, body, {"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]
    except Exception:
        return None


def fallback(case, policy):
    if os.environ.get("FINCRIME_LLM") != "1":
        return None
    # TODO(T-LLM): build a compact structured summary (alerts, measured, key txns, doc texts dated <= window end)
    # and the relevant POLICY.md excerpt; parse {"verdict","reason_code","reason","evidence"}; reject CLOSE.
    return None


def rewrite_reason(verdict, code, evidence, facts, template):
    """T-EXPLAIN: rewrite the template into 2-4 precise sentences using ONLY the facts. Returns text or None."""
    if not os.environ.get("OPENROUTER_API_KEY"):
        return None
    sys = ("You write the reason line for an AML alert decision that has ALREADY been made. Use ONLY the facts given. "
           "2-4 sentences. Name the specific documents (by ID), amounts, dates and people. Do not change the verdict. "
           "For RFI, say exactly what to ask the customer for. Never invent IDs or amounts. "
           'Reply as JSON: {"reason": "..."}')
    user = json.dumps({"verdict": verdict, "reason_code": code, "evidence": evidence, "facts": facts,
                       "draft": template}, default=str)
    out = _chat([{"role": "system", "content": sys}, {"role": "user", "content": user}])
    try:
        return json.loads(out)["reason"] if out else None
    except Exception:
        return None
