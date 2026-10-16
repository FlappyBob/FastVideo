# SPDX-License-Identifier: Apache-2.0
import json

import httpx
import pytest

from fastvideo_studio.agent_planner import StoryPlanner


def test_planner_sends_blueprint_contract_and_preserves_private_settings():
    captured=[]
    def respond(request):
        captured.append(json.loads(request.content))
        assert request.headers['Authorization']=='Bearer private-test-key'
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({
            'title':'Story','shots':[{'id':'one','title':'Opening','prompt':'a lighthouse'}]})}}]})
    planner=StoryPlanner({'base_url':'http://planner.test/v1','model':'test','api_key':'private-test-key'},
                         transport=httpx.MockTransport(respond))
    output=planner({'title':'Story','brief':'a lighthouse','shots':[],'characters':[],
                    'recipe':{'num_gpus':4}},'plan three shots',None,assets={})
    assert output['shots'][0]['id']=='one'
    assert 'private-test-key' not in json.dumps(captured)
    assert 'num_gpus' not in json.dumps(captured)
    assert captured[0]['response_format']=={'type':'json_object'}


def test_bad_planner_response_is_not_turned_into_a_fake_success():
    planner=StoryPlanner({'base_url':'http://planner.test/v1','model':'test'},
        transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'choices':[{'message':{'content':'nonsense'}}]})))
    with pytest.raises(ValueError,match='JSON'):
        planner({'title':'Story','recipe':{}},'plan',None,assets={})


def test_reference_generator_uses_explicit_image_model_and_returns_image_bytes():
    import base64
    from fastvideo_studio.agent_planner import ReferenceGenerator
    captured=[]
    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(b'image-data').decode()}]})
    generator=ReferenceGenerator({'base_url':'http://image.test/v1','model':'local-image'},
                                  transport=httpx.MockTransport(respond))
    assert generator('yellow robot')==b'image-data'
    assert captured[0]['model']=='local-image'
    assert captured[0]['response_format']=='b64_json'


def test_planner_context_attaches_observations_and_keyframe_sheets(tmp_path):
    from PIL import Image
    from fastvideo_studio.agent_planner import planner_context
    sheet=tmp_path/'sheet.jpg';Image.new('RGB',(8,8)).save(sheet)
    reference=tmp_path/'ref.png';Image.new('RGB',(8,8)).save(reference)
    assets={'r':{'id':'r','name':'Robot','path':str(reference),'kind':'image'},
            'm':{'id':'m','name':'Music','path':'/x.mp3','kind':'audio'}}
    observations={'one':[{'job_id':'j','tier':'preview','status':'completed','review':'accepted','note':'',
                          'selected':True,'observation':{'seconds':5.2,'flags':['mostly_static']},
                          'keyframes_path':str(sheet)}]}
    payload,images=planner_context({'title':'S','recipe':{'num_gpus':8},'shots':[]},'tighten',None,assets=assets,
                                   observations=observations,mode='cut',durations={'one':5.2})
    assert 'num_gpus' not in json.dumps(payload) and payload['take_seconds']=={'one':5.2}
    assert payload['observations']['one'][0]['keyframes_image']=='image 2'
    assert 'keyframes_path' not in json.dumps(payload) and [i['path'] for i in images]==[str(reference),str(sheet)]
    captured=[]
    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'shots':[]})}}]})
    planner=StoryPlanner({'base_url':'http://planner.test/v1','model':'test'},transport=httpx.MockTransport(respond))
    planner({'title':'S','recipe':{},'shots':[]},'tighten',None,assets=assets,observations=observations,mode='cut')
    assert 'film editor' in captured[0]['messages'][0]['content']
    parts=captured[0]['messages'][1]['content']
    assert sum(1 for p in parts if p['type']=='image_url')==2 and 'image/jpeg' in parts[-1]['image_url']['url']
