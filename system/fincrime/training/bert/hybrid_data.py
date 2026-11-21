"""Separate real preprocessed numeric features from anonymized evidence text."""
import json,math,re,tempfile
from contextlib import contextmanager
from pathlib import Path
from fincrime.training.preprocessing.prepare import prepare
from fincrime.training.bert.data import rows, compact, digest, case_input, load_indices

def number(v):
 if v is None:return None
 if isinstance(v,(int,float)):return float(v)
 if isinstance(v,str) and re.fullmatch(r'-?\d+(?:\.\d+)?',v):return float(v)
 return None

def extract(r):
 c=json.loads(r['text']);f=c.pop('features');numeric={k:number(v) for k,v in f.items() if v is None or number(v) is not None}
 categories={k:v for k,v in f.items() if k not in numeric}
 # Preserve alerted and amount-matching historical transactions as grounding context.
 table=c.pop('transactions');txs=[dict(zip(table['columns'],row)) for row in table['rows']]
 linked={tid for a in c['alerts'] for tid in a.get('transaction_ids',[])}
 amounts={t.get('amount_gbp') for t in txs if t.get('transaction_id') in linked}
 c['transactions']=[t for t in txs if t.get('transaction_id') in linked or t.get('amount_gbp') in amounts]
 c['categorical_features']=categories
 # The complete transaction population remains represented by preprocessing aggregates.
 return {**r, 'text':compact(c)},numeric

def scale_features(f, keys, means, scales):
    values = []
    missing = []
    if not len(keys) == len(means) == len(scales):
        raise ValueError("Numeric scaling dimensions mismatch")
    for key, mean, std in zip(keys, means, scales):
        value = f.get(key)
        missing.append(float(value is None))
        if value is None:
            values.append(0.0)
        else:
            signed_log = math.copysign(math.log1p(abs(value)), value)
            values.append((signed_log - mean) / std)
    return values + missing

def _prepared_inputs(prepared, scaler):
    indices = load_indices(prepared)
    for bundle in rows(prepared / "case_bundles.jsonl"):
        row, _ = case_input(bundle, *indices)
        hybrid, numeric = extract(row)
        hybrid["numeric"] = scale_features(
            numeric, scaler["numeric_keys"], scaler["means"], scaler["scales"]
        )
        yield bundle["case"]["raw"]["case_id"], hybrid


@contextmanager
def model_inputs(folder, scaler):
    with tempfile.TemporaryDirectory(prefix="bert-preprocess-") as directory:
        prepared = Path(directory)
        prepare(folder, prepared)
        yield _prepared_inputs(prepared, scaler)


def main():
 source=Path('dist/bert-anonymized-v3');out=Path('dist/bert-hybrid-data');out.mkdir(parents=True,exist_ok=False)
 data={s:[extract(r) for r in rows(source/(s+'.jsonl'))] for s in ('train','validation')}
 keys=sorted({k for _,f in data['train'] for k in f});means=[];scales=[]
 for k in keys:
  values=[math.copysign(math.log1p(abs(f[k])),f[k]) for _,f in data['train'] if f.get(k) is not None]
  mean=sum(values)/len(values) if values else 0.;std=(sum((v-mean)**2 for v in values)/len(values))**.5 if values else 1.
  means.append(mean);scales.append(max(std,1e-6))
 for split,records in data.items():
  with (out/(split+'.jsonl')).open('w') as stream:
   for r,f in records:
    r['numeric']=scale_features(f,keys,means,scales);stream.write(compact(r)+'\n')
 manifest=dict(source_manifest_sha256=digest(source/'manifest.json'),numeric_keys=keys,means=means,scales=scales,numeric_dim=2*len(keys),fit_split='train',transform='signed log1p, train mean/std, plus missing indicators',text='Full documents, alerts, customer and review context; alerted and amount-matching transactions. All transaction populations represented by preprocessed numeric aggregates.',counts={s:len(rs) for s,rs in data.items()})
 (out/'manifest.json').write_text(json.dumps(manifest,indent=2));print(json.dumps(manifest['counts']),len(keys),'numeric features',flush=True)
if __name__=='__main__':main()
