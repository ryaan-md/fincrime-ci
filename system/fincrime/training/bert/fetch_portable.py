"""Download and verify the CPU model bundle using Python's standard library."""
import argparse
import hashlib
from pathlib import Path
import tarfile
import tempfile
import urllib.request

URL = 'https://github.com/ryaan-md/fincrime-ci/releases/download/hybrid-cpu-2026-11-21/modernbert-hybrid-cpu.tar.gz'
SHA256 = '352be8442edfaa1ea57da2a3e8af887b5f61dd5af9750ecb20e2cb33275ab8dd'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive', type=Path, help='Use an already downloaded archive, e.g. for private repositories')
    a = p.parse_args()
    if Path('dist/portable-hybrid').exists():
        p.error('dist/portable-hybrid already exists; refusing to overwrite')
    with tempfile.TemporaryDirectory() as temp:
        archive = a.archive or Path(temp)/'model.tar.gz'
        if not a.archive:
            print('Downloading portable CPU model...', flush=True)
            with urllib.request.urlopen(URL, timeout=120) as response, archive.open('wb') as out:
                while chunk := response.read(1024*1024):
                    out.write(chunk)
        h = hashlib.sha256()
        with archive.open('rb') as stream:
            while chunk := stream.read(1024*1024):
                h.update(chunk)
        if h.hexdigest() != SHA256:
            raise ValueError('Model archive checksum mismatch')
        with tarfile.open(archive) as tar:
            for entry in tar.getmembers():
                if not (entry.name == 'dist/portable-hybrid' or entry.name.startswith('dist/portable-hybrid/')):
                    raise ValueError('Unexpected archive path')
                if '..' in Path(entry.name).parts or not (entry.isfile() or entry.isdir()):
                    raise ValueError('Unsafe archive entry')
            tar.extractall('.', filter='data')
    print('Verified model ready at dist/portable-hybrid')

if __name__ == '__main__':
    main()
