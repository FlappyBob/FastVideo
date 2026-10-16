# SPDX-License-Identifier: Apache-2.0
from io import BytesIO

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from fastvideo_studio.agent_project import AgentProjectStore, V2
from fastvideo_studio.agent_service import AgentService
from fastvideo_studio.agent_routes import build_agent_router


def client(tmp_path):
    jobs={}
    def create(job_id,payload):
        jobs[job_id]=dict(id=job_id,status='pending',**payload)
        return jobs[job_id]
    def enqueue(ids, **kwargs):
        for job_id in ids:jobs[job_id]['status']='queued'
    service=AgentService(AgentProjectStore(tmp_path/'db.sqlite'),create,jobs.get,enqueue,lambda:[V2])
    app=FastAPI();app.include_router(build_agent_router(lambda:service,tmp_path/'assets',mock=True))
    return TestClient(app),service,jobs


def test_http_story_approval_generate_review_and_export(tmp_path):
    api,service,jobs=client(tmp_path)
    project=api.post('/api/agent/projects',json={'title':'demo','shots':[{'id':'a','title':'one','prompt':'lighthouse'}]}).json()
    prefix='/api/agent/projects/'+project['id']
    assert api.post(prefix+'/generate',json={'revision':1,'max_new_jobs':1}).status_code==422
    assert api.post(prefix+'/approve',json={'revision':1}).status_code==200
    assert api.post(prefix+'/generate',json={'revision':1,'max_new_jobs':1}).status_code==200
    assert len(jobs)==1
    job_id=next(iter(jobs));jobs[job_id]['status']='completed'
    assert api.get(prefix+'/export-jobs').status_code==422
    assert api.post(prefix+'/review',json={'shot_id':'a','job_id':job_id,'verdict':'accepted'}).status_code==200
    assert api.get(prefix+'/export-jobs').json()['job_ids']==[job_id]
    assert api.get('/api/agent/config').json()['mock'] is True
    assert api.post(prefix+'/plan',json={'revision':1,'instruction':'improve story'}).status_code==503


def test_assets_are_validated_and_scoped_to_project(tmp_path):
    api,service,jobs=client(tmp_path)
    one=api.post('/api/agent/projects',json={'title':'one'}).json()['id']
    two=api.post('/api/agent/projects',json={'title':'two'}).json()['id']
    data=BytesIO();Image.new('RGB',(32,32),'red').save(data,format='PNG')
    response=api.post(f'/api/agent/projects/{one}/assets',files={'file':('photo.png',data.getvalue(),'image/png')})
    assert response.status_code==201
    asset=response.json()
    assert 'path' not in asset
    assert api.get(f'/api/agent/projects/{one}/assets/{asset["id"]}').status_code==200
    assert api.get(f'/api/agent/projects/{two}/assets/{asset["id"]}').status_code==404
    assert api.post(f'/api/agent/projects/{one}/assets',files={'file':('bad.png',b'broken','image/png')}).status_code==422


def test_generated_reference_attaches_only_after_revision_check(tmp_path):
    api,service,jobs=client(tmp_path)
    project=api.post('/api/agent/projects',json={'title':'robot story','characters':[
        {'id':'robot','name':'Robot','description':'yellow robot'}]}).json()
    data=BytesIO();Image.new('RGB',(32,32),'yellow').save(data,format='PNG')
    service.image_generator=lambda prompt:data.getvalue()
    prefix=f'/api/agent/projects/{project["id"]}'
    response=api.post(prefix+'/references/generate',json={'revision':1,'character_id':'robot'})
    assert response.status_code==200
    updated=response.json()
    assert updated['revision']==2
    assert len(updated['blueprint']['characters'][0]['asset_ids'])==1
    assert len(service.store.assets(project['id']))==1
    assert api.post(prefix+'/references/generate',json={'revision':1,'character_id':'robot'}).status_code==409


def test_http_takes_soundtrack_and_cut_render(tmp_path):
    import numpy as np
    from fastvideo_studio.merge import find_ffmpeg
    from fastvideo_studio.tests.test_merge import write_clip
    from fastvideo_studio.tests.test_edit_render import write_tone
    if find_ffmpeg() is None:
        import pytest
        pytest.skip('needs an ffmpeg binary')
    jobs={}
    def create(job_id,payload):
        jobs[job_id]=dict(id=job_id,status='pending',**payload)
        return jobs[job_id]
    def enqueue(ids, **kwargs):
        for job_id in ids:
            path=tmp_path/f'{job_id}.mp4';write_clip(str(path),frames=48)
            jobs[job_id].update(status='completed',output_path=str(path))
    service=AgentService(AgentProjectStore(tmp_path/'db.sqlite'),create,jobs.get,enqueue,lambda:[V2],
                         cache_dir=str(tmp_path/'obs'),output_dir=str(tmp_path/'out'))
    app=FastAPI();app.include_router(build_agent_router(lambda:service,tmp_path/'assets',mock=True))
    api=TestClient(app)
    assert api.get('/api/agent/config').json()['tiers']==['preview','final']
    project=api.post('/api/agent/projects',json={'title':'demo','shots':[{'id':'a','title':'one','prompt':'x'}]}).json()
    prefix='/api/agent/projects/'+project['id']
    music=tmp_path/'m.m4a';write_tone(music,seconds=2)
    assert api.post(prefix+'/soundtrack',files={'file':('m.txt',b'nope','text/plain')}).status_code==422
    uploaded=api.post(prefix+'/soundtrack',files={'file':('m.m4a',music.read_bytes(),'audio/mp4')})
    assert uploaded.status_code==201 and uploaded.json()['kind']=='audio' and 'path' not in uploaded.json()
    api.post(prefix+'/approve',json={'revision':1});api.post(prefix+'/generate',json={'revision':1,'max_new_jobs':1})
    job_id=next(iter(jobs))
    take=api.get(f'{prefix}/takes/a/{job_id}')
    assert take.status_code==200 and take.json()['keyframes_url'].endswith('/keyframes') and 'keyframes_path' not in take.json()
    assert api.get(f'{prefix}/takes/a/{job_id}/keyframes').headers['content-type']=='image/jpeg'
    assert api.get(f'{prefix}/takes/a/missing').status_code==404
    assert api.post(prefix+'/cut/render').status_code==422
    api.post(prefix+'/review',json={'shot_id':'a','job_id':job_id,'verdict':'accepted'})
    current=api.get(prefix).json()
    plan=current['blueprint'];plan['soundtrack']['asset_id']=uploaded.json()['id'];plan['shots'][0]['edit']['caption']='Hi'
    saved=api.put(prefix,json={'revision':current['revision'],'blueprint':plan})
    assert saved.status_code==200 and saved.json()['approved_revision']==saved.json()['revision']
    rendered=api.post(prefix+'/cut/render')
    assert rendered.status_code==200, rendered.text
    body=rendered.json();assert body['clips']==1 and 'path' not in body
    film=api.get(body['url']);assert film.status_code==200 and film.headers['content-type']=='video/mp4'
    assert api.get(prefix+'/renders/../x.mp4').status_code in (404,422)
    assert api.get(prefix+'/timing').json()['operations']['render']>=0
    assert api.post(prefix+'/cut/propose',json={'revision':saved.json()['revision'],'instruction':'tight'}).status_code==503
    assert len(np.zeros(1))==1
