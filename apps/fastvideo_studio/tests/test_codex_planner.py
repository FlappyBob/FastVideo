# SPDX-License-Identifier: Apache-2.0
import json
import sys

import pytest

from fastvideo_studio.agent_planner import load_planner


def executable(tmp_path, body):
    path = tmp_path / 'codex'
    path.write_text('#!' + sys.executable + '\n' + body)
    path.chmod(0o700)
    return str(path)


def configured(tmp_path, program, **options):
    config = tmp_path / 'agent.json'
    config.write_text(json.dumps({'provider': 'codex', 'executable': program, **options}))
    return load_planner(str(config))


def test_codex_provider_returns_structured_story_without_hardware_inputs(tmp_path):
    program = executable(tmp_path, '''import json,sys
from pathlib import Path
args=sys.argv[1:]
assert '--ignore-user-config' in args and '--ephemeral' in args
assert args[args.index('--sandbox')+1]=='read-only'
assert 'shell_tool' in args and 'multi_agent' in args
prompt=sys.stdin.read()
assert 'num_gpus' not in prompt and 'Plan a lighthouse film' in prompt
schema=json.loads(Path(args[args.index('--output-schema')+1]).read_text())
assert 'recipe' not in schema['properties']
assert set(schema['required'])==set(schema['properties'])
Path(args[args.index('--output-last-message')+1]).write_text(json.dumps({
 'title':'Lighthouse','shots':[{'id':'a','title':'Arrival','prompt':'A yellow robot arrives'}]}))
''')
    planner = configured(tmp_path, program)
    result = planner({'title': 'Story', 'recipe': {'num_gpus': 8}},
                     'Plan a lighthouse film', None, assets={})
    assert result['shots'][0]['prompt'] == 'A yellow robot arrives'


def test_codex_failure_does_not_expose_raw_output_or_claim_success(tmp_path):
    program = executable(tmp_path, 'import sys\nprint("secret-token",file=sys.stderr)\nsys.exit(1)\n')
    planner = configured(tmp_path, program)
    with pytest.raises(RuntimeError, match='Codex') as error:
        planner({}, 'plan', None, assets={})
    assert 'secret-token' not in str(error.value)


def test_codex_timeout_is_bounded(tmp_path):
    program = executable(tmp_path, 'import time\ntime.sleep(20)\n')
    planner = configured(tmp_path, program, timeout_seconds=1)
    with pytest.raises(RuntimeError, match='timed out'):
        planner({}, 'plan', None, assets={})


def test_codex_invalid_json_is_explicit(tmp_path):
    program = executable(tmp_path, '''import sys
from pathlib import Path
Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text('not json')
''')
    with pytest.raises(ValueError, match='JSON'):
        configured(tmp_path, program)({}, 'plan', None, assets={})


def test_codex_cut_mode_uses_editorial_schema_and_attaches_keyframes(tmp_path):
    from PIL import Image
    sheet = tmp_path / 'sheet.jpg'
    Image.new('RGB', (8, 8)).save(sheet)
    program = executable(tmp_path, '''import json,sys
from pathlib import Path
args=sys.argv[1:]
prompt=sys.stdin.read()
assert 'film editor' in prompt and '"take_seconds"' in prompt
images=[args[i+1] for i,a in enumerate(args) if a=='--image']
assert len(images)==1 and images[0].endswith('.jpg') and Path(images[0]).is_file()
schema=json.loads(Path(args[args.index('--output-schema')+1]).read_text())
assert 'edit' in schema['properties']['shots']['items']['properties']
assert 'asset_id' not in schema['properties']['soundtrack']['properties']
Path(args[args.index('--output-last-message')+1]).write_text(json.dumps({
 'shots':[{'id':'a','edit':{'out_seconds':3}}],'soundtrack':{'gain_db':-12},'rationale':'trim'}))
''')
    planner = configured(tmp_path, program)
    result = planner({'title': 'Story', 'recipe': {}, 'shots': [{'id': 'a'}]}, 'tighten', None, assets={},
                     observations={'a': [{'job_id': 'j', 'selected': True, 'status': 'completed', 'review': 'pending',
                                          'note': '', 'tier': 'final', 'observation': {'seconds': 5},
                                          'keyframes_path': str(sheet)}]},
                     mode='cut', durations={'a': 5.0})
    assert result['shots'][0]['edit']['out_seconds'] == 3
