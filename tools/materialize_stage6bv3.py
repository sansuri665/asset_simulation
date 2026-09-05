"""One-use publication helper: verify exact payload and allowlisted text files."""
from pathlib import Path
import base64
import hashlib
import json
import lzma
import os
import subprocess

BASE = '6f6aaa24e44784ff3eb0538e488636cff5f7d88b'
BRANCH = 'stage6b-v3-transparent-market'
RAW_SHA256 = 'e3ff95d34f3105b8750c5a1334e8586fb17e0a2e281a8c185121637b9e08a599'
ALLOWED = {
    'asset_simulation/CLAUDE.md',
    'asset_simulation/audit_stage6b_v3.py',
    'asset_simulation/config/stage6b_market_v0.3.json',
    'asset_simulation/contracts/stage6b_market_v3.json',
    'asset_simulation/docs/INDEX.md',
    'asset_simulation/docs/MODEL_CONTEXT_GUIDE.md',
    'asset_simulation/docs/current/PROJECT_STATUS.md',
    'asset_simulation/docs/current/STAGE6B_V3_TRANSPARENT_MARKET.md',
    'asset_simulation/model/shipping_v3/__init__.py',
    'asset_simulation/model/shipping_v3/availability.py',
    'asset_simulation/model/shipping_v3/checkpoint.py',
    'asset_simulation/model/shipping_v3/diagnostics.py',
    'asset_simulation/model/shipping_v3/engine.py',
    'asset_simulation/model/shipping_v3/policies.py',
    'asset_simulation/model/shipping_v3/pricing.py',
    'asset_simulation/model/shipping_v3/types.py',
    'asset_simulation/tests/test_shipping_market_v3.py',
}
assert os.environ.get('GITHUB_REPOSITORY') == 'sansuri665/asset_simulation'
assert os.environ.get('GITHUB_REF') == f'refs/heads/{BRANCH}'
subprocess.run(['git', 'merge-base', '--is-ancestor', BASE, 'HEAD'], check=True)
encoded = ''.join(Path(f'tools/stage6bv3_payload_{i}.b64').read_text().strip() for i in range(4))
raw = lzma.decompress(base64.b64decode(encoded, validate=True))
assert hashlib.sha256(raw).hexdigest() == RAW_SHA256, 'source payload checksum mismatch'
data = json.loads(raw)
assert data['base_commit'] == BASE and data['branch'] == BRANCH
assert len(data['files']) == len(ALLOWED)
assert {row['path'] for row in data['files']} == ALLOWED
for row in data['files']:
    content = row['content'].encode('utf-8')
    assert hashlib.sha256(content).hexdigest() == row['sha256'], row['path']
    path = Path(row['path'])
    old = subprocess.run(['git', 'show', f'{BASE}:{row["path"]}'], capture_output=True)
    if old.returncode == 0:
        assert path.read_bytes() == old.stdout, f'concurrent source change: {path}'
    else:
        assert not path.exists(), f'new source already exists: {path}'
for row in data['files']:
    path = Path(row['path']); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(row['content'].encode('utf-8'))
print(f'Verified and reconstructed {len(ALLOWED)} source files; main and legacy source untouched.')
