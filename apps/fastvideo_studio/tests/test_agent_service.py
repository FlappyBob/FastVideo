# SPDX-License-Identifier: Apache-2.0
from copy import deepcopy

import pytest

from fastvideo_studio.agent_project import AgentProjectStore
from fastvideo_studio.agent_service import AgentService


def setup(tmp_path):
    store=AgentProjectStore(tmp_path/'studio.db')
    jobs={};created=[];queued=[]
    def create(job_id,payload):
        created.append(job_id)
        jobs[job_id]=dict(id=job_id,status='pending',**payload)
        return jobs[job_id]
    def enqueue(ids, **kwargs):
        queued.append(ids)
        for job_id in ids:jobs[job_id]['status']='queued'
    service=AgentService(store,create,jobs.get,enqueue,lambda:['FastVideo/FastVideo-FastH3-8-Step-V2'])
    project=store.create({'title':'Demo','shots':[{'id':str(i),'title':str(i),'prompt':'a lighthouse'} for i in range(3)]})
    return service,project,jobs,created,queued


def test_approval_and_budget_gate_all_submissions(tmp_path):
    service,p,jobs,created,queued=setup(tmp_path)
    with pytest.raises(ValueError,match='approve'):
        service.generate(p['id'],1,3)
    service.approve(p['id'],1)
    with pytest.raises(ValueError,match='budget'):
        service.generate(p['id'],1,2)
    assert created==[]
    first=service.generate(p['id'],1,3)
    second=service.generate(p['id'],1,3)
    assert first['selected']==second['selected']
    assert len(created)==3 and len(queued)==1
    assert all(job['num_inference_steps']==9 for job in jobs.values())


def test_interrupted_creation_resumes_reserved_ids_without_duplicates(tmp_path):
    service,p,jobs,created,queued=setup(tmp_path);service.approve(p['id'],1)
    original=service.create_job
    def fail_once(job_id,payload):
        result=original(job_id,payload)
        if len(created)==2:raise RuntimeError('simulated interruption after persistence')
        return result
    service.create_job=fail_once
    with pytest.raises(RuntimeError):service.generate(p['id'],1,3)
    assert queued==[]
    service.create_job=original
    result=service.generate(p['id'],1,3)
    assert len(created)==3 and len(set(created))==3
    assert len(result['selected'])==3 and len(queued)==1


def test_revisions_keep_unaffected_completed_takes(tmp_path):
    service,p,jobs,created,queued=setup(tmp_path);service.approve(p['id'],1)
    rendered=service.generate(p['id'],1,3)
    revised=deepcopy(rendered['blueprint']);revised['shots'][1]['prompt']='a bright lighthouse'
    with pytest.raises(ValueError,match='active'):service.update(p['id'],revised,1)
    for job in jobs.values():job['status']='completed'
    updated=service.update(p['id'],revised,1)
    service.approve(p['id'],updated['revision'])
    after=service.generate(p['id'],updated['revision'],1)
    assert len(created)==4
    assert after['selected']['0']==rendered['selected']['0']
    assert after['selected']['2']==rendered['selected']['2']
    assert after['selected']['1']!=rendered['selected']['1']


def test_planner_response_cannot_overwrite_newer_revision(tmp_path):
    service,p,jobs,created,queued=setup(tmp_path)
    def planner(current,instruction,target,**kwargs):
        changed=deepcopy(current);changed['style']='user edit'
        service.store.update(p['id'],changed,1)
        return current
    service.planner=planner
    with pytest.raises(ValueError,match='revision'):service.plan(p['id'],1,'make it dramatic',None)
    assert service.store.get(p['id'])['blueprint']['style']=='user edit'


def test_single_shot_planner_revision_preserves_other_shots(tmp_path):
    service,p,jobs,created,queued=setup(tmp_path)
    def planner(current,instruction,target,**kwargs):
        changed=deepcopy(current)
        for shot in changed['shots']:shot['prompt']='changed'
        changed['recipe']['num_gpus']=64
        return changed
    service.planner=planner
    result=service.plan(p['id'],1,'change second shot','1')
    assert [s['prompt'] for s in result['blueprint']['shots']]==['a lighthouse','changed','a lighthouse']
    assert result['blueprint']['recipe']['num_gpus']==1


def test_missing_planner_is_explicit_and_preserves_project(tmp_path):
    service,p,jobs,created,queued=setup(tmp_path)
    with pytest.raises(RuntimeError,match='configured'):service.plan(p['id'],1,'make film',None)
    assert service.store.get(p['id'])==p


def test_changed_reserved_job_cannot_run_under_old_approval(tmp_path):
    service,p,jobs,created,queued=setup(tmp_path);service.approve(p['id'],1)
    result=service.generate(p['id'],1,3)
    job_id=result['selected']['0']
    jobs[job_id].update(status='pending',prompt='unapproved prompt',num_gpus=64)
    with pytest.raises(ValueError,match='approved'):
        service.generate(p['id'],1,0)
    assert len(queued)==1


def test_rejected_take_requires_an_explicit_new_take(tmp_path):
    service,p,jobs,created,queued=setup(tmp_path);service.approve(p['id'],1)
    result=service.generate(p['id'],1,3)
    for job in jobs.values():job['status']='completed'
    service.review(p['id'],'0',result['selected']['0'],'rejected','wrong subject')
    with pytest.raises(ValueError,match='new take'):
        service.generate(p['id'],1,0)
    assert len(created)==3


def test_reference_integrity_is_checked_again_before_submission(tmp_path):
    import hashlib
    from PIL import Image
    from fastvideo_studio.agent_project import FULL_H3
    service,p,jobs,created,queued=setup(tmp_path)
    path=tmp_path/'reference.png';Image.new('RGB',(8,8),'red').save(path)
    service.store.add_asset(p['id'],{'id':'ref','path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    payload=deepcopy(p['blueprint']);payload['recipe']['model_id']=FULL_H3;payload['shots'][0]['asset_ids']=['ref']
    service.model_ids=lambda:[FULL_H3]
    service.update(p['id'],payload,1);service.approve(p['id'],2)
    path.write_bytes(b'changed')
    with pytest.raises(ValueError,match='reference|Reference'):
        service.generate(p['id'],2,3)
    assert created==[]


def test_display_title_change_reuses_approved_visual_inputs(tmp_path):
    service,p,jobs,created,queued=setup(tmp_path);service.approve(p['id'],1)
    rendered=service.generate(p['id'],1,3)
    for job in jobs.values():job['status']='completed'
    payload=deepcopy(rendered['blueprint']);payload['title']='Renamed film';payload['shots'][0]['title']='New label'
    service.update(p['id'],payload,1);service.approve(p['id'],2)
    restored=service.generate(p['id'],2,0)
    assert len(created)==3
    assert restored['selected']==rendered['selected']
