# SPDX-License-Identifier: Apache-2.0
"""Optional bounded Codex CLI planner using the local account's existing login."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .agent_project import Blueprint
from .agent_planner import _SYSTEM


def _strict_schema(node: Any) -> Any:
    if isinstance(node, list):
        return [_strict_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    result = {key: _strict_schema(value) for key, value in node.items() if key != 'default'}
    if result.get('type') == 'object':
        result['additionalProperties'] = False
        result['required'] = list(result.get('properties', {}))
    return result


class CodexPlanner:
    """Each call is a fresh structured planning turn, not a resumed chat thread."""

    def __init__(self, settings: dict[str, Any]):
        executable = shutil.which(str(settings.get('executable', 'codex')))
        if not executable:
            raise ValueError('Codex CLI is unavailable; install it and run codex login first')
        self.executable = str(Path(executable).resolve())
        self.model = settings.get('model')
        self.timeout = float(settings.get('timeout_seconds', 180))
        if not 1 <= self.timeout <= 900:
            raise ValueError('Codex timeout_seconds must be between 1 and 900')

    def __call__(self, current: dict, instruction: str, target: str | None, *, assets: dict) -> dict:
        creative = {key: value for key, value in current.items() if key != 'recipe'}
        catalog = [{key: value for key, value in asset.items() if key != 'path'} for asset in assets.values()]
        prompt = _SYSTEM + '\nDo not use tools. Return only the storyboard JSON.\n' + json.dumps(
            dict(current=creative, instruction=instruction, target_shot_id=target, assets=catalog), ensure_ascii=False)
        schema = Blueprint.model_json_schema()
        schema['properties'].pop('recipe')
        schema.get('$defs', {}).pop('Recipe', None)
        with tempfile.TemporaryDirectory(prefix='studio-codex-plan-') as directory:
            root = Path(directory)
            schema_path, answer = root / 'schema.json', root / 'answer.json'
            schema_path.write_text(json.dumps(_strict_schema(schema)))
            command = [
                self.executable,
                'exec',
                '--ignore-user-config',
                '--ephemeral',
                '--skip-git-repo-check',
                '--sandbox',
                'read-only',
                '-c',
                'approval_policy="never"',
                '-c',
                'web_search="disabled"',
                '--output-schema',
                str(schema_path),
                '--output-last-message',
                str(answer),
            ]
            for feature in ('shell_tool', 'multi_agent', 'apps', 'browser_use', 'computer_use', 'skill_search'):
                command.extend(['--disable', feature])
            if self.model:
                command.extend(['--model', str(self.model)])
            for index, asset in enumerate(list(assets.values())[:9]):
                # Supply only explicit project assets, without repository context.
                image = root / f'reference-{index}.png'
                shutil.copyfile(asset['path'], image)
                command.extend(['--image', str(image)])
            command.append('-')
            try:
                result = subprocess.run(command,
                                        input=prompt,
                                        text=True,
                                        cwd=root,
                                        timeout=self.timeout,
                                        stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL,
                                        check=False)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError('Codex planning timed out; no storyboard was changed') from exc
            except OSError as exc:
                raise RuntimeError('Could not start Codex planner; check the server CLI installation') from exc
            if result.returncode or not answer.is_file():
                raise RuntimeError('Codex planning failed; check codex login status and model access on the server')
            try:
                proposal = json.loads(answer.read_text())
                if not isinstance(proposal, dict):
                    raise ValueError()
            except (ValueError, OSError) as exc:
                raise ValueError('Codex did not return a valid JSON storyboard') from exc
            return proposal
