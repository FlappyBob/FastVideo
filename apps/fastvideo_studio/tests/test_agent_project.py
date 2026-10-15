# SPDX-License-Identifier: Apache-2.0
from copy import deepcopy

import pytest

from fastvideo_studio.agent_project import AgentProjectStore, Blueprint, compile_jobs, import_vimax


def plan():
    return {'title':'A rainy arrival','brief':'A robot finds shelter','style':'cinematic blue and amber',
            'recipe':{'model_id':'MiniMaxAI/MiniMax-H3'},
            'characters':[{'id':'robot','name':'Robot','description':'small yellow robot','asset_ids':['asset1']}],
            'shots':[{'id':'wide','title':'Arrival','prompt':'Robot walks into a station','character_ids':['robot']},
                     {'id':'close','title':'Discovery','prompt':'Robot looks up','character_ids':['robot']},
                     {'id':'exit','title':'Departure','prompt':'Robot leaves','previous_shot_id':'close'}]}


def assets():
    return {'asset1':{'id':'asset1','sha256':'a'*64,'path':'/tmp/robot.png'}}


def test_compiles_shared_identity_and_previous_take_reference():
    jobs=compile_jobs(Blueprint.model_validate(plan()),assets(),{'wide':'j1','close':'j2','exit':'j3'})
    assert 'small yellow robot' in jobs['wide']['prompt']
    assert jobs['wide']['references'][0]['source']=='/tmp/robot.png'
    assert jobs['exit']['references']==[{'source':'job-last-frame:j2','media_type':'image'}]
    assert jobs['close']['num_inference_steps']==50


def test_t2v_checkpoint_cannot_silently_discard_continuity_inputs():
    payload=plan();payload['recipe']['model_id']='FastVideo/FastVideo-FastH3-8-Step-V2'
    with pytest.raises(ValueError,match='reference'):
        compile_jobs(Blueprint.model_validate(payload),assets(),{'wide':'j1','close':'j2','exit':'j3'})


def test_unknown_assets_and_dependency_cycles_rejected():
    with pytest.raises(ValueError,match='asset'):
        compile_jobs(Blueprint.model_validate(plan()),{}, {'wide':'j1','close':'j2','exit':'j3'})
    payload=plan();payload['shots'][1]['previous_shot_id']='exit'
    with pytest.raises(ValueError,match='cycle'):
        Blueprint.model_validate(payload)


def test_revision_invalidates_only_changed_shot_and_dependents(tmp_path):
    store=AgentProjectStore(tmp_path/'projects.db')
    project=store.create(plan())
    for shot in ['wide','close','exit']:
        store.add_take(project['id'],shot,shot+'-job',project['fingerprints'][shot])
    before=store.get(project['id'])
    revised=deepcopy(before['blueprint']);revised['shots'][1]['prompt']='Robot opens a red door'
    after=store.update(project['id'],revised,expected_revision=1)
    assert after['selected']=={'wide':'wide-job'}
    assert len(after['takes']['close'])==1
    assert after['stale_shots']==['close','exit']
    assert AgentProjectStore(tmp_path/'projects.db').get(project['id'])==after
    with pytest.raises(ValueError,match='revision'):
        store.update(project['id'],revised,expected_revision=1)
    assert store.get(project['id'])==after


def test_approval_invalidated_by_any_plan_change(tmp_path):
    store=AgentProjectStore(tmp_path/'projects.db');project=store.create(plan())
    approved=store.approve(project['id'],1)
    assert approved['approved_revision']==1
    changed=deepcopy(project['blueprint']);changed['style']='daylight'
    revised=store.update(project['id'],changed,expected_revision=1)
    assert revised['approved_revision'] is None


def test_import_vimax_preserves_character_and_shot_semantics():
    imported=import_vimax({'characters':[{'idx':0,'identifier_in_scene':'robot','static_features':'yellow robot',
        'dynamic_features':'wet coat'}],'shot_descriptions':[{'idx':0,'visual_desc':'a station',
        'motion_desc':'walking','audio_desc':'rain','ff_vis_char_idxs':[0]}]})
    assert imported['characters'][0]['description']=='yellow robot; wet coat'
    assert imported['shots'][0]['character_ids']==['character-0']
    assert 'walking' in imported['shots'][0]['prompt']
