# SPDX-License-Identifier: Apache-2.0
"""Fixed attention catalogue for the synthetic, GPU-independent browser mock.

This is not runtime capability detection. The real server continues to use the
core backend enum and compatibility checks; mock options describe test inputs.
"""
from types import SimpleNamespace

from .inference_options import _recipes, resolve_attention_defaults

_MOCK_BACKENDS = ('FLASH_ATTN', 'TORCH_SDPA', 'SAGE_ATTN', 'SAGE_ATTN_THREE', 'ATTN_QAT_INFER', 'ATTN_QAT_TRAIN',
                  'VIDEO_SPARSE_ATTN', 'VIDEO_SPARSE_ATTN_H3', 'BSA_ATTN', 'VMOBA_ATTN', 'SLA_ATTN', 'SAGE_SLA_ATTN',
                  'NABLA_ATTN', 'NO_ATTENTION')


def coerce_attn_backend(value):
    if value is None:
        return None
    if not isinstance(value, str) or value.strip().upper() not in _MOCK_BACKENDS:
        raise ValueError('Unknown attention backend in mock catalogue')
    return SimpleNamespace(name=value.strip().upper())


def inference_options(model_id: str):
    backend, tile = resolve_attention_defaults(model_id, 't2v', None, None)
    return dict(attention_backends=list(_MOCK_BACKENDS),
                **(_recipes().get(model_id) or dict(defaults={}, source=None)),
                auto_attention_backend=backend,
                auto_vsa_tile_size=tile)
