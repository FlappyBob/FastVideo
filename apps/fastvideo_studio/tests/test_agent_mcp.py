# SPDX-License-Identifier: Apache-2.0
"""External agents drive a project through MCP tools that only call the HTTP API."""
import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastvideo_studio.agent_mcp import StudioClient, StudioError, build_server, compact_project
from fastvideo_studio.agent_project import AgentProjectStore, V2
from fastvideo_studio.agent_routes import build_agent_router
from fastvideo_studio.agent_service import AgentService
from fastvideo_studio.merge import find_ffmpeg
from fastvideo_studio.tests.test_merge import write_clip

pytest.importorskip('mcp.server.mcpserver')


def studio(tmp_path):
    jobs = {}

    def create(job_id, payload):
        jobs[job_id] = dict(id=job_id, status='pending', **payload)
        return jobs[job_id]

    def enqueue(ids, **kwargs):
        for job_id in ids:
            path = tmp_path / f'{job_id}.mp4'
            write_clip(str(path), frames=48)
            jobs[job_id].update(status='completed', output_path=str(path))

    service = AgentService(AgentProjectStore(tmp_path / 'db.sqlite'), create, jobs.get, enqueue, lambda: [V2],
                           cache_dir=str(tmp_path / 'obs'), output_dir=str(tmp_path / 'out'))
    app = FastAPI()
    app.include_router(build_agent_router(lambda: service, tmp_path / 'assets', mock=True))
    client = TestClient(app)
    return build_server(StudioClient('http://testserver/api', client=client)), jobs


def call(server, name, **arguments):
    from mcp.server.mcpserver.exceptions import ToolError
    try:
        return asyncio.run(server.call_tool(name, arguments)).content
    except ToolError as exc:  # the server-side detail is preserved for the agent
        raise StudioError(str(exc)) from exc


def text_of(content):
    return json.loads(content[0].text)


@pytest.mark.skipif(find_ffmpeg() is None, reason='needs an ffmpeg binary')
def test_tools_walk_the_whole_workflow_without_bypassing_server_rules(tmp_path):
    server, jobs = studio(tmp_path)
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert {'create_project', 'get_project', 'propose_plan', 'approve', 'generate', 'get_take', 'review',
            'propose_cut', 'render_cut', 'wait_for_takes', 'get_timing'} <= names
    created = text_of(call(server, 'create_project', title='Demo', brief='A robot'))
    assert created['blueprint']['recipe']['tier'] == 'preview'
    project_id, revision = created['id'], created['revision']
    blueprint = created['blueprint']
    blueprint['shots'] = [dict(id='a', title='Arrival', prompt='A robot arrives')]
    refused = text_of(call(server, 'generate', project_id=project_id, revision=revision, max_new_jobs=1))
    assert 'approve' in refused['error']
    saved = text_of(call(server, 'save_blueprint', project_id=project_id, revision=revision, blueprint=blueprint))
    approved = text_of(call(server, 'approve', project_id=project_id, revision=saved['revision']))
    generated = text_of(call(server, 'generate', project_id=project_id, revision=approved['revision'], max_new_jobs=1))
    job_id = generated['selected']['a']
    waited = text_of(call(server, 'wait_for_takes', project_id=project_id, timeout_seconds=2, poll_seconds=1))
    assert waited['done'] and waited['states'] == {'a': 'completed'}
    take = call(server, 'get_take', project_id=project_id, shot_id='a', job_id=job_id)
    assert json.loads(take[0].text)['probe']['width'] == 128
    assert take[1].type == 'image' and 'jpeg' in (getattr(take[1], 'mime_type', None) or getattr(take[1], 'mimeType', ''))
    reviewed = text_of(call(server, 'review', project_id=project_id, shot_id='a', job_id=job_id, verdict='accepted',
                            note='static but usable'))
    assert reviewed['takes']['a'][0]['review'] == 'accepted' and reviewed['takes']['a'][0]['flags']
    assert 'HTTP 503' in text_of(call(server, 'propose_cut', project_id=project_id, revision=reviewed['revision']))['error']
    rendered = text_of(call(server, 'render_cut', project_id=project_id))
    assert rendered['clips'] == 1 and rendered['url'].endswith('.mp4')
    timing = text_of(call(server, 'get_timing', project_id=project_id))
    assert timing['operations']['render'] >= 0
    listed = text_of(call(server, 'list_projects'))  # one text block per project; this Studio has one
    assert listed['id'] == project_id and listed['approved'] is True


def test_compact_project_drops_job_logs_but_keeps_decisions():
    project = dict(id='p', revision=2, approved_revision=2, blueprint=dict(title='t', shots=[]), takes={
        's': [dict(job_id='j', review='pending', note='', observation=dict(flags=['mostly_static']))]
    }, jobs={'j': dict(status='completed', error=None, logs='x' * 10000)}, selected={'s': 'j'},
                   renders=[dict(filename='f.mp4', path='/secret/f.mp4')])
    compact = compact_project(project)
    assert compact['takes']['s'][0]['flags'] == ['mostly_static'] and compact['takes']['s'][0]['status'] == 'completed'
    assert 'logs' not in json.dumps(compact) and '/secret' not in json.dumps(compact)
