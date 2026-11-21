# Container verification — 2026-11-21

Verified on Linux ARM64 in Docker/Colima, with CPU-only PyTorch 2.14.1+cpu and
Transformers 5.24.0. No Apple GPU, bitsandbytes, PEFT, host PyTorch, labels or saved
train/validation splits are used by the container runtime.

| Check | Result |
|---|---|
| Standard-library unit run | 10 tests, 9 passed and the optional Torch test skipped |
| Optional hybrid gradient/accumulation test | Passed separately in the training environment |
| Original NF4 versus portable CPU export | 12/12 predictions match; maximum probability difference 0.000167 |
| Raw inference versus training preprocessing | Exact text/numeric match on 10 existing cases |
| Default submission runner | All 4,000 outputs match the four checked-in output files byte-for-byte |
| Docker, original raw bank format | 3 cases produced valid standard-schema predictions |
| Docker, synthetic unseen IDs | 2 cases produced valid predictions without labels or split files |
| Repeatability | Unseen predictions and diagnostics are byte-identical across runs |
| Network isolation | Docker smoke runs used `--network none` |
| Non-root execution | Passed with default UID 10001 and host UID/GID 501:20 |
| Mac CPU versus Linux CPU classifier | 3/3 predictions match; maximum probability difference 1.1920929e-7 |
| Final container source | SHA-256 of infer/data/hybrid_data/model modules matches the PR source |

The final-image unseen-data run produced byte-identical predictions and diagnostics
to the earlier verified run. Model file permissions are explicitly readable by the
non-root runtime user. Per-file model checksums are verified at startup.

These are integration and portability checks, not an evaluation of combined-model
accuracy on unseen gold labels or a full-batch throughput benchmark. Linux AMD64
was not executed locally. The existing rules-only entrypoint remains unchanged;
Ryaan's independent explainer PR can be merged without replacing its logic here.

Portable asset: release `hybrid-cpu-2026-11-21`, file
`modernbert-hybrid-cpu.tar.gz` (307,402,872 bytes).
SHA-256: `352be8442edfaa1ea57da2a3e8af887b5f61dd5af9750ecb20e2cb33275ab8dd`.
