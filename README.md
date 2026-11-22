# fincrime-ci: AML alert triage (Track 1 submission)

Deterministic, rules-first triage engine for transaction-monitoring alerts. Every CLOSE is proven by documents on file per each institution's POLICY.md; anything unproven becomes an RFI that names the missing document, and red flags always escalate.

- `system/run <institution_folder> <output_file>`: entrypoint (Python 3 standard library only). See `system/README.md` for how it decides, runtime and cost.
- `output/{bank,emoney,investment,payments_fx}.jsonl`: outputs on the development data.

Dev-set results (organiser cost matrix): total cost 174 against 7,800 for escalate-everything (97.8% saved), 0 wrongful closes, verdicts identical across runs.

Optional LLM review: when `OPENROUTER_API_KEY` is set, cases no rule settles are reviewed by an LLM via OpenRouter (temperature 0, cached, validated; it can only choose RFI or ESCALATE, never CLOSE). Without a key the output is identical to `output/`. See `system/README.md`.

## Architecture

```mermaid
flowchart TD
    IN["Institution folder<br/>cases, alerts, customers, transactions,<br/>evidence, previous reviews, internal list"] --> LOAD["loader.py<br/>read JSONL, drop malformed rows,<br/>parse documents (parsers/)"]
    LOAD --> POL["policy/&lt;institution&gt;.py<br/>per-institution POLICY.md rules"]
    POL --> RULES

    subgraph RULES["Rules (per case)"]
        FLAGS["flags/<br/>red-flag scanners<br/>(listed party, mule, structuring,<br/>high-risk country, fraud report...)"]
        CHECKS["checks/<br/>close checkers per alert<br/>(invoice matches, own account, refund,<br/>within profile, returning customer...)"]
    end

    RULES --> EDGE["edge.py + edge_model.json<br/>stdlib anomaly score:<br/>unusual / suspicious"]
    EDGE --> A{"A: any red flag?"}
    A -- yes --> ESC1["ESCALATE<br/>flag's reason code"]
    A -- no --> P{"All alerts proven<br/>by documents?"}
    P -- yes --> BC{"Edge score"}
    BC -- "unusual + suspicious (B)" --> ESC2["ESCALATE"]
    BC -- "unusual (C)" --> RFI1["RFI"]
    BC -- "normal (D)" --> CLOSE["CLOSE<br/>proof's reason code"]
    P -- no --> E{"E: a checker found<br/>a missing document?"}
    E -- yes --> RFI2["RFI<br/>names the missing document"]
    E -- no --> F{"F: OPENROUTER_API_KEY set?<br/>LLM review (never CLOSE)"}
    F -- "valid answer" --> LLMV["RFI or ESCALATE"]
    F -- "no key / invalid" --> G{"G: unusual?"}
    G -- yes --> ESC3["ESCALATE"]
    G -- no --> H{"H: RFI default<br/>for these alert rules?"}
    H -- yes --> RFI3["RFI"]
    H -- no --> ESC4["I: ESCALATE"]

    ESC1 & ESC2 & RFI1 & CLOSE & RFI2 & LLMV & ESC3 & RFI3 & ESC4 --> EXP["explain.py + reasons/templates.py<br/>plain-English reason citing<br/>document / transaction / list IDs"]
    EXP --> OUT["output/&lt;institution&gt;.jsonl<br/>one line per case, deterministic"]
```

Red flags always win, a CLOSE needs every alert proven by documents on file, and anything unsettled falls to RFI or ESCALATE. Any crash on a case also falls back to ESCALATE, so the engine never closes when unsure.
