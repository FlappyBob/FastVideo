# SPDX-License-Identifier: Apache-2.0
"""MCP tool surface over the Studio story-agent HTTP API.

An external agent (Claude Code, Codex, ViMax, a script) drives a project
through the same endpoints the web workspace uses, so every server-side rule
still applies: revisions are checked, budgets are bounded, generation needs an
approved storyboard, and a final take needs an accepted preview when one
exists. The tools never touch the GPU or the database directly.

Run from ``apps/`` in the FastVideo environment::

    python -m fastvideo_studio.agent_mcp --api http://127.0.0.1:8210/api

and register the command as a stdio MCP server in the client of your choice.
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import time
from typing import Any

import httpx

DEFAULT_API = os.getenv('STUDIO_API_URL', 'http://127.0.0.1:8189/api')
ACTIVE = {'queued', 'running', 'pending'}


class StudioError(RuntimeError):
    pass


class StudioClient:
    """Thin, synchronous wrapper; errors carry the server's detail text."""

    def __init__(self, api: str = DEFAULT_API, client: httpx.Client | None = None):
        self.api = api.rstrip('/')
        self.client = client or httpx.Client(timeout=600)

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.client.request(method, self.api + '/agent' + path, **kwargs)
        if response.status_code >= 400:
            try:
                detail = response.json().get('detail', response.text)
            except ValueError:
                detail = response.text
            raise StudioError(f'HTTP {response.status_code}: {detail}')
        return response.json() if response.content else None

    def binary(self, path: str) -> bytes:
        response = self.client.get(self.api + '/agent' + path)
        if response.status_code >= 400:
            raise StudioError(f'HTTP {response.status_code}: {response.text}')
        return response.content


def compact_project(project: dict[str, Any]) -> dict[str, Any]:
    """The project without bulky per-job logs; enough to decide the next step."""
    jobs = project.get('jobs') or {}
    takes = {}
    for shot_id, records in project.get('takes', {}).items():
        takes[shot_id] = [
            dict(job_id=t['job_id'],
                 tier=t.get('tier', 'final'),
                 review=t['review'],
                 note=t['note'],
                 status=(jobs.get(t['job_id']) or {}).get('status'),
                 error=(jobs.get(t['job_id']) or {}).get('error'),
                 flags=(t.get('observation') or {}).get('flags')) for t in records
        ]
    return dict(id=project['id'],
                revision=project['revision'],
                approved_revision=project['approved_revision'],
                blueprint=project['blueprint'],
                assets=project.get('assets', []),
                takes=takes,
                selected=project.get('selected', {}),
                stale_shots=project.get('stale_shots', []),
                timing=project.get('timing'),
                renders=[{k: v for k, v in r.items() if k != 'path'} for r in project.get('renders', [])],
                messages=project.get('messages', [])[-6:])


def guarded(function):
    """Server rejections come back as data the agent can read, not as opaque tool errors."""

    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except StudioError as exc:
            return dict(error=str(exc))

    return wrapper


