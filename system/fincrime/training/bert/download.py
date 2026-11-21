import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from urllib.request import urlopen,urlretrieve
repo='answerdotai/ModernBERT-base'
meta=json.load(urlopen('https://huggingface.co/api/models/'+repo));revision=meta['sha']
out=Path('dist/models/modernbert-base');out.mkdir(parents=True,exist_ok=True)
names=['config.json','model.safetensors','tokenizer.json','tokenizer_config.json','special_tokens_map.json']
def fetch(name):
 urlretrieve(f'https://huggingface.co/{repo}/resolve/{revision}/{name}',out/(name+'.partial'))
 (out/(name+'.partial')).rename(out/name);print(name,flush=True)
with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(fetch,names))
(out/'source.json').write_text(json.dumps(dict(repo=repo,revision=revision),indent=2));print(revision,flush=True)
