# SPDX-License-Identifier: Apache-2.0
"""Persistent story blueprints, identity references, revisions and take ownership."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Key = Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')]
V2 = 'FastVideo/FastVideo-FastH3-8-Step-V2'
FULL_H3 = 'MiniMaxAI/MiniMax-H3'


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', validate_default=True)


class Recipe(StrictModel):
    model_id: str = V2
    width: int = Field(default=832, ge=64, le=4096)
    height: int = Field(default=480, ge=64, le=4096)
    num_frames: int = Field(default=124, ge=1, le=1024)
    fps: int = Field(default=24, ge=1, le=120)
    num_inference_steps: int = Field(default=50, ge=1, le=200)
    guidance_scale: float = Field(default=5, ge=0, le=20)
    attention_backend: str | None = None
    vsa_tile_size: int | None = Field(default=None, gt=0)
    vsa_sparsity: float = Field(default=0, ge=0, le=1)
    num_gpus: int = Field(default=1, ge=1, le=64)
    dit_layerwise_offload: bool = True
    text_encoder_cpu_offload: bool = True
    vae_cpu_offload: bool = True
    enable_torch_compile: bool = False
    # Preview renders the same prompt/seed on a smaller canvas so composition,
    # action and cuts can be judged quickly; final uses the full canvas.
    tier: Literal['preview', 'final'] = 'final'

    @model_validator(mode='before')
    @classmethod
    def recipe_defaults(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get('model_id', V2) == V2:
            value = dict(value)
            for key, default in dict(num_inference_steps=9,
                                     guidance_scale=1,
                                     vsa_sparsity=.8,
                                     attention_backend='VIDEO_SPARSE_ATTN_H3',
                                     vsa_tile_size=64).items():
                value.setdefault(key, default)
        return value


PREVIEW_SCALE = 2 / 3
CANVAS_MULTIPLE = 32


def preview_canvas(width: int, height: int) -> tuple[int, int]:
    """Smaller canvas with the same aspect, on the 32-pixel grid H3 requires."""

    def shrink(value: int) -> int:
        return max(CANVAS_MULTIPLE, round(value * PREVIEW_SCALE / CANVAS_MULTIPLE) * CANVAS_MULTIPLE)

    return shrink(width), shrink(height)


def effective_recipe(recipe: Recipe) -> dict[str, Any]:
    """Generation parameters actually submitted for this tier."""
    values = recipe.model_dump(exclude={'tier'})
    if recipe.tier == 'preview':
        values['width'], values['height'] = preview_canvas(recipe.width, recipe.height)
    return values


class ShotEdit(StrictModel):
    """Editorial decisions for one shot. They never change generation inputs."""
    in_seconds: float = Field(default=0, ge=0, le=600)
    out_seconds: float | None = Field(default=None, gt=0, le=600)
    transition: Literal['cut', 'crossfade', 'fade_black'] = 'cut'
    transition_seconds: float = Field(default=0.5, ge=0.1, le=3)
    caption: str = Field(default='', max_length=200)
    caption_start: float = Field(default=0, ge=0, le=600)
    caption_end: float | None = Field(default=None, gt=0, le=600)

    @model_validator(mode='after')
    def ordered(self) -> ShotEdit:
        if self.out_seconds is not None and self.out_seconds <= self.in_seconds:
            raise ValueError('Shot out point must be after its in point')
        if self.caption_end is not None and self.caption_end <= self.caption_start:
            raise ValueError('Caption end must be after its start')
        return self


class Soundtrack(StrictModel):
    asset_id: Key | None = None
    gain_db: float = Field(default=-14, ge=-40, le=6)
    duck: bool = True
    fade_out_seconds: float = Field(default=2, ge=0, le=15)


class CaptionStyle(StrictModel):
    font_size: int = Field(default=44, ge=12, le=200)
    position: Literal['bottom', 'top'] = 'bottom'


class Character(StrictModel):
    id: Key
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=4000)
    asset_ids: list[Key] = Field(default_factory=list, max_length=9)


class Shot(StrictModel):
    id: Key
    title: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=12000)
    character_ids: list[Key] = Field(default_factory=list, max_length=20)
    asset_ids: list[Key] = Field(default_factory=list, max_length=9)
    previous_shot_id: Key | None = None
    seed: int = Field(default=1024, ge=0, le=2**63 - 1)
    edit: ShotEdit = Field(default_factory=ShotEdit)


class Blueprint(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    brief: str = Field(default='', max_length=12000)
    style: str = Field(default='', max_length=4000)
    characters: list[Character] = Field(default_factory=list, max_length=30)
    shots: list[Shot] = Field(default_factory=list, max_length=32)
    recipe: Recipe = Field(default_factory=lambda: Recipe.model_validate({}))
    soundtrack: Soundtrack = Field(default_factory=Soundtrack)
    captions: CaptionStyle = Field(default_factory=CaptionStyle)

    @model_validator(mode='after')
    def relations(self) -> Blueprint:
        characters = {c.id for c in self.characters}
        shots = {s.id: s for s in self.shots}
        if len(characters) != len(self.characters) or len(shots) != len(self.shots):
            raise ValueError('Duplicate character or shot ID')
        for shot in self.shots:
            if not set(shot.character_ids) <= characters:
                raise ValueError(f'Shot {shot.id} uses an unknown character')
            seen = {shot.id}
            source = shot.previous_shot_id
            while source:
                if source not in shots:
                    raise ValueError('Unknown previous shot')
                if source in seen:
                    raise ValueError('Shot continuity dependency cycle')
                seen.add(source)
                source = shots[source].previous_shot_id
        return self


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def shot_fingerprints(plan: Blueprint, *, ignore_tier: bool = False) -> dict[str, str]:
    """Generation-input identity per shot. Editorial fields never enter it.

    With ``ignore_tier`` the key identifies the content regardless of
    preview/final canvas, which is how a final take is matched to the preview
    that was accepted for it.
    """
    characters = {c.id: c.model_dump() for c in plan.characters}
    shots = {s.id: s for s in plan.shots}
    keys: dict[str, str] = {}
    recipe = plan.recipe.model_dump(exclude={'tier'} if ignore_tier else None)

    def visit(key: str) -> str:
        if key not in keys:
            shot = shots[key]
            keys[key] = digest(
                dict(shot=shot.model_dump(exclude={'title', 'edit'}),
                     style=plan.style,
                     characters=[characters[c] for c in shot.character_ids],
                     recipe=recipe,
                     previous=visit(shot.previous_shot_id) if shot.previous_shot_id else None))
        return keys[key]

    for shot in plan.shots:
        visit(shot.id)
    return keys


def content_keys(plan: Blueprint) -> dict[str, str]:
    return shot_fingerprints(plan, ignore_tier=True)


def compile_jobs(plan: Blueprint, assets: dict, job_ids: dict[str, str]) -> dict[str, dict]:
    """Pure admission: validate the whole project before creating any GPU job."""
    characters = {c.id: c for c in plan.characters}
    recipe = effective_recipe(plan.recipe)
    output = {}
    for shot in plan.shots:
        character_assets = [asset for key in shot.character_ids for asset in characters[key].asset_ids]
        asset_ids = list(dict.fromkeys(character_assets + shot.asset_ids))
        if any(key not in assets or assets[key].get('kind', 'image') != 'image' for key in asset_ids):
            raise ValueError(f'Shot {shot.id} references an unknown or non-image asset')
        refs = [dict(source=assets[key]['path'], media_type='image', sha256=assets[key]['sha256']) for key in asset_ids]
        if shot.previous_shot_id:
            refs.append(dict(source=f'job-last-frame:{job_ids[shot.previous_shot_id]}', media_type='image'))
        if refs and plan.recipe.model_id != FULL_H3:
            raise ValueError('This project uses reference images. Select the full MiniMax H3 Ref2VA model; '
                             'FastH3 V2 cannot preserve these conditions.')
        if len(refs) > 9:
            raise ValueError('H3 supports at most nine image references per shot')
        identity = '\n'.join(f'{characters[key].name}: {characters[key].description}' for key in shot.character_ids)
        prompt = '\n'.join(part for part in (plan.style, identity, shot.prompt) if part)
        for index, asset_id in enumerate(asset_ids, 1):
            owners = [characters[key].name for key in shot.character_ids if asset_id in characters[key].asset_ids]
            prompt += f'\nReference image {index}: {", ".join(owners) or "scene/style reference"}. Preserve its appearance.'
        if shot.previous_shot_id:
            prompt += f'\nReference image {len(refs)} shows the preceding shot ending; maintain subject, setting and motion continuity.'
        output[shot.id] = dict(**recipe,
                               name=f'{plan.title} / {shot.title}'.strip(),
                               prompt=prompt.strip(),
                               seed=shot.seed,
                               references=refs,
                               job_type='inference',
                               workload_type='t2v')
    return output


def import_vimax(payload: dict) -> dict:
    """Import ViMax planning artifacts; missing image assets stay explicit."""
    characters = [
        dict(id=f'character-{c["idx"]}',
             name=c['identifier_in_scene'],
             description='; '.join(filter(
                 None, [c.get('static_features'), c.get('dynamic_features')])),
             asset_ids=[]) for c in payload.get('characters', [])
    ]
    shots = [
        dict(id=f'shot-{s["idx"]}',
             title=s.get('visual_desc', 'Shot')[:200],
             prompt='\n'.join(filter(
                 None, [s.get('visual_desc'), s.get('motion_desc'),
                        s.get('audio_desc')])),
             character_ids=[f'character-{i}' for i in s.get('ff_vis_char_idxs', [])])
        for s in payload.get('shot_descriptions', [])
    ]
    return Blueprint.model_validate(dict(title=payload.get('title', 'ViMax story'), characters=characters,
                                         shots=shots)).model_dump()


class AgentProjectStore:

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS agent_projects (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS agent_versions (project_id TEXT, revision INTEGER, data TEXT, '
                         'PRIMARY KEY(project_id, revision))')
            conn.execute('CREATE TABLE IF NOT EXISTS agent_assets (id TEXT PRIMARY KEY, project_id TEXT, data TEXT)')

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            conn.execute('BEGIN IMMEDIATE')
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _get(conn: sqlite3.Connection, project_id: str) -> dict[str, Any]:
        row = conn.execute('SELECT data FROM agent_projects WHERE id=?', (project_id, )).fetchone()
        if not row:
            raise KeyError('Project not found')
        project = json.loads(row[0])
        # Projects written before editorial/timing fields existed stay readable.
        plan = Blueprint.model_validate(project['blueprint'])
        project['blueprint'] = plan.model_dump()
        project.setdefault('content_keys', content_keys(plan))
        project.setdefault('timings', [])
        project.setdefault('renders', [])
        for takes in project['takes'].values():
            for take in takes:
                take.setdefault('tier', 'final')
                take.setdefault('content_key', None)
                take.setdefault('observation', None)
        return project

    @staticmethod
    def _save(conn: sqlite3.Connection, project: dict[str, Any]) -> None:
        conn.execute('INSERT OR REPLACE INTO agent_projects VALUES (?,?)',
                     (project['id'], json.dumps(project, ensure_ascii=False)))

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        plan = Blueprint.model_validate(payload)
        project = dict(id=uuid.uuid4().hex,
                       revision=1,
                       approved_revision=None,
                       blueprint=plan.model_dump(),
                       fingerprints=shot_fingerprints(plan),
                       content_keys=content_keys(plan),
                       takes={},
                       selected={},
                       stale_shots=[],
                       operations={},
                       created_at=time.time(),
                       messages=[],
                       timings=[],
                       renders=[])
        with self.connection() as conn:
            self._save(conn, project)
            conn.execute('INSERT INTO agent_versions VALUES (?,?,?)',
                         (project['id'], 1, json.dumps(project['blueprint'])))
        return project

    def get(self, project_id: str) -> dict[str, Any]:
        with self.connection() as conn:
            return self._get(conn, project_id)

    def list(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            return [json.loads(row[0]) for row in conn.execute('SELECT data FROM agent_projects ORDER BY rowid DESC')]

    def mutate(self, project_id: str, callback: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        with self.connection() as conn:
            project = self._get(conn, project_id)
            callback(project)
            self._save(conn, project)
            return project

    def update(self, project_id: str, payload: dict[str, Any], expected_revision: int) -> dict[str, Any]:
        plan = Blueprint.model_validate(payload)
        fingerprints = shot_fingerprints(plan)
        with self.connection() as conn:
            project = self._get(conn, project_id)
            if project['revision'] != expected_revision:
                raise ValueError('Project revision changed; reload before saving')
            changed = [key for key, value in fingerprints.items() if project['fingerprints'].get(key) != value]
            # Approval binds generation inputs. A purely editorial revision
            # (trims, captions, soundtrack) keeps it; any input change drops it.
            inputs_unchanged = fingerprints == project['fingerprints']
            carried = expected_revision + 1 if inputs_unchanged and project['approved_revision'] == expected_revision \
                else None
            project.update(revision=expected_revision + 1,
                           blueprint=plan.model_dump(),
                           approved_revision=carried,
                           fingerprints=fingerprints,
                           content_keys=content_keys(plan),
                           stale_shots=changed)
            project['selected'] = {
                key: value
                for key, value in project['selected'].items() if key in fingerprints and key not in changed
            }
            self._save(conn, project)
            conn.execute('INSERT INTO agent_versions VALUES (?,?,?)',
                         (project_id, project['revision'], json.dumps(plan.model_dump())))
            return project

    def approve(self, project_id: str, revision: int) -> dict[str, Any]:

        def change(project: dict[str, Any]) -> None:
            if project['revision'] != revision:
                raise ValueError('Project revision changed')
            if not project['blueprint']['shots']:
                raise ValueError('Add at least one shot before approval')
            project['approved_revision'] = revision

        return self.mutate(project_id, change)

    def add_take(self, project_id: str, shot_id: str, job_id: str, fingerprint: str) -> dict[str, Any]:

        def change(project: dict[str, Any]) -> None:
            if shot_id not in project['fingerprints']:
                raise ValueError('Unknown shot')
            records = project['takes'].setdefault(shot_id, [])
            if not any(take['job_id'] == job_id for take in records):
                records.append(
                    dict(job_id=job_id,
                         fingerprint=fingerprint,
                         review='pending',
                         note='',
                         tier=project['blueprint']['recipe']['tier'],
                         content_key=project['content_keys'].get(shot_id),
                         observation=None))
            if project['fingerprints'][shot_id] == fingerprint:
                project['selected'][shot_id] = job_id

        return self.mutate(project_id, change)

    def record_timing(self, project_id: str, kind: str, seconds: float, **fields: Any) -> dict[str, Any]:
        """Append one wall-clock measurement (planning, rendering, waiting)."""

        def change(project: dict[str, Any]) -> None:
            project['timings'].append(dict(kind=kind, seconds=round(float(seconds), 3), at=time.time(), **fields))
            del project['timings'][:-500]

        return self.mutate(project_id, change)

    def assets(self, project_id: str) -> dict[str, Any]:
        with self.connection() as conn:
            self._get(conn, project_id)
            return {
                row[0]: json.loads(row[1])
                for row in conn.execute('SELECT id,data FROM agent_assets WHERE project_id=?', (project_id, ))
            }

    def add_asset(self, project_id: str, data: dict[str, Any]) -> dict[str, Any]:
        with self.connection() as conn:
            self._get(conn, project_id)
            conn.execute('INSERT INTO agent_assets VALUES (?,?,?)', (data['id'], project_id, json.dumps(data)))
        return data