def build_server(studio: StudioClient):
    from mcp.server.mcpserver import Image, MCPServer

    server = MCPServer(
        'fastvideo-studio-agent',
        instructions=('Plan, generate, observe, review and cut a multi-shot film in FastVideo Studio. '
                      'Storyboards must be approved by a person before generation; takes must be accepted '
                      'before export. Prefer the preview tier to check composition and action, then promote '
                      'accepted shots to final. Look at get_take before revising a shot.'))

    @server.tool()
    @guarded
    def list_projects() -> list[dict[str, Any]]:
        """Projects on this Studio, newest first (id, title, revision, shot count)."""
        return [
            dict(id=p['id'], title=p['blueprint']['title'], revision=p['revision'],
                 shots=len(p['blueprint']['shots']), approved=p['approved_revision'] == p['revision'])
            for p in studio.call('GET', '/projects')
        ]

    @server.tool()
    @guarded
    def create_project(title: str, brief: str = '', style: str = '', tier: str = 'preview') -> dict[str, Any]:
        """Create an empty story project. tier is 'preview' (fast, smaller canvas) or 'final'."""
        config = studio.call('GET', '/config')
        recipe = dict(tier=tier)
        if config.get('models'):
            recipe['model_id'] = config['models'][0]
        return compact_project(
            studio.call('POST', '/projects', json=dict(title=title, brief=brief, style=style, recipe=recipe)))

    @server.tool()
    @guarded
    def get_project(project_id: str) -> dict[str, Any]:
        """Current storyboard, takes with review status, selected takes, timing and renders."""
        return compact_project(studio.call('GET', f'/projects/{project_id}'))

    @server.tool()
    @guarded
    def save_blueprint(project_id: str, revision: int, blueprint: dict[str, Any]) -> dict[str, Any]:
        """Replace the storyboard (title, brief, style, characters, shots, recipe, soundtrack, captions).

        Editorial-only changes keep the approval; any generation input change drops it and marks the
        affected shots stale. Revision must match the project's current revision."""
        return compact_project(
            studio.call('PUT', f'/projects/{project_id}', json=dict(revision=revision, blueprint=blueprint)))

    @server.tool()
    @guarded
    def propose_plan(project_id: str, revision: int, instruction: str, target_shot: str | None = None) -> dict[str, Any]:
        """Ask the Studio planner to write or revise the storyboard. The proposal is saved for review,
        not approved. Set target_shot to change one shot only; the planner sees real take observations."""
        return compact_project(
            studio.call('POST',
                        f'/projects/{project_id}/plan',
                        json=dict(revision=revision, instruction=instruction, target_shot=target_shot)))

    @server.tool()
    @guarded
    def approve(project_id: str, revision: int) -> dict[str, Any]:
        """Approve the storyboard at this revision. Only a person should call this after reading the plan."""
        return compact_project(studio.call('POST', f'/projects/{project_id}/approve', json=dict(revision=revision)))

    @server.tool()
    @guarded
    def generate(project_id: str, revision: int, max_new_jobs: int = 3) -> dict[str, Any]:
        """Queue missing takes for the approved revision; completed matching takes are reused.
        Fails if more than max_new_jobs new GPU jobs would be needed."""
        return compact_project(
            studio.call('POST',
                        f'/projects/{project_id}/generate',
                        json=dict(revision=revision, max_new_jobs=max_new_jobs)))

    @server.tool()
    @guarded
    def wait_for_takes(project_id: str, timeout_seconds: int = 1800, poll_seconds: int = 10) -> dict[str, Any]:
        """Block until no take is queued or running (or the timeout passes); returns per-shot statuses."""
        deadline = time.monotonic() + max(1, timeout_seconds)
        while True:
            project = studio.call('GET', f'/projects/{project_id}')
            jobs = project.get('jobs') or {}
            states = {
                shot_id: (jobs.get(job_id) or {}).get('status')
                for shot_id, job_id in project.get('selected', {}).items()
            }
            active = [s for s in states.values() if s in ACTIVE]
            if not active or time.monotonic() >= deadline:
                return dict(done=not active, states=states, timing=project.get('timing'))
            time.sleep(max(1, poll_seconds))

    @server.tool(structured_output=False)
    @guarded
    def get_take(project_id: str, shot_id: str, job_id: str, include_keyframes: bool = True):
        """Observe one completed take: duration, per-second motion, hidden cuts, audio levels, flags,
        review status, plus a timestamped key-frame sheet image. Read it before revising the shot."""
        record = studio.call('GET', f'/projects/{project_id}/takes/{shot_id}/{job_id}')
        text = json.dumps({k: v for k, v in record.items() if k != 'keyframes_url'}, ensure_ascii=False)
        if not include_keyframes:
            return [text]
        sheet = studio.binary(f'/projects/{project_id}/takes/{shot_id}/{job_id}/keyframes')
        return [text, Image(data=sheet, format='jpeg')]

    @server.tool()
    @guarded
    def review(project_id: str, shot_id: str, job_id: str, verdict: str, note: str = '') -> dict[str, Any]:
        """Record a review verdict ('accepted', 'rejected' or 'pending') with a note citing what you saw.
        Acceptance of a final take is a human decision; an agent should propose, not accept, finals."""
        return compact_project(
            studio.call('POST',
                        f'/projects/{project_id}/review',
                        json=dict(shot_id=shot_id, job_id=job_id, verdict=verdict, note=note)))

    @server.tool()
    @guarded
    def propose_cut(project_id: str, revision: int, instruction: str = '') -> dict[str, Any]:
        """Ask the Studio planner for editorial decisions (in/out, transitions, captions, music level)
        from the observed takes. Generation inputs and approval are untouched."""
        return compact_project(
            studio.call('POST', f'/projects/{project_id}/cut/propose', json=dict(revision=revision,
                                                                                 instruction=instruction)))

    @server.tool()
    @guarded
    def render_cut(project_id: str) -> dict[str, Any]:
        """Render accepted takes with the current editorial decisions and soundtrack into one film."""
        return studio.call('POST', f'/projects/{project_id}/cut/render')

    @server.tool()
    @guarded
    def get_timing(project_id: str) -> dict[str, Any]:
        """Where wall-clock time went: planning, queue waits, generation, observation, rendering."""
        return studio.call('GET', f'/projects/{project_id}/timing')

    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description='MCP server for the FastVideo Studio story agent')
    parser.add_argument('--api', default=DEFAULT_API, help='Studio API base, e.g. http://127.0.0.1:8210/api')
    parser.add_argument('--transport', default='stdio', choices=['stdio', 'streamable-http'])
    args = parser.parse_args(argv)
    build_server(StudioClient(args.api)).run(transport=args.transport)


if __name__ == '__main__':
    main()
