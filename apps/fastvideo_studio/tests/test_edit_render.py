# SPDX-License-Identifier: Apache-2.0
"""Editorial decisions render to one film: trims, transitions, captions, soundtrack."""
import os

import numpy as np
import pytest

from fastvideo_studio.edit_render import (CutSpec, Segment, SoundtrackSpec, build_command, find_caption_font,
                                          render_cut, timeline, total_seconds)
from fastvideo_studio.merge import MergeError, find_ffmpeg, probe_clip
from fastvideo_studio.tests.test_merge import write_clip

pytestmark = pytest.mark.skipif(find_ffmpeg() is None, reason='needs an ffmpeg binary')


def segments():
    return [
        Segment(path='/a.mp4', label='A', source_seconds=5, has_audio=True, out_seconds=3),
        Segment(path='/b.mp4', label='B', source_seconds=5, has_audio=True, in_seconds=1, transition='crossfade',
                transition_seconds=0.5, caption='你好 world'),
        Segment(path='/c.mp4', label='C', source_seconds=5, has_audio=False, transition='fade_black',
                transition_seconds=1),
    ]


def test_timeline_overlaps_transitions_and_command_is_well_formed():
    spec = CutSpec(segments=segments(), width=128, height=96, fps=24, font_path='/f.ttf',
                   temp_files=['/t0', '/t1', '/t2'])
    placed = timeline(spec)
    assert [p['start'] for p in placed] == [0, 2.5, 5.5]
    assert total_seconds(spec) == 10.5
    command = build_command(spec, '/out.mp4', 'ffmpeg')
    graph = command[command.index('-filter_complex') + 1]
    assert 'xfade=transition=fade:duration=0.500:offset=2.500' in graph
    assert 'xfade=transition=fadeblack:duration=1.000:offset=5.500' in graph
    assert 'anullsrc' in graph and "textfile='/t1'" in graph
    assert command[-1] == '/out.mp4' and '-map' in command


def test_invalid_ranges_are_refused():
    bad = CutSpec(segments=[Segment(path='/a', label='A', source_seconds=2, has_audio=True, in_seconds=3)],
                  width=16, height=16, fps=24)
    with pytest.raises(MergeError, match='past the end'):
        build_command(bad, '/o', 'ffmpeg')
    with pytest.raises(MergeError, match='No shots'):
        build_command(CutSpec(segments=[], width=16, height=16, fps=24), '/o', 'ffmpeg')


def write_tone(path, seconds=3.0, rate=32000):
    import av

    container = av.open(str(path), mode='w')
    stream = container.add_stream('aac', rate=rate)
    stream.layout = 'stereo'
    total = int(seconds * rate)
    tone = (np.sin(np.arange(total) * 2 * np.pi * 220 / rate) * 0.3).astype(np.float32)
    pts = 0
    for start in range(0, total, 1024):
        chunk = np.pad(tone[start:start + 1024], (0, max(0, 1024 - len(tone[start:start + 1024]))))
        frame = av.AudioFrame.from_ndarray(np.stack([chunk, chunk]), format='fltp', layout='stereo')
        frame.sample_rate, frame.pts = rate, pts
        pts += 1024
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def test_real_render_with_trim_fade_caption_and_music(tmp_path):
    paths = {}
    for shot, color in (('a', 'red'), ('b', 'green'), ('c', 'blue')):
        paths[shot] = str(tmp_path / f'{shot}.mp4')
        write_clip(paths[shot], frames=48, color=color, audio=shot != 'c')
    music = tmp_path / 'music.m4a'
    write_tone(music)
    shots = [
        dict(id='a', title='Open', edit=dict(out_seconds=1.5)),
        dict(id='b', title='Middle', edit=dict(transition='crossfade', transition_seconds=0.5, caption='字幕 caption')),
        dict(id='c', title='End', edit=dict(in_seconds=0.5, transition='fade_black', transition_seconds=0.5)),
    ]
    result = render_cut(shots, paths, str(tmp_path / 'out'), name='Demo film',
                        soundtrack=SoundtrackSpec(path=str(music), gain_db=-10), job_ids=['j1', 'j2', 'j3'])
    assert os.path.isfile(result.path) and result.clips == 3
    assert result.seconds == pytest.approx(1.5 + 2 - 0.5 + 1.5 - 0.5, abs=0.01)
    info = probe_clip(result.path)
    assert (info.width, info.height) == (128, 96) and info.audio is not None
    assert info.video_seconds == pytest.approx(result.seconds, abs=0.15)
    assert [t['start'] for t in result.timeline] == [0, 1.0, 2.5]


def test_caption_requires_font(tmp_path, monkeypatch):
    monkeypatch.delenv('FASTVIDEO_CAPTION_FONT', raising=False)
    assert find_caption_font() is None or os.path.isfile(find_caption_font())
    spec = CutSpec(segments=[Segment(path='/a', label='A', source_seconds=2, has_audio=True, caption='x')],
                   width=16, height=16, fps=24, font_path=None, temp_files=['/t'])
    with pytest.raises(MergeError, match='font'):
        build_command(spec, '/o', 'ffmpeg')
