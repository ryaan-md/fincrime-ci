# ModernBERT + numeric MLP pilot — 2026-11-21

The 30-minute pilot completed in 1,770 seconds (29m30s): 143 optimizer updates, 2,288 of 2,799 training cases, less than one full epoch. The best checkpoint was step 120, selected by macro-F1 on a fixed 120-case stratified validation subset. Its predictions matched on all 120 overlapping cases after checkpoint restoration.

## Full validation (600 cases)

- Cross-entropy loss: 0.327583
- Accuracy: 86.83% (521/600); majority-class baseline: 51.67%.
- Macro-F1: 0.868994
- Weighted error cost: 405; incorrect CLOSE predictions: 24.

| True / predicted | CLOSE | RFI | ESCALATE |
|---|---:|---:|---:|
| CLOSE | 255 | 28 | 27 |
| RFI | 12 | 139 | 0 |
| ESCALATE | 12 | 0 | 127 |

These are agreement metrics against provisional policy-sweep labels, not independently adjudicated accuracy. The 599-case test split was not loaded. The validation set was used for checkpoint selection; it is not an untouched final test. No production runtime changes were made.

## Training curves

Training losses below average the preceding 10 optimizer updates (different cases, training dropout enabled). Validation uses the same fixed 120 cases each time. They are useful trends, not identical measurement conditions.

| Update | Train loss, 10-update mean | Validation loss | Accuracy | Macro-F1 |
|---:|---:|---:|---:|---:|
| 10 | 1.0852 | 1.0524 | 48.3% | 0.3895 |
| 20 | 0.9838 | 1.0623 | 44.2% | 0.3462 |
| 30 | 1.0211 | 1.1380 | 44.2% | 0.3415 |
| 40 | 0.8391 | 0.9169 | 52.5% | 0.4323 |
| 50 | 0.7842 | 0.6826 | 58.3% | 0.4958 |
| 60 | 0.6944 | 0.7021 | 62.5% | 0.5725 |
| 70 | 0.5821 | 0.7759 | 61.7% | 0.5592 |
| 80 | 0.6657 | 0.6368 | 63.3% | 0.6042 |
| 90 | 0.5635 | 0.4572 | 85.0% | 0.8522 |
| 100 | 0.4185 | 0.3879 | 83.3% | 0.8298 |
| 110 | 0.4221 | 0.3656 | 87.5% | 0.8749 |
| 120 | 0.3553 | 0.2998 | 88.3% | 0.8817 |
| 130 | 0.3579 | 0.3819 | 84.2% | 0.8458 |
| 140 | 0.2343 | 0.4322 | 85.8% | 0.8620 |

The final 10-update mean was 0.2452 while the last sampled validation loss rose to 0.4322. The retained step-120 checkpoint had training mean 0.3553 and sampled validation loss 0.2998. This late divergence is why the final weights were not selected.

## Architecture and artifacts

See README.md for exact architecture, preprocessing, dependencies and commands. The model trains 559,364 parameters: last-eight-layer rank-8 NF4 QLoRA, attention pooling, a 130→128→128 numeric MLP, and a fused 896→256→3 classifier. Batch 2 × accumulation 8 = effective 16. Numeric scaling uses only training cases; identifiers use case-local aliases.

Local ignored artifacts: `dist/modernbert-hybrid/best/` (selected adapter and classifier), `validation-600.json` (all predictions), `result.json`, `manifest.json`, `curves.png`; normalization metadata is in `dist/bert-hybrid-data/manifest.json`. The original model revision/hash is recorded in the run manifest.

Verification: `make gate` passed after syncing with main: 376 tests (one optional PyTorch test skipped in stdlib environment), leak OK, determinism OK. The optional hybrid gradient/accumulation test passed separately in the training environment. Production gate results after sync: cost 4026, trap catch 71.1%, false alarms 0%, hygiene 0. These production scores are unrelated to the classifier validation scores above.
