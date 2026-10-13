# SPDX-License-Identifier: Apache-2.0
"""Long productions need truthful stages and stable log cursors."""
from fastvideo_studio.job_runner import JobLogBuffer


def test_checkpoint_name_and_schedule_are_not_training_progress():
    buffer = JobLogBuffer()
    buffer.phase = 'loading model'
    buffer.write('Loading FastVideo/FastVideo-FastH3-8-Step-V2')
    assert buffer.phase == 'loading model'
    buffer.write('FastH3 checkpoint schedule: 8 transformer forwards, video/audio shifts=10.0/3.0')
    assert buffer.progress == 0
    assert buffer.progress_msg == ''
    assert buffer.phase == 'loading model'


def test_stage_start_clears_previous_phase_progress():
    buffer = JobLogBuffer()
    buffer.write('Loading safetensors checkpoint shards: 100%|########| 14/14 [00:04<00:00]')
    assert buffer.progress == 100
    buffer.write('[denoising_stage|MiniMaxH3DenoisingStage] Starting execution')
    assert buffer.phase == 'denoising'
    assert buffer.progress == 0
    assert buffer.progress_msg == ''
    buffer.write('[video_decoding_stage|MiniMaxH3VideoDecodingStage] Starting execution')
    assert buffer.phase == 'video decoding'
    buffer.write('[audio_decoding_stage|MiniMaxH3AudioDecodingStage] Starting execution')
    assert buffer.phase == 'audio decoding'


def test_real_progress_bars_and_training_steps_are_preserved():
    buffer = JobLogBuffer()
    buffer.write('Denoising: 40%|####      | 20/50 [00:01<00:02]')
    assert buffer.progress == 40
    assert buffer.phase == 'denoising'
    buffer.write('Training step 12/100 loss=0.42')
    assert buffer.progress == 12
    assert buffer.progress_msg == '12/100 steps'
    assert buffer.phase == 'training'


def test_log_cursor_advances_past_ring_capacity():
    buffer = JobLogBuffer(maxlen=3)
    buffer.write('a\nb\nc')
    lines, cursor = buffer.get_lines()
    assert (lines, cursor) == (['a', 'b', 'c'], 3)
    buffer.write('d\ne')
    assert buffer.get_lines(cursor) == (['d', 'e'], 5)
    assert buffer.get_lines(0) == (['c', 'd', 'e'], 5)
    assert buffer.get_lines(5) == ([], 5)
