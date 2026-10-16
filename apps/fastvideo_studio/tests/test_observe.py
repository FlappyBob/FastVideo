# SPDX-License-Identifier: Apache-2.0
"""A finished take becomes a compact, cached observation without a GPU."""
import json

import numpy as np
import pytest

from fastvideo_studio import observe
from fastvideo_studio.observe import ObservationError, observe_video, summarize
from fastvideo_studio.tests.test_merge import write_clip


def write_motion_clip(path, *, frames=48, cut_at=None, size=(128, 96)):
    """Moving gradient; optionally a hard cut to an inverted picture at ``cut_at``."""
    import av

    container = av.open(str(path), mode='w')
    video = container.add_stream('h264', rate=24)
    video.width, video.height, video.pix_fmt = size[0], size[1], 'yuv420p'
    for i in range(frames):
        x = (np.arange(size[0]) * 2 + i * 9) % 256
        array = np.repeat(x[None, :, None], size[1], axis=0).repeat(3, axis=2).astype(np.uint8)
        if cut_at is not None and i >= cut_at:
            array = 255 - array
        for packet in video.encode(av.VideoFrame.from_ndarray(array, format='rgb24')):
            container.mux(packet)
    for packet in video.encode():
        container.mux(packet)
    container.close()


def test_static_clip_is_flagged_and_cached(tmp_path):
    clip = tmp_path / 'still.mp4'
    write_clip(str(clip), frames=48, color='blue')
    first = observe_video(str(clip), str(tmp_path / 'cache'), expected_frames=48)
    assert 'mostly_static' in first['flags']
    assert 'frame_count_mismatch' not in first['flags']
    assert first['probe']['width'] == 128 and first['motion']['frames'] == 48
    assert first['audio'] is not None and len(first['keyframe_times']) == 8
    assert (tmp_path / 'cache').glob('*.jpg')
    second = observe_video(str(clip), str(tmp_path / 'cache'), expected_frames=48)
    assert second == first
    summary = summarize(first)
    assert summary['seconds'] == pytest.approx(2.0, abs=0.1)
    assert json.dumps(summary)


def test_hidden_cut_and_missing_audio_are_reported(tmp_path):
    clip = tmp_path / 'cut.mp4'
    write_motion_clip(clip, frames=48, cut_at=24)
    record = observe_video(str(clip), str(tmp_path / 'cache'), expected_frames=50)
    assert 'possible_hidden_cut' in record['flags']
    assert record['motion']['jumps_at'] == [1.0]
    assert 'no_audio_track' in record['flags']
    assert 'frame_count_mismatch' in record['flags']
    assert 'mostly_static' not in record['flags']


def test_missing_media_is_a_client_error(tmp_path):
    with pytest.raises(ObservationError) as info:
        observe_video(str(tmp_path / 'absent.mp4'), str(tmp_path / 'cache'))
    assert info.value.status_code == 404
    assert observe.OBSERVATION_VERSION >= 1
