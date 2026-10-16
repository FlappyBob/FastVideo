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


# --- harness: tiers, observation, editorial revisions, cut rendering -------------------------------------------
from fastvideo_studio.merge import find_ffmpeg
from fastvideo_studio.tests.test_merge import write_clip


def media_setup(tmp_path, planner=None):
    """Jobs complete instantly with a real synthetic clip, so observation and rendering are exercised."""
    store = AgentProjectStore(tmp_path / 'studio.db')
    jobs = {}
    clock = [100.0]

    def create(job_id, payload):
        jobs[job_id] = dict(id=job_id, status='pending', queued_at=None, started_at=None, finished_at=None, **payload)
        return jobs[job_id]

    def enqueue(ids, **kwargs):
        for index, job_id in enumerate(ids):
            path = tmp_path / f'{job_id}.mp4'
            write_clip(str(path), frames=48, color=['red', 'green', 'blue'][index % 3])
            jobs[job_id].update(status='completed', output_path=str(path), queued_at=10.0, started_at=12.0,
                                finished_at=20.0)

    service = AgentService(store, create, jobs.get, enqueue, lambda: ['FastVideo/FastVideo-FastH3-8-Step-V2'],
                           planner=planner, cache_dir=str(tmp_path / 'obs'), output_dir=str(tmp_path / 'out'),
                           clock=lambda: clock.__setitem__(0, clock[0] + 1.5) or clock[0])
    project = store.create({'title': 'Demo', 'recipe': {'tier': 'preview'},
                            'shots': [{'id': f's{i}', 'title': f'Shot {i}', 'prompt': 'a lighthouse'} for i in range(2)]})
    return service, project, jobs


def test_preview_tier_shrinks_canvas_and_final_requires_accepted_preview(tmp_path):
    service, p, jobs = media_setup(tmp_path)
    service.approve(p['id'], 1)
    previewed = service.generate(p['id'], 1, 2)
    first = jobs[previewed['selected']['s0']]
    assert (first['width'], first['height']) == (544, 320) and 'tier' not in first
    assert all(t['tier'] == 'preview' for t in previewed['takes']['s0'])
    final_plan = deepcopy(previewed['blueprint']); final_plan['recipe']['tier'] = 'final'
    revised = service.update(p['id'], final_plan, previewed['revision'])
    assert revised['approved_revision'] is None and revised['selected'] == {}
    service.approve(p['id'], revised['revision'])
    with pytest.raises(ValueError, match='Accept a preview'):
        service.generate(p['id'], revised['revision'], 2)
    service.review(p['id'], 's0', previewed['selected']['s0'], 'accepted', 'composition ok')
    service.review(p['id'], 's1', previewed['selected']['s1'], 'accepted', '')
    final = service.generate(p['id'], revised['revision'], 2)
    job = jobs[final['selected']['s0']]
    assert (job['width'], job['height']) == (832, 480) and job['seed'] == first['seed']
    assert len(final['takes']['s0']) == 2 and final['takes']['s0'][1]['tier'] == 'final'
    assert final['takes']['s0'][0]['content_key'] == final['takes']['s0'][1]['content_key']
    # Changing the prompt after the preview invalidates the preview gate for that shot only.
    changed = deepcopy(final['blueprint']); changed['recipe']['tier'] = 'final'
    changed['shots'][1]['prompt'] = 'a lighthouse at dawn'
    changed = service.update(p['id'], changed, final['revision'])
    service.approve(p['id'], changed['revision'])
    with pytest.raises(ValueError, match='preview it again'):
        service.generate(p['id'], changed['revision'], 2)


def test_editorial_revision_keeps_approval_and_takes(tmp_path):
    service, p, jobs = media_setup(tmp_path)
    service.approve(p['id'], 1)
    generated = service.generate(p['id'], 1, 2)
    edited = deepcopy(generated['blueprint'])
    edited['shots'][0]['edit'] = {'out_seconds': 1.5, 'caption': 'Dawn'}
    edited['soundtrack']['gain_db'] = -20
    revised = service.update(p['id'], edited, generated['revision'])
    assert revised['approved_revision'] == revised['revision']
    assert revised['selected'] == generated['selected'] and revised['stale_shots'] == []
    assert service.generate(p['id'], revised['revision'], 0)['selected'] == generated['selected']


