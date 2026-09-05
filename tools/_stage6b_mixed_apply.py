"""One-use source installer; exact checksums, no workflows or main writes."""
import base64
import hashlib
import io
import json
import lzma
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = 'c8971c2d122cb7d57a565419d5be3462c7687ac539607aaf58e952dafb889cd1'
BASE = '1d9f824ad6c099e7671446698f2fc5053054f38c'
subprocess.run(['git', 'merge-base', '--is-ancestor', BASE, 'HEAD'], cwd=ROOT, check=True)
payload = ''.join((ROOT / f'tools/_stage6b_mixed_{i}.txt').read_text() for i in range(3))
compressed = base64.b64decode(payload, validate=True)
if hashlib.sha256(compressed).hexdigest() != EXPECTED:
    raise ValueError('Source transport checksum mismatch')
bundle = json.loads(lzma.decompress(compressed))
manifest = bundle['manifest']
if manifest['base_commit'] != BASE or set(manifest['files']) != set(bundle['operations']):
    raise ValueError('Source manifest mismatch')
for name, meta in manifest['files'].items():
    p = Path(name)
    if p.is_absolute() or '..' in p.parts or not name.startswith('asset_simulation/'):
        raise ValueError('Unexpected source destination')
    dest = ROOT / p
    if meta['old_blob'] is None:
        if dest.exists():
            raise ValueError(f'New source path already exists: {name}')
        old = ''
    else:
        actual = subprocess.check_output(['git', 'hash-object', name], cwd=ROOT, text=True).strip()
        if actual != meta['old_blob']:
            raise ValueError(f'Existing source changed: {name}')
        old = dest.read_text(encoding='utf-8')
    op = bundle['operations'][name]
    if set(op) == {'append'}:
        text = old + op['append']
    elif set(op) == {'prepend'}:
        text = op['prepend'] + old
    elif set(op) == {'replace'}:
        text = op['replace']
    else:
        raise ValueError('Unknown source operation')
    if hashlib.sha256(text.encode('utf-8')).hexdigest() != meta['sha256']:
        raise ValueError(f'Reconstructed source checksum mismatch: {name}')
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding='utf-8')
subprocess.run(['git', 'add', '--', *manifest['files']], cwd=ROOT, check=True)
subprocess.run(['git', 'rm', '--', 'tools/_stage6b_mixed_apply.py', *[f'tools/_stage6b_mixed_{i}.txt' for i in range(3)]], cwd=ROOT, check=True)
print('Verified source files:', len(manifest['files']))
