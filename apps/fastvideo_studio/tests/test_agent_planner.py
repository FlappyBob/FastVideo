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
