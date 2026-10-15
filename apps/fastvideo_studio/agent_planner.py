# SPDX-License-Identifier: Apache-2.0
"""Configurable story planner. Model output is a proposal, never an execution command."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

_SYSTEM = '''You are a film storyboard planner. Return one JSON object, without markdown:
{title, brief, style, characters:[{id,name,description,asset_ids:[]}],
 shots:[{id,title,prompt,character_ids:[],asset_ids:[],previous_shot_id:null,seed:1024}]}.
Use stable ASCII IDs. Preserve existing identities, appearance, wardrobe, location, time of day,
and screen direction unless the instruction explicitly changes them. Each shot must advance the story.
Describe composition, action, camera movement and sound. Usually plan 3-6 short shots unless instructed otherwise.
Maximum 32 shots. Use only asset IDs in the supplied catalog. Empty asset_ids means there is no visual reference,
not that a character is visually locked. previous_shot_id uses a preceding shot's ending as a visual reference;
it does not guarantee an exact first frame. Dependencies must be acyclic. Include sufficient independent
establishing/context shots; do not turn every cut into one unbroken camera movement.
When revising one target shot, preserve its ID and all other shots and characters. Never propose shell commands,
API URLs, filesystem paths, hardware configuration or model settings. The result must remain editable.
If a request cannot be represented, return {"error":"a clear reason"} rather than claiming success.'''


class StoryPlanner:

    def __init__(self, settings: dict, *, transport=None):
        self.base_url = str(settings.get('base_url', '')).rstrip('/')
        self.model = str(settings.get('model', ''))
        self.api_key = str(settings.get('api_key', ''))
        self.transport = transport
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Planner base_url must be an HTTP URL without embedded credentials')
        if not self.model or '<' in self.model:
            raise ValueError('Configure a real planner model')

    def __call__(self, current: dict, instruction: str, target: str | None, *, assets: dict):
        creative = {key: value for key, value in current.items() if key != 'recipe'}
        catalog = [{key: value for key, value in asset.items() if key != 'path'} for asset in assets.values()]
        content: list[dict[str, Any]] = [
            dict(type='text',
                 text=json.dumps(dict(current=creative, instruction=instruction, target_shot_id=target, assets=catalog),
                                 ensure_ascii=False))
        ]
        # Bounded image context; remaining assets retain their descriptive catalog.
        for asset in list(assets.values())[:9]:
            content.append(dict(type='text', text=f'Reference asset {asset["id"]}: {asset["name"]}'))
            data = base64.b64encode(Path(asset['path']).read_bytes()).decode()
            content.append(dict(type='image_url', image_url=dict(url=f'data:image/png;base64,{data}')))
        headers = {'Authorization': f'Bearer {self.api_key}'} if self.api_key else {}
        try:
            with httpx.Client(timeout=120, transport=self.transport) as client:
                response = client.post(
                    self.base_url + '/chat/completions',
                    headers=headers,
                    json=dict(model=self.model,
                              messages=[dict(role='system', content=_SYSTEM),
                                        dict(role='user', content=content)],
                              response_format=dict(type='json_object'),
                              max_tokens=6000))
                if response.status_code != 200:
                    raise RuntimeError(f'Planner returned HTTP {response.status_code}; no storyboard was changed')
                text = response.json()['choices'][0]['message']['content']
        except httpx.HTTPError as exc:
            raise RuntimeError('Planner connection failed; no storyboard was changed') from exc
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError('Planner response did not contain a JSON message') from exc
        try:
            proposed = json.loads(text)
            if not isinstance(proposed, dict):
                raise ValueError()
        except (ValueError, TypeError) as exc:
            raise ValueError('Planner did not return valid JSON; no storyboard was changed') from exc
        if proposed.get('error'):
            raise ValueError(str(proposed['error'])[:1000])
        return proposed


def load_planner(path: str):
    if not path:
        return None
    settings = json.loads(Path(path).read_text())
    return StoryPlanner(settings)


class ReferenceGenerator:
    """One explicit image request; never retries a potentially billable POST."""

    def __init__(self, settings: dict, *, transport=None):
        self.base_url = str(settings.get('base_url', '')).rstrip('/')
        self.model = str(settings.get('model', ''))
        self.api_key = str(settings.get('api_key', ''))
        self.transport = transport
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {'http', 'https'
                                 } or not parsed.hostname or parsed.username or parsed.password or not self.model:
            raise ValueError('Configure an image endpoint and model without embedded credentials')

    def __call__(self, prompt: str) -> bytes:
        headers = {'Authorization': f'Bearer {self.api_key}'} if self.api_key else {}
        try:
            with httpx.Client(timeout=180, transport=self.transport) as client:
                response = client.post(self.base_url + '/images/generations',
                                       headers=headers,
                                       json=dict(model=self.model,
                                                 prompt=prompt,
                                                 n=1,
                                                 size='1024x1024',
                                                 response_format='b64_json'))
                if response.status_code != 200:
                    raise RuntimeError(f'Image provider returned HTTP {response.status_code}; no automatic retry')
                encoded = response.json()['data'][0]['b64_json']
                if not isinstance(encoded, str) or len(encoded) > 22 * 1024 * 1024:
                    raise ValueError('Image response is too large or not base64 data')
                return base64.b64decode(encoded, validate=True)
        except httpx.HTTPError as exc:
            raise RuntimeError('Image request outcome is unknown; check the provider before retrying') from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValueError('Image provider must return valid b64_json data') from exc


def load_image_generator(path: str):
    if not path:
        return None
    settings = json.loads(Path(path).read_text()).get('image')
    return ReferenceGenerator(settings) if settings else None
