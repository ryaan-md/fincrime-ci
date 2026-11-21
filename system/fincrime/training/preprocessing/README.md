# Deterministic dataset preparation

This offline tool prepares Track 1 data through grouped train/validation/test splits.
It uses Python 3.9+ standard library only and never calls an LLM, trains a model,
reads gold labels, or changes the submission engine. Run from any working directory.

```sh
python3 preprocessing/prepare.py \
  --input hackathon/institutions \
  --output /tmp/fincrime-prepared \
  --ratios 0.70 0.15 0.15 \
  --seed fincrime-v1
```

Input can also be a single institution folder. Output must be a new or empty directory,
separate from the input. Store generated data outside the source tree (or under ignored
`dist/`). Existing outputs are never overwritten. Expect hundreds of MB because originals
and normalised representations are deliberately both retained. This is an offline research
entry point, not `hackathon/system/run` and not part of the submitted runtime.

## Data preservation

Every input JSONL row is stored with:

- `raw`: its complete original JSON object, including fields unknown to the pipeline.
- `raw_json`: the original line (without its line separator), preserving numeric spelling,
  whitespace, key order and escaping for exact round-trip experiments.
- `normalized`: a separate representation. Monetary values use decimal strings,
  UTC timestamps are explicit, date-only fields remain dates, names are Unicode-normalised
  and casefolded, account keys lose whitespace and become uppercase.
- `key`: an institution-qualified join key.

Numeric calculations parse source numbers with `Decimal`, not binary floats. The raw JSON
object is convenient for ordinary readers; use `raw_json` with `parse_float=Decimal` when
exact numeric lexemes matter. Strings are never overwritten. Documents retain complete
`raw_text`, including CRLF and spacing, plus `normalized_text`, source hash and an ordered
list of simple Markdown `**Field:** value` occurrences with line numbers. Repeated fields
are retained. There is no semantic interpretation of chat text or ownership.

Country strings are casefolded and whitespace-normalised, not inferred or silently converted
to ISO codes. Thus aliases may remain distinct; the raw value remains available.

## Joins and time context

- Cases → customers through `customer_id`.
- Cases → all alerts through `alert_ids`, checking the reverse `case_id` and customer.
- Alerts → transactions through `transaction_ids`, verifying customer ownership.
- Cases → held payment through `held_payment_id`.
- Customer → all transactions, document metadata and earlier reviews.
- Document metadata → the exact file path inside the institution folder.
- Counterparty names → exact normalised internal-list name candidates. These are candidates,
  not confirmed matches. All original list entries are exported for broader matching later.

A case bundle refers to exported transactions and documents by key instead of duplicating
large texts. Its separate time views identify review-window, pre-window, post-window, and
explicitly alert-linked transactions. Membership can overlap, but feature sums use each
review-window transaction once. Alert-linked transactions outside the window are retained.
All documents and reviews remain accessible; future-dated items are counted explicitly.
An opening date earlier than the supplied window end is a warning, not grounds to discard
organiser-provided records. The supplied review window determines numerical features.

Missing references, cross-customer joins, duplicate IDs, unsafe paths, invalid windows and
unparseable transaction timestamps/money fail the run before model datasets are written.
Integrity errors produce a `quality_report.json`; malformed input/IO errors also exit nonzero.
Warnings preserve data. No invalid case is silently skipped.

## Outputs

| File | Contents |
|---|---|
| `case_bundles.jsonl` | One joined bundle per case; original case/customer/alerts/reviews and transaction/document pointers |
| `*_records.jsonl` | All seven input tables with raw, raw_json and normalized representations |
| `documents.jsonl` | Full raw document text, metadata, normalised text and Markdown fields |
| `case_documents.jsonl` | Case-to-document links |
| `policies.jsonl` | Exact policy text and hashes |
| `case_features.jsonl` | Authoritative feature records, one per case |
| `case_features.csv` | Same features in CSV; empty cells mean null |
| `alert_features.jsonl` | Rule, source measured values and linked transactions per alert |
| `splits.json` | Ratios, salt, component groups, assignments and achieved counts |
| `train_index.jsonl`, `validation_index.jsonl`, `test_index.jsonl` | Case keys and group IDs for each split |
| `feature_schema.json` | Column types, roles, nullability and numeric representation |
| `quality_report.json` | Counts and stable, sorted data-quality issues |
| `preprocessing_manifest.json` | Input hashes, code/runtime versions, configuration and output hashes |

Read JSONL line by line using `json.loads`. Join a case's `document_keys` against
`documents.jsonl:key` to retrieve its raw support chats or other text. Every model row has
`case_key`; use that key against `splits.json:assignments`. Keep IDs for joining and
explanations, not as predictive inputs.

