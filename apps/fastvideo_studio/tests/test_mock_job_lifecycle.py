# SPDX-License-Identifier: Apache-2.0
"""The browser mock must support the edit/duplicate lifecycle it advertises."""
import pytest
from fastapi.testclient import TestClient
from fastvideo_studio import mock_server


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(mock_server, '_jobs', {})
    monkeypatch.setattr(mock_server, '_settings', {**mock_server._settings, 'autoStartJob': False})
    return TestClient(mock_server.app)


def test_mock_edit_duplicate_and_stop_preserve_config(client):
    payload = {'model_id': 'model', 'prompt': 'before', 'name': 'test',
               'attention_backend': 'TORCH_SDPA', 'vsa_tile_size': 64,
               'text_encoder_cpu_offload': True, 'references': [{'source': '/tmp/image.png'}]}
    created = client.post('/api/jobs', json=payload)
    assert created.status_code == 201
    job = created.json()
    edited = client.patch('/api/jobs/' + job['id'], json={'prompt': 'after', 'vsa_sparsity': 0.8})
    assert edited.status_code == 200
    copied = client.post('/api/jobs/' + job['id'] + '/duplicate')
    assert copied.status_code == 201
    copy = copied.json()
    assert copy['id'] != job['id'] and copy['status'] == 'pending'
    assert copy['prompt'] == 'after'
    assert copy['attention_backend'] == 'TORCH_SDPA' and copy['vsa_tile_size'] == 64
    assert copy['text_encoder_cpu_offload'] is True
    assert copy['references'] == payload['references']
    assert copy['vsa_sparsity'] == 0.8
    assert client.post('/api/jobs/' + copy['id'] + '/start').json()['status'] == 'running'
    assert client.patch('/api/jobs/' + copy['id'], json={'prompt': 'invalid'}).status_code == 400
    assert client.post('/api/jobs/' + copy['id'] + '/stop').json()['status'] == 'stopped'
    assert client.delete('/api/jobs/' + copy['id']).status_code == 200


def test_mock_rejects_invalid_edits_without_mutation(client):
    job = client.post('/api/jobs', json={'model_id': 'model', 'prompt': 'before'}).json()
    for change in ({'status': 'completed'}, {'vsa_tile_size': -1}, {'vsa_tile_size': True}, {'attention_backend': 'typo'}):
        assert client.patch('/api/jobs/' + job['id'], json=change).status_code == 400
    assert client.get('/api/jobs/' + job['id']).json()['prompt'] == 'before'



def test_mock_backend_names_match_core_catalogue():
    from fastvideo.platforms import AttentionBackendEnum
    assert set(mock_server._MOCK_ATTENTION_BACKENDS) == {backend.name for backend in AttentionBackendEnum}
