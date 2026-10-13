# SPDX-License-Identifier: Apache-2.0
"""Durable scene, clip and take identities alongside Studio's jobs database.

GETs never infer or create scenes. Legacy name grouping is an explicit,
repeatable import; subsequent job renames cannot change an association.
"""
from __future__ import annotations

import contextlib
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

_SUFFIX = re.compile(r"^(.*?)[\s._-]*(?:clip|part|pt|shot|take)?[\s._-]*(\d+)$", re.I)


class SceneStore:

    def __init__(self, path: Path | str):
        self.path = path
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS studio_scenes (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, legacy_key TEXT UNIQUE
                );
                CREATE TABLE IF NOT EXISTS studio_clips (
                    id TEXT PRIMARY KEY, scene_id TEXT NOT NULL REFERENCES studio_scenes(id) ON DELETE CASCADE,
                    name TEXT NOT NULL, position INTEGER NOT NULL,
                    selected_job_id TEXT, legacy_key TEXT,
                    UNIQUE(scene_id, legacy_key)
                );
                CREATE TABLE IF NOT EXISTS studio_takes (
                    clip_id TEXT NOT NULL REFERENCES studio_clips(id) ON DELETE CASCADE,
                    job_id TEXT NOT NULL, position INTEGER NOT NULL,
                    PRIMARY KEY(clip_id, job_id)
                );
            """)

    @contextlib.contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _list(conn) -> list[dict[str, Any]]:
        scenes = []
        for row in conn.execute("SELECT * FROM studio_scenes ORDER BY rowid DESC"):
            clips = []
            for clip in conn.execute("SELECT * FROM studio_clips WHERE scene_id=? ORDER BY position", (row['id'], )):
                jobs = [
                    take['job_id']
                    for take in conn.execute("SELECT job_id FROM studio_takes WHERE clip_id=? ORDER BY position", (
                        clip['id'], ))
                ]
                clips.append(
                    dict(id=clip['id'], name=clip['name'], job_ids=jobs, selected_job_id=clip['selected_job_id']))
            scenes.append(dict(id=row['id'], name=row['name'], clips=clips))
        return scenes

    def list_scenes(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return self._list(conn)

    @staticmethod
    def _validate(payload: dict, jobs: dict[str, dict]) -> None:
        if not payload.get('name', '').strip():
            raise ValueError('Scene name is required')
        ids = [clip.get('id') for clip in payload['clips'] if clip.get('id')]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate clip IDs')
        used: set[str] = set()
        for clip in payload['clips']:
            if not clip.get('name', '').strip():
                raise ValueError('Clip name is required')
            take_ids = clip['job_ids']
            if not take_ids or len(take_ids) != len(set(take_ids)):
                raise ValueError('Each clip needs distinct takes')
            if used.intersection(take_ids):
                raise ValueError('A job cannot occupy multiple clips in one scene')
            used.update(take_ids)
            for job_id in take_ids:
                if job_id not in jobs or jobs[job_id].get('job_type', 'inference') != 'inference':
                    raise ValueError(f'Unknown inference job: {job_id}')
            if clip.get('selected_job_id') is not None and clip['selected_job_id'] not in take_ids:
                raise ValueError('Selected take must belong to the clip')

    def save(self, payload: dict, jobs: dict[str, dict], scene_id: str | None = None) -> dict:
        self._validate(payload, jobs)
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            existing = {}
            if scene_id:
                if not conn.execute('SELECT id FROM studio_scenes WHERE id=?', (scene_id, )).fetchone():
                    raise KeyError(scene_id)
                existing = {
                    row['id']: dict(row)
                    for row in conn.execute('SELECT * FROM studio_clips WHERE scene_id=?', (scene_id, ))
                }
            for clip in payload['clips']:
                if clip.get('id') and clip['id'] not in existing:
                    raise ValueError('Clip ID does not belong to this scene')
            scene_id = scene_id or str(uuid.uuid4())
            conn.execute(
                'INSERT INTO studio_scenes(id,name) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name',
                (scene_id, payload['name'].strip()))
            conn.execute('DELETE FROM studio_clips WHERE scene_id=?', (scene_id, ))
            for position, clip in enumerate(payload['clips']):
                clip_id = clip.get('id') or str(uuid.uuid4())
                # Explicit choices survive new takes and rename/reorder operations.
                selected = clip.get('selected_job_id') or self._default_take(clip['job_ids'], jobs)
                conn.execute('INSERT INTO studio_clips VALUES (?,?,?,?,?,?)',
                             (clip_id, scene_id, clip['name'].strip(), position, selected, existing.get(
                                 clip_id, {}).get('legacy_key')))
                conn.executemany('INSERT INTO studio_takes VALUES (?,?,?)',
                                 [(clip_id, job_id, index) for index, job_id in enumerate(clip['job_ids'])])
            return next(scene for scene in self._list(conn) if scene['id'] == scene_id)

    @staticmethod
    def _default_take(ids: list[str], jobs: dict[str, dict]) -> str:
        completed = [job_id for job_id in ids if jobs[job_id].get('status') == 'completed']
        candidates = completed or ids
        return max(candidates,
                   key=lambda job_id: ((jobs[job_id].get('finished_at') or jobs[job_id].get('created_at') or 0)
                                       if completed else (jobs[job_id].get('created_at') or 0)))

    def import_legacy(self, jobs: dict[str, dict]) -> list[dict]:
        """Import only unassociated jobs, appending takes without changing chosen takes."""
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            associated = {row[0] for row in conn.execute('SELECT job_id FROM studio_takes')}
            groups: dict[str, dict] = {}
            for job in sorted(jobs.values(), key=lambda job: job.get('created_at') or 0):
                if job['id'] in associated or job.get('job_type', 'inference') != 'inference':
                    continue
                name = (job.get('name') or '').strip()
                match = _SUFFIX.match(name)
                stem = match[1].rstrip(' ._-\t') if match else ''
                title = stem or name or 'Unnamed jobs'
                key = (stem or name).lower() or '__unnamed__'
                order = int(match[2]) if match and stem else None
                clip_key = str(order) if order is not None else f"job:{job['id']}"
                group = groups.setdefault(key, dict(title=title, clips={}))
                clip = group['clips'].setdefault(clip_key, dict(name=name or title, ids=[], order=order))
                clip['ids'].append(job['id'])
            for key, group in groups.items():
                row = conn.execute('SELECT id FROM studio_scenes WHERE legacy_key=?', (key, )).fetchone()
                scene_id = row['id'] if row else str(uuid.uuid4())
                if not row:
                    conn.execute('INSERT INTO studio_scenes VALUES (?,?,?)', (scene_id, group['title'], key))
                count = conn.execute('SELECT COUNT(*) FROM studio_clips WHERE scene_id=?', (scene_id, )).fetchone()[0]
                for clip_key, clip in sorted(group['clips'].items(),
                                             key=lambda item: (item[1]['order'] is None, item[1]['order'] or 0)):
                    old = conn.execute('SELECT id FROM studio_clips WHERE scene_id=? AND legacy_key=?',
                                       (scene_id, clip_key)).fetchone()
                    clip_id = old['id'] if old else str(uuid.uuid4())
                    if not old:
                        conn.execute(
                            'INSERT INTO studio_clips VALUES (?,?,?,?,?,?)',
                            (clip_id, scene_id, clip['name'], count, self._default_take(clip['ids'], jobs), clip_key))
                        count += 1
                    takes_count = conn.execute('SELECT COUNT(*) FROM studio_takes WHERE clip_id=?',
                                               (clip_id, )).fetchone()[0]
                    conn.executemany('INSERT INTO studio_takes VALUES (?,?,?)',
                                     [(clip_id, job_id, takes_count + i) for i, job_id in enumerate(clip['ids'])])
            return self._list(conn)
