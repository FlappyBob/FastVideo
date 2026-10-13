# SPDX-License-Identifier: Apache-2.0
"""Shared real/mock scene API. Callbacks return a Database and iterable job dicts."""
from collections.abc import Callable, Iterable
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .scene_store import SceneStore


class SceneClipRequest(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=500)
    job_ids: list[str]
    selected_job_id: str | None = None


class SceneRequest(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    clips: list[SceneClipRequest] = Field(default_factory=list)


def build_scene_router(get_database: Callable[[], Any], get_jobs: Callable[[], Iterable[dict]]) -> APIRouter:
    router = APIRouter(prefix='/api/scenes')

    def store() -> SceneStore:
        return SceneStore(get_database()._path)

    def jobs() -> dict[str, dict]:
        return {job['id']: job for job in get_jobs()}

    @router.get('')
    def list_scenes():
        return store().list_scenes()

    @router.post('/import-legacy')
    def import_scenes():
        return store().import_legacy(jobs())

    def save(payload: SceneRequest, scene_id: str | None = None):
        try:
            return store().save(payload.model_dump(), jobs(), scene_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='Scene not found') from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post('')
    def create_scene(payload: SceneRequest):
        return save(payload)

    @router.put('/{scene_id}')
    def update_scene(scene_id: str, payload: SceneRequest):
        return save(payload, scene_id)

    return router