@pytest.mark.skipif(find_ffmpeg() is None, reason='needs an ffmpeg binary')
def test_observation_render_and_timing(tmp_path):
    service, p, jobs = media_setup(tmp_path)
    service.approve(p['id'], 1)
    generated = service.generate(p['id'], 1, 2)
    job_id = generated['selected']['s0']
    with pytest.raises(ValueError, match='accept'):
        service.render(p['id'])
    observation = service.observe(p['id'], 's0', job_id)
    assert observation['probe']['width'] == 128 and 'mostly_static' in observation['flags']
    assert service.observe(p['id'], 's0', job_id)['keyframes_path'] == observation['keyframes_path']
    assert service.store.get(p['id'])['takes']['s0'][0]['observation']['flags'] == observation['flags']
    for shot in ('s0', 's1'):
        service.review(p['id'], shot, generated['selected'][shot], 'accepted', '')
    edited = deepcopy(service.store.get(p['id'])['blueprint'])
    edited['shots'][1]['edit'] = {'transition': 'crossfade', 'transition_seconds': 0.5, 'caption': 'The end'}
    revised = service.update(p['id'], edited, generated['revision'])
    result = service.render(p['id'])
    assert result['clips'] == 2 and result['seconds'] == pytest.approx(3.5, abs=0.01)
    assert (tmp_path / 'out' / 'merged' / result['filename']).is_file()
    assert service.render_path(p['id'], result['filename']).endswith(result['filename'])
    timing = service.get(p['id'])['timing']
    assert timing['operations']['render'] > 0 and timing['operations']['observe'] > 0
    assert timing['generation_seconds'] == 16.0 and timing['queue_wait_seconds'] == 4.0
    assert timing['preview_takes'] == 2 and timing['final_takes'] == 0
    assert service.store.get(p['id'])['renders'][0]['revision'] == revised['revision']


def test_cut_proposal_only_changes_editorial_fields(tmp_path):
    seen = {}

    def planner(current, instruction, target, *, assets, observations=None, mode='story', durations=None):
        seen.update(mode=mode, durations=durations, observations=observations)
        return {'shots': [{'id': 's0', 'edit': {'out_seconds': 99, 'transition': 'crossfade', 'caption': '开场'}},
                          {'id': 's1', 'edit': {'in_seconds': 0.5, 'transition': 'fade_black'}}],
                'soundtrack': {'gain_db': -9, 'asset_id': 'ignored'}, 'rationale': 'Trim the static tail.',
                'recipe': {'model_id': 'MiniMaxAI/MiniMax-H3'}}

    service, p, jobs = media_setup(tmp_path, planner)
    with pytest.raises(ValueError, match='Generate'):
        service.propose_cut(p['id'], 1, 'tighten')
    service.approve(p['id'], 1)
    generated = service.generate(p['id'], 1, 2)
    proposed = service.propose_cut(p['id'], generated['revision'], 'tighten')
    assert seen['mode'] == 'cut' and seen['durations'] == {'s0': 2.0, 's1': 2.0}
    assert seen['observations']['s0'][0]['observation']['flags']
    assert proposed['blueprint']['shots'][0]['edit']['out_seconds'] is None  # beyond the take, dropped
    assert proposed['blueprint']['shots'][0]['edit']['caption'] == '开场'
    assert proposed['blueprint']['shots'][1]['edit']['transition'] == 'fade_black'
    assert proposed['blueprint']['soundtrack'] == {'asset_id': None, 'gain_db': -9, 'duck': True,
                                                   'fade_out_seconds': 2}
    assert proposed['blueprint']['recipe']['model_id'] == 'FastVideo/FastVideo-FastH3-8-Step-V2'
    assert proposed['approved_revision'] == proposed['revision'] and proposed['selected'] == generated['selected']
    assert proposed['messages'][-1]['content'] == 'Trim the static tail.'
    assert service.store.get(p['id'])['timings'][-1]['kind'] == 'propose_cut'
