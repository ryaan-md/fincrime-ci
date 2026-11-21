"""Start immediately once the pinned weights and anonymized snapshot are ready."""
import subprocess,sys,time
from pathlib import Path
required=[Path('dist/models/modernbert-base/source.json'),Path('dist/bert-hybrid-data/manifest.json')]
while not all(p.exists() for p in required):time.sleep(5)
print('Inputs ready; running GPU preflight.',flush=True)
subprocess.run([sys.executable,'-u','-m','fincrime.training.bert.train','--probe','--output','dist/modernbert-hybrid-probe'],check=True)
print('Preflight passed; starting training.',flush=True)
subprocess.run([sys.executable,'-u','-m','fincrime.training.bert.train','--output','dist/modernbert-hybrid'],check=True)
