# fincrime: how the system decides

**Run:** `system/run <institution_folder> <output_file>`. Python 3 standard library only, and it works from any working directory.

**Approach: CI for alert review.** Each case is parsed into a structured object (alerts, transactions, documents and their fields, the customer's declared profile, internal-list hits). Then:
1. **Red-flag scanners** look for signs of crime regardless of documents: sending-bank fraud reports, the internal-list party identified (e.g. matching date of birth, officer still in post or significant control at the payment date), notes about staying under limits, and others. Any flag means **ESCALATE**.
2. **Proof checkers**, one per CLOSE reason, verify every alert against the documents on file: amount to the penny, payee/holder identity, document dated on or before the transaction, and policy limits (own-account under 5x declared activity, invoices for payments of £10k or more at the payments firm). The case is **CLOSED** only if every alert has a passing proof.
3. Otherwise it's an **RFI** naming the missing document when nothing is suspicious, or an **ESCALATE** when the activity has no credible explanation.
4. An **edge-case detector** (unsupervised) scores how unusual the case is. A fixed-rule **arbiter** combines it with the rules: unusual cases can be downgraded from CLOSE, but nothing can be upgraded to CLOSE.
5. The **explainer** writes the reason last, from the facts that decided the case. Templates are filled with this case's document IDs, amounts and names; an optional LLM rewrite (OpenRouter, temperature 0) is accepted only if a validator confirms every ID and amount it mentions exists. `evidence` lists exactly the IDs relied on.

**Model:** none. No LLM is used at runtime by default; an OpenRouter reason-rewrite (`FINCRIME_LLM_REASONS=1`, temperature 0, validated) exists but is off, and it can never produce a CLOSE.

**Runtime and cost:** a run of 200 cases takes about 1–3 seconds on a laptop and costs $0 (no LLM calls).

**Determinism:** no randomness or clock; stable ordering. Identical output across runs.
