"""Portable CPU inference: raw institution folder -> policy-grounded hybrid decisions."""
import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path



def read_json(path):
    return json.loads(Path(path).read_text())


def fuse(case, pol, rules, signal, probabilities, incomplete=''):
    """Learned models can add caution, never authorize a CLOSE or remove a flag."""
    from fincrime.decide import arbitrate, safe_escalate
    from fincrime.model import EdgeSignal
    labels = ['CLOSE', 'RFI', 'ESCALATE']
    if len(probabilities) != 3 or not all(math.isfinite(p) and 0 <= p <= 1 for p in probabilities):
        raise ValueError('Invalid classifier probabilities')
    if abs(sum(probabilities) - 1) > 1e-4:
        raise ValueError('Classifier probabilities must sum to one')
    baseline = arbitrate(case, pol, rules, EdgeSignal())
    bert = labels[max(range(3), key=lambda i: probabilities[i])]
    combined = signal
    if baseline.verdict == 'CLOSE' and bert != 'CLOSE':
        combined = EdgeSignal(max(signal.score, 1-probabilities[0]), True,
                              signal.suspicious or bert == 'ESCALATE',
                              list(signal.features) + ['bert_disagreement'])
    decision = arbitrate(case, pol, rules, combined)
    if decision.verdict == 'CLOSE' and incomplete:
        decision = safe_escalate(case.raw, incomplete)
    diagnostics = dict(rules_verdict=baseline.verdict, bert_verdict=bert,
                       probabilities=dict(zip(labels, probabilities)),
                       edge_score=signal.score, edge_unusual=signal.unusual,
                       edge_suspicious=signal.suspicious, edge_features=signal.features,
                       final_verdict=decision.verdict, incomplete=incomplete)
    return decision, diagnostics


class Predictor:
    def __init__(self, model_dir, threads=4):
        import torch
        from transformers import AutoModel, AutoTokenizer
        from fincrime.training.bert.model import CaseClassifier
        self.torch = torch
        torch.set_num_threads(threads)
        self.root = Path(model_dir)
        checks = read_json(self.root / 'checksums.json')
        for name, expected in checks.items():
            path = (self.root / name).resolve()
            if not path.is_relative_to(self.root.resolve()):
                raise ValueError('Invalid checkpoint manifest path')
            with path.open('rb') as stream:
                actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            if actual != expected:
                raise ValueError('Checkpoint checksum mismatch: ' + name)
        self.scaler = read_json(self.root / 'preprocessing.json')
        self.manifest = read_json(self.root / 'training.json')
        if read_json(self.root / 'labels.json') != ['CLOSE', 'RFI', 'ESCALATE']:
            raise ValueError('Checkpoint class order mismatch')
        self.tokenizer = AutoTokenizer.from_pretrained(self.root / 'encoder', local_files_only=True)
        encoder = AutoModel.from_pretrained(self.root / 'encoder', local_files_only=True,
                                           dtype=torch.float32, attn_implementation='sdpa')
        self.model = CaseClassifier(encoder, self.scaler['numeric_dim']).eval()
        state = torch.load(self.root / 'classifier.pt', map_location='cpu', weights_only=True)
        for name in ('pool_gate', 'numeric_net', 'head'):
            getattr(self.model, name).load_state_dict(state[name])

    def predict(self, row):
        torch = self.torch
        numeric = row['numeric']
        if len(numeric) != self.scaler['numeric_dim'] or not all(math.isfinite(x) for x in numeric):
            raise ValueError('Invalid numeric feature vector')
        tok = self.tokenizer
        ids = tok.encode(row['text'], add_special_tokens=False)
        payload = self.manifest['chunk_size'] - 2
        stride = payload - self.manifest['chunk_overlap']
        chunks = []
        for start in range(0, max(1, len(ids)), stride):
            chunks.append([tok.cls_token_id] + ids[start:start+payload] + [tok.sep_token_id])
            if start + payload >= len(ids):
                break
        length = max(map(len, chunks))
        x = torch.full((len(chunks), length), tok.pad_token_id, dtype=torch.long)
        mask = torch.zeros_like(x)
        for i, chunk in enumerate(chunks):
            x[i, :len(chunk)] = torch.tensor(chunk)
            mask[i, :len(chunk)] = 1
        with torch.inference_mode():
            logits = self.model(x, mask, [len(chunks)], 1, torch.tensor([numeric], dtype=torch.float32))
            return logits.softmax(-1)[0].tolist()


def run_folder(folder, output, diagnostics, predictor, limit=None):
    from fincrime.training.bert.hybrid_data import model_inputs
    from fincrime.training.edge import signature
    from fincrime import policy, edge
    from fincrime.loader import load
    from fincrime.main import _Tolerant
    from fincrime.decide import run_rules
    from fincrime.explain import explain
    from fincrime.reasons import render
    os.environ['FINCRIME_LLM'] = '0'
    os.environ['FINCRIME_LLM_REASONS'] = '0'
    with _Tolerant(str(folder)) as quality:
        institution, cases = load(str(folder))
    by_id = {c.case_id: c for c in cases}
    pol = policy.get(institution)
    edge_model = read_json(edge.MODEL_PATH)
    if not edge_model.get('archetypes'):
        raise ValueError('Trained archetype model is missing')
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or (diagnostics and Path(diagnostics).exists()):
        raise ValueError('Refusing to overwrite existing predictions')
    count = 0
    with tempfile.TemporaryDirectory(dir=output.parent) as temp:
        pending = Path(temp)/'predictions.jsonl'
        audit = Path(temp)/'diagnostics.jsonl'
        with pending.open('w') as stream, audit.open('w') as trace:
            with model_inputs(Path(folder), predictor.scaler) as rows:
                for case_id, row in rows:
                    case = by_id.pop(case_id)
                    probabilities = predictor.predict(row)
                    rules = run_rules(case, pol)
                    signal = signature.edge_signal(case, rules, edge_model, 0.5)
                    decision, details = fuse(case, pol, rules, signal, probabilities, quality.incomplete(case))
                    try:
                        decision.reason = explain(case, decision)
                    except Exception:
                        decision.reason = render(case, decision.verdict, decision.reason_code, decision.facts)
                    print(json.dumps(decision.to_json(), ensure_ascii=False, allow_nan=False), file=stream)
                    print(json.dumps(dict(case_id=case_id, **details), allow_nan=False), file=trace)
                    count += 1
                    if count % 10 == 0:
                        print(f'{count} cases classified', file=sys.stderr, flush=True)
                    if limit and count >= limit:
                        break
        if by_id and not limit:
            raise ValueError(f'Missing prepared inputs for {len(by_id)} cases')
        if diagnostics:
            dest = Path(diagnostics)
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Copy via exclusive create: diagnostic path may be on a separate filesystem.
            with dest.open('x') as f:
                f.write(audit.read_text())
        os.link(pending, output)
    return count


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--model', default=os.environ.get('FINCRIME_MODEL', 'dist/portable-hybrid'))
    p.add_argument('--diagnostics', type=Path)
    p.add_argument('--threads', type=int, default=4)
    p.add_argument('--limit', type=int, help='Smoke testing only; omitted processes every case')
    a = p.parse_args()
    if a.threads < 1 or (a.limit is not None and a.limit < 1):
        p.error('threads and limit must be positive')
    predictor = Predictor(a.model, a.threads)
    count = run_folder(a.input, a.output, a.diagnostics, predictor, a.limit)
    print(f'Wrote {count} decisions to {a.output}', file=sys.stderr)

if __name__ == '__main__':
    main()