## Feature dictionary

All transaction aggregates use the case's inclusive review window, in UTC.

| Columns | Meaning |
|---|---|
| `institution`, `customer_kind`, `risk_rating` | Categorical context, no learned encoding |
| `alert_count`, `alert_count_TM*` | Case-level trigger counts |
| `transaction_count`, `active_days` | Current-window transaction volume and active dates |
| `historical_transaction_count`, `post_window_transaction_count` | Coverage/context outside the window |
| `in_count`, `out_count` | Direction-specific transaction counts |
| `in_total_gbp`, `out_total_gbp`, `in_max_gbp`, `out_max_gbp` | Exact GBP aggregates; maximum is null for an empty subset |
| `in_counterparty_count`, `out_counterparty_count` | Distinct account keys, falling back to normalised names; not resolved identities |
| `*_missing_counterparty_count` | Transactions with neither account nor name |
| `declared_monthly_basis_gbp`, `declared_basis_source`, `declared_basis_missing` | Income for individuals; annual turnover / 12 for businesses, including payments/FX's actual annual-volume field |
| `inflow_to_declared_ratio` | Incoming GBP total / positive monthly basis; null if unavailable |
| `profile_alert_threshold_ratio` | TM01 policy multiplier: 3 for individuals, 2 for businesses |
| `inflow_to_profile_threshold_ratio` | Inflow ratio / policy multiplier; equality is not a strict threshold breach |
| `outflow_to_inflow_ratio` | Whole-window outflow / inflow; not a rapid-funds attribution calculation |
| `median_transaction_gbp`, `mean_transaction_gbp` | Aggregated unsigned amounts across both directions |
| `channel_count_*`, `channel_total_gbp_*` | Exact source channel counts and sums; fixed categories, no fitted vocabulary |
| `missing_channel_count` | Transactions without a channel |
| `counterparty_country_count`, `missing_counterparty_country_count` | Observed country coverage |
| `undeclared_country_transaction_count` | Known country absent from declared list; null if no declared countries. This does not mean high risk |
| `days_since_last_observed_prior_transaction`, `prior_history_absent` | Observed gap before window start; not proof of 180-day dormancy |
| `document_count`, `document_count_*` | Document presence counts; presence does not establish an explanation |
| `documents_after_window_count`, `document_missing_date_count` | Document date coverage |
| `previous_review_count`, `reviews_after_window_count` | Historical review availability; historical CLOSE is not today's target |
| `held_payment_present` | Whether a held-payment reference exists |

Source `measured` values for every rule are preserved in `alert_features.jsonl`. This version
recomputes broad aggregates and TM01-relative features, not complete TM02–TM22 rule execution.
It does not infer payment attribution, ownership, invoice reconciliation, fund lot matches,
semantic contradictions, high-risk-country policy sets, labels, or confidence
scores. No missing value is imputed and no scaler, encoder or TF-IDF vocabulary is fitted.
Those steps belong after the split and must be fitted on training data only.

## Grouped split

Default 70/15/15. Union-find groups are formed using:

1. The same institution-qualified customer across all their cases.
2. Shared account identifiers in customer `account_ids` and transaction `account_id` fields.
3. A transaction counterparty account that is also a known customer's owned account.

Account keys are compared globally across institutions; grouping errs toward keeping matching
keys together. A shared external merchant or payee alone does **not** group unrelated customers.
Shared names, phones, addresses and inferred document ownership do not establish a grouping.

Whole connected components are assigned with a deterministic greedy allocation balancing
case counts by institution. Larger groups go first; SHA-256 of salt + group root provides a
stable tie order. No random generator, Python hash(), clock or label is used. Ratios are targets,
not guarantees when large components exist. Group IDs, membership, achieved counts and largest
component are exported. Cases with multiple alerts never span splits.

Freeze the resulting manifest for an experiment. Adding cases can change allocation, so a new
input snapshot is a new split version. This split prevents known customer/account leakage;
it does not certify independence of repeated synthetic scenario templates. Evaluate that
separately before making generalisation claims.

## Verification

```sh
python3 -m unittest discover -s tests -p test_preprocess.py -v
```

Tests cover exact raw preservation, cosmetic normalisation, invalid joins and paths,
missing evidence, null semantics, out-of-window evidence, annual-volume conversion,
transitive shared accounts, cross-institution grouping and fresh-process byte equality
under different hash seeds and timezones. Run the CLI into two empty directories and compare
all output hashes for a full-dataset check. Identical inputs/code/config/runtime must produce
identical output bytes; output directory and wall-clock time are not included in artifacts.
