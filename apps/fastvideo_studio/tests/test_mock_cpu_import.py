# SPDX-License-Identifier: Apache-2.0
"""Browser mock must remain usable without importing a GPU inference runtime."""
import subprocess
import sys
from pathlib import Path


def test_mock_server_imports_without_torch_triton_or_fastvideo():
    apps = Path(__file__).resolve().parents[2]
    code = f'''import sys, importlib.abc
sys.path.insert(0, {str(apps)!r})
class BlockGPU(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {{'torch','triton','fastvideo'}}:
            raise ImportError('GPU runtime must not be imported: '+fullname)
sys.meta_path.insert(0,BlockGPU())
import fastvideo_studio.mock_server as mock
assert mock.get_inference_options('FastVideo/FastVideo-FastH3-8-Step-V2')['auto_attention_backend']=='VIDEO_SPARSE_ATTN_H3'
'''
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
