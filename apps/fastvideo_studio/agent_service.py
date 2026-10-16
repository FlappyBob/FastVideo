# SPDX-License-Identifier: Apache-2.0
"""Story-project orchestration on Studio's existing job executor.

The service owns state, admission and verification. Planning is delegated to
a replaceable planner that only ever returns proposals; people approve
storyboards and accept takes. Observations of real media (``observe.py``)
feed both the planner and the reviewer, and every operation that costs wall
clock time is recorded so a workflow can be compared, not just described.
"""
from __future__ import annotations

import copy
import os
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from .agent_project import AgentProjectStore, Blueprint, ShotEdit, Soundtrack, compile_jobs
from .edit_render import SoundtrackSpec, render_cut
from .job_queue import validate_reference_content
from .observe import ObservationError, observe_video, summarize

ACTIVE = {'queued', 'running'}


class AgentService:

    def __init__(self,
                 store: AgentProjectStore,
                 create_job,
                 get_job,
                 enqueue,
                 model_ids,
                 planner=None,
                 image_generator=None,
                 *,
                 video_path: Callable[[dict[str, Any]], str | None] | None = None,
                 cache_dir: str | None = None,
                 output_dir: str | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.store = store
        self.create_job = create_job
        self.get_job = get_job
        self.enqueue = enqueue
        self.model_ids = model_ids
        self.planner = planner
        self.image_generator = image_generator
        self.video_path = video_path or (lambda job: job.get('output_path'))
        self.cache_dir = cache_dir or os.path.join(os.path.dirname(str(store.path)), 'agent-observations')
        self.output_dir = output_dir or os.path.join(os.path.dirname(str(store.path)), 'agent-renders')
        self.clock = clock
        self.lock = threading.RLock()

    # ------------------------------------------------------------------ reads
    def get(self, project_id: str) -> dict[str, Any]:
        project = self.store.get(project_id)
        project['assets'] = list(self.store.assets(project_id).values())
        for asset in project['assets']:
            asset.pop('path', None)
        project['jobs'] = {
            take['job_id']: self.get_job(take['job_id'])
            for takes in project['takes'].values()
            for take in takes
        }
        project['timing'] = self.timing(project)
        return project

    def timing(self, project: dict[str, Any]) -> dict[str, Any]:
        """Where the clock went: planning, queue waits, generation, rendering."""
        totals: dict[str, float] = {}
        for entry in project.get('timings', []):
            totals[entry['kind']] = round(totals.get(entry['kind'], 0) + entry['seconds'], 3)
        shots = []
        for shot in project['blueprint']['shots']:
            for take in project['takes'].get(shot['id'], []):
                job = self.get_job(take['job_id']) or {}
                queued, started, finished = job.get('queued_at'), job.get('started_at'), job.get('finished_at')
                shots.append(
                    dict(shot_id=shot['id'],
                         job_id=take['job_id'],
                         tier=take.get('tier', 'final'),
                         status=job.get('status'),
                         queue_wait_seconds=round(started - queued, 1) if queued and started else None,
                         generation_seconds=round(finished - started, 1) if started and finished else None))
        generation = [s['generation_seconds'] for s in shots if s['generation_seconds'] is not None]
        waits = [s['queue_wait_seconds'] for s in shots if s['queue_wait_seconds'] is not None]
        return dict(operations=totals,
                    takes=shots,
                    generation_seconds=round(sum(generation), 1),
                    queue_wait_seconds=round(sum(waits), 1),
                    final_takes=sum(1 for s in shots if s['tier'] == 'final' and s['status'] == 'completed'),
                    preview_takes=sum(1 for s in shots if s['tier'] == 'preview' and s['status'] == 'completed'))

    def _editable(self, project: dict[str, Any]) -> None:
        for takes in project['takes'].values():
            for take in takes:
                job = self.get_job(take['job_id'])
                if job and job['status'] in ACTIVE:
                    raise ValueError('Project has active jobs; wait or stop them before revising')

    def update(self, project_id: str, payload: dict[str, Any], revision: int) -> dict[str, Any]:
        with self.lock:
            self._editable(self.store.get(project_id))
            return self.store.update(project_id, payload, revision)

    # -------------------------------------------------------------- admission
    def _admit(self, project: dict[str, Any], ids: dict[str, str]) -> dict[str, Any]:
        plan = Blueprint.model_validate(project['blueprint'])
        if plan.recipe.model_id not in self.model_ids():
            raise ValueError('Unknown model; select a registered Studio model')
        compiled = compile_jobs(plan, self.store.assets(project['id']), ids)
        checked = set()
        for payload in compiled.values():
            for reference in payload['references']:
                if reference.get('sha256') and reference['source'] not in checked:
                    validate_reference_content(reference)
                    checked.add(reference['source'])
        return compiled

    @staticmethod
    def _assert_approved(job: dict[str, Any], expected: dict[str, Any]) -> None:
        changed = [key for key, value in expected.items() if key != 'name' and job.get(key) != value]
        if changed:
            raise ValueError('Job no longer matches approved storyboard inputs; request a new take (' +
                             ', '.join(changed) + ')')

    def approve(self, project_id: str, revision: int) -> dict[str, Any]:
        with self.lock:
            project = self.store.get(project_id)
            self._admit(project, {s['id']: 'admission-check' for s in project['blueprint']['shots']})
            return self.store.approve(project_id, revision)

    def _preview_gate(self, project: dict[str, Any], shot_id: str) -> None:
        """A final take may only follow an accepted preview of the same content, if previews exist."""
        if project['blueprint']['recipe']['tier'] != 'final':
            return
        key = project['content_keys'].get(shot_id)
        previews = [t for t in project['takes'].get(shot_id, []) if t.get('tier') == 'preview']
        if not previews:
            return
        matching = [t for t in previews if t.get('content_key') == key]
        if not matching:
            raise ValueError(f'Shot {shot_id} changed since its preview; preview it again before the final render')
        if not any(t['review'] == 'accepted' for t in matching):
            raise ValueError(f'Accept a preview of shot {shot_id} before submitting its final render')

    def generate(self, project_id: str, revision: int, max_new_jobs: int):
        if type(max_new_jobs) is not int or not 0 <= max_new_jobs <= 32:
            raise ValueError('New-job budget must be between zero and 32')
        with self.lock:
            project = self.store.get(project_id)
            if project['revision'] != revision:
                raise ValueError('Project revision changed; reload')
            if project['approved_revision'] != revision:
                raise ValueError('Review and approve the current storyboard before generation')
            ids = {}
            new_count = 0
            for shot in project['blueprint']['shots']:
                key = shot['id']
                current = project['fingerprints'][key]
                reusable = next((t for t in reversed(project['takes'].get(key, []))
                                 if t['fingerprint'] == current and t['review'] != 'rejected'), None)
                job_id = reusable['job_id'] if reusable else str(
                    uuid.uuid5(uuid.NAMESPACE_URL, f'fastvideo-agent:{project_id}:{key}:{current}'))
                if any(t['job_id'] == job_id and t['review'] == 'rejected' for t in project['takes'].get(key, [])):
                    raise ValueError('This take was rejected; request a new take by revising its seed')
                job = self.get_job(job_id)
                if reusable and not job:
                    raise ValueError(
                        'A recorded job is missing. Create a new take explicitly rather than resubmitting.')
                if job and job['status'] in {'failed', 'stopped'}:
                    raise ValueError('A take failed or stopped. Revise its seed to request a new take explicitly.')
                if not job:
                    self._preview_gate(project, key)
                    new_count += 1
                ids[key] = job_id
            if new_count > max_new_jobs:
                raise ValueError(f'Generation budget is {max_new_jobs}, but {new_count} new jobs are required')
            compiled = self._admit(project, ids)
            for shot_id, job_id in ids.items():
                existing = self.get_job(job_id)
                if existing is not None:
                    self._assert_approved(existing, compiled[shot_id])
            operation_id = f'revision-{revision}'

            def reserve(current: dict[str, Any]) -> None:
                if current['revision'] != revision or current['approved_revision'] != revision:
                    raise ValueError('Project revision changed during admission')
                current['operations'].setdefault(operation_id, dict(job_ids=ids, phase='prepared'))

            self.store.mutate(project_id, reserve)
            # Journal is committed before callbacks. Retrying can reconcile every
            # deterministic ID even after a crash between creation and add_take.
            for shot_id, job_id in ids.items():
                if self.get_job(job_id) is None:
                    self.create_job(job_id, compiled[shot_id])
                self._assert_approved(self.get_job(job_id), compiled[shot_id])
                self.store.add_take(project_id, shot_id, job_id, project['fingerprints'][shot_id])
            waiting = [job_id for job_id in ids.values() if self.get_job(job_id)['status'] == 'pending']
            if waiting:
                self.enqueue(waiting,
                             expected_jobs={
                                 ids[key]: {
                                     field: value
                                     for field, value in payload.items() if field != 'name'
                                 }
                                 for key, payload in compiled.items()
                             })
            self.store.mutate(project_id, lambda current: current['operations'][operation_id].update(phase='submitted'))
            return self.store.get(project_id)

    # ------------------------------------------------------------ observation
    def _take(self, project: dict[str, Any], shot_id: str, job_id: str) -> dict[str, Any]:
        take = next((t for t in project['takes'].get(shot_id, []) if t['job_id'] == job_id), None)
        if take is None:
            raise KeyError('Take does not belong to this shot')
        return take

    def observe(self, project_id: str, shot_id: str, job_id: str) -> dict[str, Any]:
        """Facts about one completed take, computed once and kept with the take."""
        project = self.store.get(project_id)
        take = self._take(project, shot_id, job_id)
        job = self.get_job(job_id)
        if not job or job['status'] != 'completed':
            raise ValueError('Only completed takes can be observed')
        started = self.clock()
        try:
            record = observe_video(self.video_path(job) or '', self.cache_dir, expected_frames=job.get('num_frames'))
        except ObservationError as exc:
            raise ValueError(exc.detail) from exc
        if take.get('observation') != record:

            def change(current: dict[str, Any]) -> None:
                self._take(current, shot_id, job_id)['observation'] = record

            self.store.mutate(project_id, change)
            self.store.record_timing(project_id, 'observe', self.clock() - started, shot_id=shot_id, job_id=job_id)
        return dict(shot_id=shot_id,
                    job_id=job_id,
                    tier=take.get('tier', 'final'),
                    review=take['review'],
                    note=take['note'],
                    **record)

    def keyframes_path(self, project_id: str, shot_id: str, job_id: str) -> str:
        return self.observe(project_id, shot_id, job_id)['keyframes_path']

    def _context(self, project: dict[str, Any]) -> dict[str, Any]:
        """Real results and reviews, per shot, for a planner that must look before it revises."""
        context: dict[str, Any] = {}
        for shot in project['blueprint']['shots']:
            entries = []
            for take in project['takes'].get(shot['id'], []):
                job = self.get_job(take['job_id']) or {}
                entry = dict(job_id=take['job_id'],
                             tier=take.get('tier', 'final'),
                             status=job.get('status'),
                             review=take['review'],
                             note=take['note'],
                             selected=project['selected'].get(shot['id']) == take['job_id'])
                if job.get('status') == 'completed':
                    try:
                        observation = self.observe(project['id'], shot['id'], take['job_id'])
                        entry['observation'] = summarize(observation)
                        entry['keyframes_path'] = observation['keyframes_path']
                    except ValueError as exc:
                        entry['observation_error'] = str(exc)
                entries.append(entry)
            if entries:
                context[shot['id']] = entries
        return context

    # ---------------------------------------------------------------- planning
    def plan(self, project_id: str, revision: int, instruction: str, target_shot: str | None) -> dict[str, Any]:
        if self.planner is None:
            raise RuntimeError(
                'Planner is not configured. Start the server with --agent-config or import a ViMax plan.')
        if not instruction.strip():
            raise ValueError('Describe the story or revision')
        current = self.store.get(project_id)
        if current['revision'] != revision:
            raise ValueError('Project revision changed')
        self._editable(current)
        started = self.clock()
        proposed = self.planner(copy.deepcopy(current['blueprint']),
                                instruction,
                                target_shot,
                                assets=self.store.assets(project_id),
                                observations=self._context(current))
        latency = self.clock() - started
        proposed['recipe'] = current['blueprint']['recipe']  # LLM cannot change hardware/model permissions.
        proposed.setdefault('soundtrack', current['blueprint']['soundtrack'])
        proposed.setdefault('captions', current['blueprint']['captions'])
        candidate = Blueprint.model_validate(proposed).model_dump()
        if target_shot:
            replacement = next((s for s in candidate['shots'] if s['id'] == target_shot), None)
            if replacement is None or target_shot not in current['fingerprints']:
                raise ValueError('Planner did not preserve the requested shot ID')
            candidate = copy.deepcopy(current['blueprint'])
            candidate['shots'] = [replacement if shot['id'] == target_shot else shot for shot in candidate['shots']]
        with self.lock:
            updated = self.update(project_id, candidate, revision)
            self.store.record_timing(project_id, 'plan', latency, target_shot=target_shot)

            def record(project: dict[str, Any]) -> None:
                project['messages'].append(dict(role='user', content=instruction, revision=updated['revision']))
                project['messages'].append(
                    dict(role='assistant',
                         content='Storyboard proposal saved for review.',
                         revision=updated['revision']))

            return self.store.mutate(project_id, record)

    def propose_cut(self, project_id: str, revision: int, instruction: str) -> dict[str, Any]:
        """Ask the planner for editorial decisions only; generation inputs stay fixed."""
        if self.planner is None:
            raise RuntimeError('Planner is not configured.')
        current = self.store.get(project_id)
        if current['revision'] != revision:
            raise ValueError('Project revision changed')
        context = self._context(current)
        durations = {
            shot['id']: next((e['observation']['seconds'] for e in context.get(shot['id'], [])
                              if e.get('selected') and e.get('observation')), None)
            for shot in current['blueprint']['shots']
        }
        if not any(durations.values()):
            raise ValueError('Generate and review at least one take before proposing a cut')
        started = self.clock()
        proposed = self.planner(copy.deepcopy(current['blueprint']),
                                instruction or 'Propose the cut.',
                                None,
                                assets=self.store.assets(project_id),
                                observations=context,
                                mode='cut',
                                durations=durations)
        latency = self.clock() - started
        candidate = copy.deepcopy(current['blueprint'])
        edits = {item['id']: item.get('edit', {}) for item in proposed.get('shots', []) if isinstance(item, dict)}
        for shot in candidate['shots']:
            if shot['id'] in edits:
                edit = ShotEdit.model_validate(edits[shot['id']]).model_dump()
                limit = durations.get(shot['id'])
                if limit:
                    if edit['in_seconds'] >= limit:
                        raise ValueError(f'Planner put the in point of {shot["id"]} past the end of its take')
                    if edit['out_seconds'] and edit['out_seconds'] > limit + 0.05:
                        edit['out_seconds'] = None
                shot['edit'] = edit
        if isinstance(proposed.get('soundtrack'), dict):
            soundtrack = dict(candidate['soundtrack'])
            for key in ('gain_db', 'duck', 'fade_out_seconds'):
                if key in proposed['soundtrack']:
                    soundtrack[key] = proposed['soundtrack'][key]
            candidate['soundtrack'] = Soundtrack.model_validate(soundtrack).model_dump()
        with self.lock:
            updated = self.update(project_id, candidate, revision)
            self.store.record_timing(project_id, 'propose_cut', latency)

            def record(project: dict[str, Any]) -> None:
                project['messages'].append(dict(role='user', content=instruction or 'Propose the cut.',
                                                revision=updated['revision']))
                project['messages'].append(
                    dict(role='assistant',
                         content=str(proposed.get('rationale', 'Cut proposal saved; review the shot edits.'))[:2000],
                         revision=updated['revision']))

            return self.store.mutate(project_id, record)

    # ------------------------------------------------------------------ review
    def review(self, project_id: str, shot_id: str, job_id: str, verdict: str, note: str) -> dict[str, Any]:
        if verdict not in {'pending', 'accepted', 'rejected'}:
            raise ValueError('Unknown review verdict')
        job = self.get_job(job_id)
        if not job or job['status'] != 'completed':
            raise ValueError('Only completed takes can be reviewed')

        def change(project: dict[str, Any]) -> None:
            take = next((t for t in project['takes'].get(shot_id, []) if t['job_id'] == job_id), None)
            if take is None:
                raise ValueError('Take does not belong to this shot')
            take.update(review=verdict, note=note[:4000])

        return self.store.mutate(project_id, change)

    def export_jobs(self, project_id: str) -> list[str]:
        project = self.store.get(project_id)
        ordered = []
        for shot in project['blueprint']['shots']:
            job_id = project['selected'].get(shot['id'])
            job = self.get_job(job_id) if job_id else None
            take = next((t for t in project['takes'].get(shot['id'], []) if t['job_id'] == job_id), None)
            if not job or job['status'] != 'completed' or not take or take['review'] != 'accepted':
                raise ValueError('Review and accept a completed current take for every shot before export')
            ordered.append(job_id)
        if not ordered:
            raise ValueError('No shots to export')
        compiled = self._admit(project, project['selected'])
        for shot_id, payload in compiled.items():
            self._assert_approved(self.get_job(project['selected'][shot_id]), payload)
        return ordered

    def render(self, project_id: str) -> dict[str, Any]:
        """Render accepted takes with the current editorial decisions."""
        with self.lock:
            job_ids = self.export_jobs(project_id)
            project = self.store.get(project_id)
            plan = project['blueprint']
            paths = {}
            for shot, job_id in zip(plan['shots'], job_ids, strict=True):
                path = self.video_path(self.get_job(job_id))
                if not path:
                    raise ValueError(f'Take media for {shot["title"]} is not available')
                paths[shot['id']] = path
            soundtrack = None
            if plan['soundtrack']['asset_id']:
                assets = self.store.assets(project_id)
                asset = assets.get(plan['soundtrack']['asset_id'])
                if not asset or asset.get('kind') != 'audio':
                    raise ValueError('Soundtrack asset is missing; upload it again')
                soundtrack = SoundtrackSpec(path=asset['path'],
                                            gain_db=plan['soundtrack']['gain_db'],
                                            duck=plan['soundtrack']['duck'],
                                            fade_out_seconds=plan['soundtrack']['fade_out_seconds'])
            started = self.clock()
            try:
                result = render_cut(plan['shots'],
                                    paths,
                                    self.output_dir,
                                    name=plan['title'],
                                    soundtrack=soundtrack,
                                    font_size=plan['captions']['font_size'],
                                    caption_position=plan['captions']['position'],
                                    job_ids=job_ids)
            except Exception as exc:
                detail = getattr(exc, 'detail', str(exc))
                raise ValueError(f'Render failed: {detail}') from exc
            seconds = self.clock() - started
            entry = dict(filename=result.filename,
                         path=result.path,
                         clips=result.clips,
                         seconds=result.seconds,
                         timeline=result.timeline,
                         revision=project['revision'],
                         job_ids=job_ids,
                         render_seconds=round(seconds, 3),
                         at=time.time())

            def change(current: dict[str, Any]) -> None:
                current['renders'].append(entry)
                del current['renders'][:-50]

            self.store.mutate(project_id, change)
            self.store.record_timing(project_id, 'render', seconds, filename=result.filename)
            return {key: value for key, value in entry.items() if key != 'path'}

    def render_path(self, project_id: str, filename: str) -> str:
        project = self.store.get(project_id)
        entry = next((r for r in project['renders'] if r['filename'] == filename), None)
        if entry is None or not os.path.isfile(entry['path']):
            raise KeyError('Render not found')
        return entry['path']
