"""One-use, checksummed source transport for this explicitly scoped branch."""
from pathlib import Path
import base64
import hashlib
import lzma
import os
import subprocess

BRANCH = 'stage5a-fixed10-bounded-pressure'
OLD_SHA = '7c2cc8a1e3b6d8caaac51715624a5d7a85624e46'
EXPECTED_TREE = 'fd1376748804dcf1e7632a5f135869469b299e37'
if os.environ.get('GITHUB_REF') != 'refs/heads/' + BRANCH:
    raise SystemExit('wrong write target')
subprocess.run(['git', 'merge-base', '--is-ancestor', OLD_SHA, 'HEAD'], check=True)
parts = [Path(f'tools/_stage5a_patch_{i}.txt') for i in range(5)]
packed = base64.b64decode(''.join(p.read_text(encoding='utf-8') for p in parts), validate=True)
if hashlib.sha256(packed).hexdigest() != '773c40cc5dedc304a735606ed19fd7e2e3a589ccacf5c1a6499ea2ba4661511f':
    raise SystemExit('source transport checksum mismatch')
patch = lzma.decompress(packed)
subprocess.run(['git', 'apply', '--check', '--index', '-'], input=patch, check=True)
subprocess.run(['git', 'apply', '--index', '-'], input=patch, check=True)
transport_paths = [str(p) for p in parts] + ['tools/_apply_stage5a_v2.py', '.github/workflows/bootstrap_stage5a_v2.yml']
subprocess.run(['git', 'rm', '--', *transport_paths], check=True)
subprocess.run(['git', 'diff', '--cached', '--check'], check=True)
tree = subprocess.check_output(['git', 'write-tree'], text=True).strip()
if tree != EXPECTED_TREE:
    raise SystemExit(f'unexpected candidate source tree {tree}')
print('Exact locally tested source tree staged:', tree)
