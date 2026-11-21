# Optional model pipeline

All current model pipeline source lives here: ModernBERT QLoRA, the numeric MLP,
training-input preparation, portable CPU export, Docker inference, and archetype
edge-model training. The default `system/run` does not import this package or need
third-party dependencies. No raw development data or training labels are bundled.

- `bert/DOCKER.md`: run the exact trained model on unseen raw institution folders.
- `bert/train.py`, `bert/model.py`: NF4 LoRA training and the numeric/text classifier.
- `bert/data.py`, `bert/hybrid_data.py`, `preprocessing/prepare.py`: raw-data preparation,
  identifier anonymization and saved numeric normalization.
- `bert/export_portable.py`: merge the trained adapter into the dequantized base,
  verify prediction parity, and export CPU-compatible weights.
- `bert/infer.py`: portable inference with the existing policy arbiter and explainer.
- `edge/train_edge.py`, `edge/signature.py`: train and score archetype novelty.
- `bert/checkpoint/`: selected small adapter/head, scaling metadata, training report.
- `tests/`: raw-input and policy-fusion regression tests.

## Reproduce training (developer environment)

Run from repository root with `PYTHONPATH=system`. The original training environment
was Apple MPS, with dependencies in `bert/requirements-training.txt`; this is not
required on the judge's host. Supply the external raw dataset and corrected label
file explicitly. Preserve the grouped train/validation/test split.

```sh
export PYTHONPATH=system
python -m fincrime.training.preprocessing.prepare --input /path/to/institutions --output /path/to/preprocessed
python -m fincrime.training.bert.data --source /path/to/preprocessed --labels /path/to/annotations.jsonl --output dist/bert-anonymized-v3
python -m fincrime.training.bert.hybrid_data
python -m fincrime.training.bert.download_parallel
python -m fincrime.training.bert.train --probe --output dist/modernbert-hybrid-probe
python -m fincrime.training.bert.train --output dist/modernbert-hybrid

# Export the bundled selected checkpoint (not a newly trained run):
python -m fincrime.training.bert.export_portable
```

Training uses the separately supplied labels. Inference uses neither labels nor
stored case IDs/splits; it accepts the original raw format and applies the saved
training statistics. The held-out test split was not loaded during the pilot.
See `bert/RESULTS.md` for provisional-label validation results and limitations.

Train a separate edge artifact without overwriting the production one:

```sh
PYTHONPATH=system python -m fincrime.training.edge.train_edge --input /path/to/institutions --output /path/to/new-edge-model.json
```

## Architecture

Raw folder → normalization/anonymization → ModernBERT text chunks + numeric MLP →
class probabilities. In parallel, the existing loader/rules and trained archetype
model supply proofs, flags and novelty. `bert.infer.fuse` adds model caution through
`fincrime.decide.arbitrate`; `fincrime.explain.explain` creates the grounded reason.
The final JSONL uses the standard schema. Optional diagnostics contain each model's
contribution. BERT and edge scores never create a CLOSE or override a red flag.

Ryaan's explainer PR is independent: this pipeline calls the shared arbiter and
explainer, so later evidence/reason improvements are inherited rather than copied.
The default submission outputs and rule implementation are untouched. Combined
accuracy must be evaluated against the same labels as the rules baseline; the BERT
pilot score alone does not prove an improvement over the newer production rules.
