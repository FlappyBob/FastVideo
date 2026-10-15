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
