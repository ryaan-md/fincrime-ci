# Anonymized ModernBERT case classifier

Offline experiment using the versioned preprocessing snapshot in
an externally supplied preprocessing snapshot. The input artifacts are checked
against their preprocessing manifest. Normalized customer/case/alert/transaction
records, full normalized documents, historical reviews, relevant internal-list
records and case features are used. Current-case labels, generated rationales,
checker code and execution results are never predictive inputs.

Identifiers and known entity names receive consistent case-local aliases, reset
for every case. Matching is case-insensitive; values such as amounts and dates
remain. Source-ID patterns and known identifiers are audited after replacement.
Technical alert rule IDs are removed while their meaningful rule names remain.
This is identifier pseudonymization, not a claim of complete free-text PII removal.

Corrected labels are joined separately and must match the preprocessing split.
The two withheld cases are excluded: 2,799 train, 600 validation, 599 test.
The trainer never loads the test split. Targets remain provisional policy-sweep
labels, not independent human adjudications.

ModernBERT-base is loaded with bitsandbytes NF4 double quantization and FP16
compute on Apple MPS. Rank-8 LoRA (alpha 16, dropout .05) adapts QKV/output attention
projections in the final eight layers. A learned attention pool combines
256-token chunks (64-token overlap) into one case representation. A numeric MLP
feeds the three-way classification head alongside that text representation. Chunking preserves text but does not provide joint attention
across all chunks. The head has dropout .1. The model predicts only CLOSE/RFI/ESCALATE.

Physical batch: two cases. Accumulation: eight microbatches, effective batch 16.
AdamW learning rates: adapters 1e-4, head/pool 1e-3; ten-update linear warm-up,
weight decay .01, gradient norm cap 1, seed 42. Loss is weighted by case count,
including the last partial accumulated batch. Hybrid trainable parameter count: 559,364.

The configured run is bounded around 30 minutes with validation-driven early
stopping (three checks without .005 macro-F1 improvement). Intermediate validation
uses 120 stratified cases; final evaluation uses all 600 validation cases. Baseline
uses 12 stratified cases. The best validation checkpoint is retained. Timing checks
occur between optimizer updates; evaluation/checkpoint overhead may extend wall time.

Commands from the worktree root, with the isolated environment active:

```sh
PYTHONPATH=system python -m fincrime.training.bert.data --source /path/to/preprocessed --labels /path/to/annotations.jsonl --output dist/bert-anonymized-v3
PYTHONPATH=system python -m fincrime.training.bert.hybrid_data
PYTHONPATH=system python -m fincrime.training.bert.train --data dist/bert-hybrid-data --chunk-size 256 --probe --output dist/modernbert-hybrid-probe
PYTHONPATH=system python -m fincrime.training.bert.train --data dist/bert-hybrid-data --chunk-size 256 --output dist/modernbert-hybrid
```

Model/source, data hashes, hyperparameters, progress, adapters and validation
predictions are saved under ignored `dist/`. The submission runtime is unchanged.

## Active hybrid run

The active run uses `dist/bert-hybrid-data` and `dist/modernbert-hybrid`. A 65-feature
numeric branch applies signed log1p and training-only mean/std scaling, appends
missingness indicators (130 inputs), then uses a 128/128 MLP. Its representation
is concatenated with the ModernBERT text representation and classified by a
256-unit fusion head. Text preserves all documents and alerts, customer/review/list
context, and alerted or amount-matching transaction rows. Other transaction rows
are represented through the preprocessed aggregate features, not individual text.

The hybrid uses 256-token chunks with 64-token overlap, batch 2 and accumulation 8.
It synchronizes and clears the MPS allocator cache between microbatches and uses
contiguous chunk slices for case pooling. The earlier 1024-token text-only attempt
exceeded available memory and was stopped; it is not the active configuration.

```sh
PYTHONPATH=system python -m fincrime.training.bert.hybrid_data
PYTHONPATH=system python -m fincrime.training.bert.train --data dist/bert-hybrid-data --batch-size 2 --accumulation 8 --chunk-size 256 --output dist/modernbert-hybrid
```

Validated training environment: Python 3.14, torch 2.14.1, transformers 5.24.0,
peft 0.22.0, bitsandbytes 0.50.2, scikit-learn 1.9.1. These are offline training
dependencies only; the submission system remains Python stdlib.
