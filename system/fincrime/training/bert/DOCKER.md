# Docker inference on unseen raw cases

This is an additional containerized inference path. It includes PyTorch **inside**
the image; the host needs Docker, not PyTorch or an Apple GPU. Container acceptance
must be agreed with the judges: it is not a standard-library-only submission.
The existing `system/run` remains the separate stdlib path.

## Build once

From the repository root, download the pinned portable model (Python 3.12+ stdlib):

```sh
PYTHONPATH=system python3 -m fincrime.training.bert.fetch_portable
docker build -t fincrime-hybrid .
```

For a private GitHub repository, use `gh release download hybrid-cpu-2026-11-21
--pattern modernbert-hybrid-cpu.tar.gz`, then run
`PYTHONPATH=system python3 -m fincrime.training.bert.fetch_portable --archive modernbert-hybrid-cpu.tar.gz`.
The downloader verifies SHA-256 before extraction. No training data or labels are
included in the inference image, and no network is needed at inference time.

## Run on the original input format

The input is an **institution folder** named `bank`, `emoney`, `investment`, or
`payments_fx`, containing the original `cases.jsonl`, `customers.jsonl`,
`alerts.jsonl`, `transactions.jsonl`, `evidence.jsonl`, `previous_reviews.jsonl`,
`internal_list.jsonl`, `POLICY.md`, and the evidence Markdown files referenced by
`evidence.jsonl`. Empty tables may be empty JSONL files. Judges do not need model
features, labels, a split manifest, or recognized IDs.

For example, from the repository root on macOS/Linux:

```sh
mkdir -p predictions
docker run --rm --network none \
  --user "$(id -u):$(id -g)" \
  -v "/absolute/path/to/unseen/bank:/input/bank:ro" \
  -v "$PWD/predictions:/output" \
  fincrime-hybrid /input/bank /output/bank.jsonl \
  --diagnostics /output/bank-diagnostics.jsonl
```

Replace the input mount with the unseen institution folder. Repeat for each
institution. Output paths must not already exist. Omit `--diagnostics` if only the
standard output schema is needed. `--limit N` is for smoke tests only; without it,
the script processes every case. `--threads 4` controls CPU parallelism.

Each output line has `case_id`, `alert_ids`, `verdict`, `reason_code`, `reason`, and
`evidence`, with original input IDs. Diagnostics separately record BERT class
probabilities, rules verdict, edge novelty/suspicion and final verdict.

## What runs internally

1. Validate and normalize the raw records using the training preprocessor.
2. Replace identifiers and known entity names with case-local aliases. Preserve
   equality links, monetary values, dates, and substantive document text.
3. Select the same alert-linked/amount-matching transaction context. Scale the
   same 65 numeric features using **saved training means and scales**, append 65
   missing-value indicators, and split text into 256-token overlapping chunks.
4. Run the selected ModernBERT + numeric MLP on CPU. The portable model contains
   the dequantized NF4 base with the selected LoRA updates merged into its weights;
   it is not a retrained replacement or the original unquantized base.
5. Combine with rules and the trained archetype edge detector. Flags always win;
   learned models never create a CLOSE. BERT disagreement with a rules CLOSE adds
   RFI/ESCALATE caution. Suspicious edge signals also escalate. Reasons and evidence
   are produced by the policy pipeline. LLM calls are disabled for this path.

Malformed/unsupported raw input raises a clear error without writing a completed
prediction file. Unsupported institution names are not inferred or relabeled.
Full raw-file validation and complete input coverage are required; inference never
loads the training labels or looks up known predictions.

## Verification and limitations

The export's 12-case parity check matched 12/12 original predictions, with maximum
probability difference 0.000167. Ten real raw cases reproduced the exact training
text and numeric vectors. Synthetic unseen IDs pass preprocessing without labels
or stored splits. See `system/fincrime/training/tests/test_docker_inference.py` for policy fusion checks.
The classifier's earlier 86.83% validation accuracy is **not** a measured accuracy
of this combined pipeline. Combining models adds caution and does not by itself
prove lower error cost. CPU runtime depends on document volume and hardware.

To regenerate the portable artifact, developers with the original MPS training
environment can run `PYTHONPATH=system python -m fincrime.training.bert.export_portable`. Judges do not run
this export or install its training dependencies.
