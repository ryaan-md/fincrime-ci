# fincrime-ci: AML alert triage (Track 1 submission)

Deterministic, rules-first triage engine for transaction-monitoring alerts. Every CLOSE is proven by documents on file per each institution's POLICY.md; anything unproven becomes an RFI that names the missing document, and red flags always escalate.

- `system/run <institution_folder> <output_file>`: entrypoint (Python 3 standard library only). See `system/README.md` for how it decides, runtime and cost.
- `output/{bank,emoney,investment,payments_fx}.jsonl`: outputs on the development data.

Dev-set results (organiser cost matrix): total cost 546 against 7,800 for escalate-everything (93.0% saved), 0 wrongful closes, verdicts identical across runs.

Built from ryaan-md/fincrime-ci at 113b954b. The organisers' dataset is intentionally not included.
