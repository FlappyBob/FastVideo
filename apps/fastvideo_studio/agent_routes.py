# SPDX-License-Identifier: Apache-2.0
"""Story-agent HTTP surface shared by real and explicitly simulated servers."""
from __future__ import annotations

import hashlib
import io
import uuid
from pathlib import Path
from typing import Any
from collections.abc import Callable

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field

from .agent_project import Blueprint, FULL_H3, import_vimax


class RevisionRequest(BaseModel):
    revision: int = Field(ge=1)


class UpdateBlueprint(RevisionRequest):
    blueprint: Blueprint


class PlanRequest(RevisionRequest):
    instruction: str = Field(min_length=1, max_length=12000)
    target_shot: str | None = None


class GenerateRequest(RevisionRequest):
    max_new_jobs: int = Field(default=3, ge=0, le=32)


class ReviewRequest(BaseModel):
    shot_id: str
    job_id: str
    verdict: str
    note: str = Field(default='', max_length=4000)


class ReferenceRequest(RevisionRequest):
    character_id: str


class ImportRequest(RevisionRequest):
    payload: dict


def _call(callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return callback(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(404, 'Project or asset not found') from exc
    except ValueError as exc:
        raise HTTPException(409 if 'revision' in str(exc).lower() else 422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


def build_agent_router(get_service, assets_dir, *, mock=False):
    router = APIRouter(prefix='/api/agent', tags=['story-agent'])

    @router.get('/config')
    def config():
        service = get_service()
        return dict(image_generator_configured=service.image_generator is not None,
                    planner_configured=service.planner is not None,
                    mock=mock,
                    reference_model=FULL_H3,
                    models=service.model_ids(),
                    max_shots=32)

    @router.get('/projects')
    def projects():
        return get_service().store.list()

    @router.post('/projects', status_code=201)
    def create(request: Blueprint):
        return get_service().store.create(request.model_dump())

    @router.get('/projects/{project_id}')
    def get(project_id: str):
        return _call(get_service().get, project_id)

    @router.put('/projects/{project_id}')
    def update(project_id: str, request: UpdateBlueprint):
        return _call(get_service().update, project_id, request.blueprint.model_dump(), request.revision)

    @router.post('/projects/{project_id}/approve')
    def approve(project_id: str, request: RevisionRequest):
        return _call(get_service().approve, project_id, request.revision)

    @router.post('/projects/{project_id}/generate')
    def generate(project_id: str, request: GenerateRequest):
        return _call(get_service().generate, project_id, request.revision, request.max_new_jobs)

    @router.post('/projects/{project_id}/plan')
    def plan(project_id: str, request: PlanRequest):
        return _call(get_service().plan, project_id, request.revision, request.instruction, request.target_shot)

    @router.post('/projects/{project_id}/import-vimax')
    def import_plan(project_id: str, request: ImportRequest):
        service = get_service()
        old = _call(service.store.get, project_id)
        blueprint = _call(import_vimax, request.payload)
        blueprint['recipe'] = old['blueprint']['recipe']
        return _call(service.update, project_id, blueprint, request.revision)

    @router.post('/projects/{project_id}/review')
    def review(project_id: str, request: ReviewRequest):
        return _call(get_service().review, project_id, request.shot_id, request.job_id, request.verdict, request.note)

    @router.get('/projects/{project_id}/export-jobs')
    def export(project_id: str):
        return dict(job_ids=_call(get_service().export_jobs, project_id))

    def store_image(project_id: str, raw: bytes, name: str):
        service = get_service()
        _call(service.store.get, project_id)
        if len(raw) > 15 * 1024 * 1024:
            raise HTTPException(413, 'Reference image exceeds 15 MiB')
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.width * image.height > 16_000_000 or image.format not in {'PNG', 'JPEG', 'WEBP'}:
                    raise ValueError('Use PNG, JPEG or WebP at no more than 16 megapixels')
                image.load()
                converted = image.convert('RGB')
                output = io.BytesIO()
                converted.save(output, format='PNG')
                data = output.getvalue()
                width, height = image.size
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise HTTPException(422, 'Invalid reference image') from exc
        asset_id = uuid.uuid4().hex
        directory = Path(assets_dir() if callable(assets_dir) else assets_dir) / project_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f'{asset_id}.png'
        path.write_bytes(data)
        asset = dict(id=asset_id,
                     name=name[:200],
                     path=str(path.resolve()),
                     sha256=hashlib.sha256(data).hexdigest(),
                     width=width,
                     height=height)
        try:
            service.store.add_asset(project_id, asset)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return {key: value for key, value in asset.items() if key != 'path'}

    @router.post('/projects/{project_id}/assets', status_code=201)
    async def upload(project_id: str, file: UploadFile):
        raw = await file.read(15 * 1024 * 1024 + 1)
        return store_image(project_id, raw, file.filename or 'Reference')

    @router.post('/projects/{project_id}/references/generate')
    def generate_reference(project_id: str, request: ReferenceRequest):
        service = get_service()
        if service.image_generator is None:
            raise HTTPException(503, 'Image provider is not configured; upload a reference instead')
        project = _call(service.store.get, project_id)
        if project['revision'] != request.revision:
            raise HTTPException(409, 'Project revision changed before image generation')
        _call(service._editable, project)
        character = next((c for c in project['blueprint']['characters'] if c['id'] == request.character_id), None)
        if character is None:
            raise HTTPException(422, 'Unknown character')
        prompt = f"{project['blueprint']['style']}\nCharacter reference portrait: {character['name']}. "
        prompt += character['description'] + '. Clear appearance and wardrobe, neutral pose, no text or labels.'
        raw = _call(service.image_generator, prompt)
        asset = store_image(project_id, raw, character['name'])
        character['asset_ids'].append(asset['id'])
        # A concurrent edit keeps the new image in the library, but must not
        # attach it to a character whose definition has since changed.
        return _call(service.update, project_id, project['blueprint'], request.revision)

    @router.get('/projects/{project_id}/assets/{asset_id}')
    def asset(project_id: str, asset_id: str):
        assets = _call(get_service().store.assets, project_id)
        if asset_id not in assets or not Path(assets[asset_id]['path']).is_file():
            raise HTTPException(404, 'Asset not found')
        return FileResponse(assets[asset_id]['path'], media_type='image/png')

    return router
