# Selected hybrid checkpoint

Selected optimizer step: 120. See ../RESULTS.md for validation and limitations.

`adapter/` contains trained LoRA weights; `classifier.pt` contains attention pooling,
the numeric MLP and fusion head. `preprocessing.json` records numeric feature order
and training-only normalization; `labels.json` gives output order. The adapter
requires the exact base revision in `base-source.json`.

The Docker-ready CPU export is distributed in the `hybrid-cpu-2026-11-21` release.
Use `fincrime.training.bert.fetch_portable` to verify and extract it, then follow
../DOCKER.md. This release contains inference weights and metadata, not training
examples or labels. The original NF4 adapter requires the MPS training environment;
the portable export does not.
