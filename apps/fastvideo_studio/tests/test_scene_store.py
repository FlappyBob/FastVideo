# SPDX-License-Identifier: Apache-2.0
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.fastvideo_studio.scene_routes import build_scene_router
from apps.fastvideo_studio.scene_store import SceneStore


def jobs():
    return {
        'a': dict(id='a', name='夜谈-clip-01', status='completed', created_at=1),
        'b': dict(id='b', name='夜谈-clip-01', status='completed', created_at=2),
        'c': dict(id='c', name='夜谈-clip-02', status='pending', created_at=3),
    }


def test_import_is_explicit_idempotent_and_survives_renames(tmp_path):
    path = tmp_path / 'studio.db'
    store = SceneStore(path)
    source = jobs()
    assert store.list_scenes() == []
    imported = store.import_legacy(source)
    assert len(imported) == 1
    assert imported[0]['clips'][0]['job_ids'] == ['a', 'b']
    assert imported[0]['clips'][0]['selected_job_id'] == 'b'
    assert store.import_legacy(source) == imported
    source['a']['name'] = 'completely unrelated'
    assert SceneStore(path).import_legacy(source) == imported


def test_selection_name_and_clip_order_survive_restart_and_new_takes(tmp_path):
    path = tmp_path / 'studio.db'
    store = SceneStore(path)
    source = jobs()
    scene = store.import_legacy(source)[0]
    first, second = scene['clips']
    first['selected_job_id'] = 'a'
    first['name'] = 'Opening conversation'
    scene['name'] = 'Conversation'
    scene['clips'] = [second, first]
    saved = store.save(scene, source, scene['id'])
    assert SceneStore(path).list_scenes() == [saved]
    source['d'] = dict(id='d', name='夜谈-clip-01', status='completed', created_at=4)
    updated = SceneStore(path).import_legacy(source)[0]
    assert [clip['id'] for clip in updated['clips']] == [second['id'], first['id']]
    assert updated['clips'][1]['selected_job_id'] == 'a'
    assert updated['clips'][1]['name'] == 'Opening conversation'
    assert updated['clips'][1]['job_ids'] == ['a', 'b', 'd']


@pytest.mark.parametrize('change', ['missing_job', 'foreign_clip', 'foreign_take', 'duplicate_clip', 'duplicate_job'])
def test_invalid_update_has_no_partial_mutation(tmp_path, change):
    store = SceneStore(tmp_path / 'studio.db')
    source = jobs()
    scene = store.import_legacy(source)[0]
    before = store.list_scenes()
    scene['name'] = 'Must not change'
    if change == 'missing_job':
        scene['clips'][1]['job_ids'].append('missing')
    elif change == 'foreign_clip':
        scene['clips'][1]['id'] = 'foreign'
    elif change == 'foreign_take':
        scene['clips'][1]['selected_job_id'] = 'a'
    elif change == 'duplicate_clip':
        scene['clips'][1]['id'] = scene['clips'][0]['id']
    else:
        scene['clips'][1]['job_ids'].append('a')
    with pytest.raises(ValueError):
        store.save(scene, source, scene['id'])
    assert store.list_scenes() == before


def test_routes_create_import_update_and_validation(tmp_path):
    app = FastAPI()
    database = SimpleNamespace(_path=tmp_path / 'studio.db')
    app.include_router(build_scene_router(lambda: database, lambda: jobs().values()))
    with TestClient(app) as client:
        assert client.get('/api/scenes').json() == []
        scene = client.post('/api/scenes', json={'name': 'Empty'}).json()
        assert scene['clips'] == []
        scene['clips'] = [dict(name='Opening', job_ids=['a', 'b'], selected_job_id='a')]
        response = client.put(f"/api/scenes/{scene['id']}", json=scene)
        assert response.status_code == 200
        saved = response.json()
        assert saved['clips'][0]['selected_job_id'] == 'a'
        assert client.put('/api/scenes/missing', json={'name': 'Missing'}).status_code == 404
        assert client.post('/api/scenes', json={'name': 'Bad', 'clips': [dict(name='Bad', job_ids=['missing'])]}).status_code == 422
        imported = client.post('/api/scenes/import-legacy').json()
        assert len(imported) == 2
        assert client.get('/api/scenes').json() == imported
        assert client.post('/api/scenes/import-legacy').json() == imported
