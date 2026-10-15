# SPDX-License-Identifier: Apache-2.0
"""Story-project orchestration on Studio's existing job executor."""
from __future__ import annotations

import copy
import threading
import uuid
from typing import Any

from .agent_project import AgentProjectStore, Blueprint, compile_jobs


class AgentService:

    def __init__(self,
                 store: AgentProjectStore,
                 create_job,
                 get_job,
                 enqueue,
                 model_ids,
                 planner=None,
                 image_generator=None):
        self.store = store
        self.create_job = create_job
        self.get_job = get_job
        self.enqueue = enqueue
        self.model_ids = model_ids
        self.planner = planner
        self.image_generator = image_generator
        self.lock = threading.RLock()

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
        return project

    def _editable(self, project: dict[str, Any]) -> None:
        for takes in project['takes'].values():
            for take in takes:
                job = self.get_job(take['job_id'])
                if job and job['status'] in {'queued', 'running'}:
                    raise ValueError('Project has active jobs; wait or stop them before revising')

    def update(self, project_id: str, payload: dict[str, Any], revision: int) -> dict[str, Any]:
        with self.lock:
            self._editable(self.store.get(project_id))
            return self.store.update(project_id, payload, revision)

    def _admit(self, project: dict[str, Any], ids: dict[str, str]) -> dict[str, Any]:
        plan = Blueprint.model_validate(project['blueprint'])
        if plan.recipe.model_id not in self.model_ids():
            raise ValueError('Unknown model; select a registered Studio model')
        return compile_jobs(plan, self.store.assets(project['id']), ids)

    def approve(self, project_id: str, revision: int) -> dict[str, Any]:
        with self.lock:
            project = self.store.get(project_id)
            self._admit(project, {s['id']: 'admission-check' for s in project['blueprint']['shots']})
            return self.store.approve(project_id, revision)

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
                job = self.get_job(job_id)
                if reusable and not job:
                    raise ValueError(
                        'A recorded job is missing. Create a new take explicitly rather than resubmitting.')
                if job and job['status'] in {'failed', 'stopped'}:
                    raise ValueError('A take failed or stopped. Revise its seed to request a new take explicitly.')
                if not job:
                    new_count += 1
                ids[key] = job_id
            if new_count > max_new_jobs:
                raise ValueError(f'Generation budget is {max_new_jobs}, but {new_count} new jobs are required')
            compiled = self._admit(project, ids)
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
                self.store.add_take(project_id, shot_id, job_id, project['fingerprints'][shot_id])
            waiting = [job_id for job_id in ids.values() if self.get_job(job_id)['status'] == 'pending']
            if waiting:
                self.enqueue(waiting)
            self.store.mutate(project_id, lambda current: current['operations'][operation_id].update(phase='submitted'))
            return self.store.get(project_id)

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
        proposed = self.planner(copy.deepcopy(current['blueprint']),
                                instruction,
                                target_shot,
                                assets=self.store.assets(project_id))
        proposed['recipe'] = current['blueprint']['recipe']  # LLM cannot change hardware/model permissions.
        candidate = Blueprint.model_validate(proposed).model_dump()
        if target_shot:
            replacement = next((s for s in candidate['shots'] if s['id'] == target_shot), None)
            if replacement is None or target_shot not in current['fingerprints']:
                raise ValueError('Planner did not preserve the requested shot ID')
            candidate = copy.deepcopy(current['blueprint'])
            candidate['shots'] = [replacement if shot['id'] == target_shot else shot for shot in candidate['shots']]
        with self.lock:
            updated = self.update(project_id, candidate, revision)

            def record(project: dict[str, Any]) -> None:
                project['messages'].append(dict(role='user', content=instruction, revision=updated['revision']))
                project['messages'].append(
                    dict(role='assistant',
                         content='Storyboard proposal saved for review.',
                         revision=updated['revision']))

            return self.store.mutate(project_id, record)

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
        return ordered
