# SPDX-License-Identifier: Apache-2.0
"""What a finished take actually looks and sounds like, for people and planners.

A planner that only ever reads prompts cannot tell a static frame from a
performance, a hidden cut from a continuous move, or silence from ambience.
This module turns one rendered clip into a compact observation: stream
metadata, a per-second motion profile, an audio level profile, a contact
sheet of timestamped key frames, and a few conservative flags. Flags are
hints for review; nothing here accepts or rejects a take.

Needs only PyAV, numpy and Pillow, so it runs without a GPU and is cached by
the source file's identity, like ``frames.py``.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import numpy as np

from fastvideo_studio.media import atomic_output

OBSERVATION_VERSION = 2
SHEET_COLUMNS = 4
SHEET_FRAMES = 8
SHEET_TILE_WIDTH = 336
STATIC_MOTION = 1.5  # mean |Δ| of 8-bit luma between consecutive samples
JUMP_MOTION = 45.0  # above this a sampled transition looks like a hard cut
SILENCE_DBFS = -55.0


class ObservationError(Exception):

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _luma(frame: Any, width: int = 160) -> np.ndarray:
    image = frame.to_image().convert('L')
    height = max(1, round(image.height * width / image.width))
    return np.asarray(image.resize((width, height)), dtype=np.float32)


def probe_media(path: str) -> dict[str, Any]:
    """Stream-level facts a planner or reviewer needs before watching."""
    import av

    with av.open(path) as container:
        video = next((s for s in container.streams if s.type == 'video'), None)
        if video is None:
            raise ValueError('No video stream')
        audio = next((s for s in container.streams if s.type == 'audio'), None)
        fps = float(video.average_rate or video.guessed_rate or 0)
        seconds = float(video.duration * video.time_base) if video.duration is not None else (
            container.duration / 1_000_000 if container.duration else 0.0)
        result = dict(width=video.codec_context.width,
                      height=video.codec_context.height,
                      fps=round(fps, 3),
                      video_seconds=round(seconds, 3),
                      video_codec=video.codec_context.name,
                      audio=None)
        if audio is not None:
            audio_seconds = float(audio.duration * audio.time_base) if audio.duration is not None else seconds
            result['audio'] = dict(codec=audio.codec_context.name,
                                   sample_rate=audio.codec_context.sample_rate,
                                   channels=audio.codec_context.channels,
                                   seconds=round(audio_seconds, 3))
    return result


def motion_profile(path: str) -> dict[str, Any]:
    """Per-second luma change; also counts frames and detects hard jumps."""
    import av

    per_second: list[float] = []
    bucket: list[float] = []
    previous = None
    frames = 0
    jumps: list[float] = []
    with av.open(path) as container:
        stream = container.streams.video[0]
        fps = float(stream.average_rate or stream.guessed_rate or 24)
        for frame in container.decode(stream):
            current = _luma(frame)
            if previous is not None and current.shape == previous.shape:
                delta = float(np.abs(current - previous).mean())
                bucket.append(delta)
                if delta >= JUMP_MOTION:
                    jumps.append(round(frames / fps, 2))
            previous = current
            frames += 1
            if len(bucket) >= round(fps):
                per_second.append(round(float(np.mean(bucket)), 3))
                bucket = []
    if bucket:
        per_second.append(round(float(np.mean(bucket)), 3))
    if frames == 0:
        raise ValueError('The video has no frames.')
    static = [index for index, value in enumerate(per_second) if value < STATIC_MOTION]
    return dict(frames=frames,
                decoded_seconds=round(frames / fps, 3),
                per_second=per_second,
                mean=round(float(np.mean(per_second)), 3) if per_second else 0.0,
                static_seconds=static,
                jumps_at=jumps[:20])


def audio_profile(path: str) -> dict[str, Any] | None:
    """Per-second RMS level in dBFS, or None when the clip has no audio."""
    import av

    with av.open(path) as container:
        stream = next((s for s in container.streams if s.type == 'audio'), None)
        if stream is None:
            return None
        rate = stream.codec_context.sample_rate or 48000
        chunks: list[np.ndarray] = []
        for frame in container.decode(stream):
            array = frame.to_ndarray()
            if array.dtype.kind in 'iu':
                array = array.astype(np.float32) / float(np.iinfo(array.dtype).max)
            if array.ndim == 2:  # planar: channels x samples; packed: 1 x (samples*channels)
                if frame.format.is_planar:
                    array = array.mean(axis=0)
                else:
                    channels = max(1, len(frame.layout.channels))
                    array = array.reshape(-1, channels).mean(axis=1)
            chunks.append(array.astype(np.float32))
    if not chunks:
        return dict(per_second=[], silent_seconds=[], peak_dbfs=None)
    samples = np.concatenate(chunks)
    per_second = []
    for start in range(0, len(samples), rate):
        window = samples[start:start + rate]
        rms = float(np.sqrt(np.mean(window**2))) if len(window) else 0.0
        per_second.append(round(20 * np.log10(max(rms, 1e-6)), 1))
    peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
    return dict(per_second=per_second,
                silent_seconds=[index for index, level in enumerate(per_second) if level < SILENCE_DBFS],
                peak_dbfs=round(20 * np.log10(max(peak, 1e-6)), 1))


def _font(size: int):
    from PIL import ImageFont

    for candidate in ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', ):
        if os.path.isfile(candidate):
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def contact_sheet(path: str, out_path: str, *, count: int = SHEET_FRAMES, columns: int = SHEET_COLUMNS,
                  tile_width: int = SHEET_TILE_WIDTH) -> list[float]:
    """Evenly spaced, timestamped frames on one JPEG. Returns the sampled times."""
    import av
    from PIL import Image, ImageDraw

    with av.open(path) as container:
        stream = container.streams.video[0]
        fps = float(stream.average_rate or stream.guessed_rate or 24)
        frames = list(container.decode(stream))
    if not frames:
        raise ValueError('The video has no frames.')
    count = max(1, min(count, len(frames)))
    positions = [round(index * (len(frames) - 1) / max(1, count - 1)) for index in range(count)]
    first = frames[0].to_image()
    tile_height = max(1, round(first.height * tile_width / first.width))
    rows = -(-count // columns)
    sheet = Image.new('RGB', (tile_width * min(columns, count), tile_height * rows), 'black')
    draw = ImageDraw.Draw(sheet)
    font = _font(max(12, tile_width // 16))
    times = []
    for slot, index in enumerate(positions):
        tile = frames[index].to_image().resize((tile_width, tile_height))
        x, y = (slot % columns) * tile_width, (slot // columns) * tile_height
        sheet.paste(tile, (x, y))
        seconds = index / fps
        times.append(round(seconds, 2))
        label = f'{seconds:5.2f}s  #{index}'
        box = draw.textbbox((x + 6, y + 4), label, font=font)
        draw.rectangle((box[0] - 4, box[1] - 2, box[2] + 4, box[3] + 2), fill=(0, 0, 0))
        draw.text((x + 6, y + 4), label, fill=(255, 255, 255), font=font)
    with atomic_output(out_path) as tmp:
        sheet.save(tmp, format='JPEG', quality=85)
    return times


def flags_for(probe: dict[str, Any], motion: dict[str, Any], audio: dict[str, Any] | None,
              expected_frames: int | None) -> list[str]:
    flags = []
    seconds = max(1, len(motion['per_second']))
    if len(motion['static_seconds']) >= seconds * 0.6:
        flags.append('mostly_static')
    if motion['jumps_at']:
        flags.append('possible_hidden_cut')
    if expected_frames and motion['frames'] != expected_frames:
        flags.append('frame_count_mismatch')
    if probe['audio'] is None:
        flags.append('no_audio_track')
    elif audio and audio['per_second'] and len(audio['silent_seconds']) >= len(audio['per_second']) * 0.8:
        flags.append('silent_audio')
    return flags


def _identity(path: str) -> str:
    stat = os.stat(path)
    return hashlib.sha256(
        json.dumps((os.path.abspath(path), stat.st_mtime_ns, stat.st_size, stat.st_ino, OBSERVATION_VERSION)).encode()
    ).hexdigest()[:24]


def observe_video(path: str, cache_dir: str, *, expected_frames: int | None = None) -> dict[str, Any]:
    """Cached observation of one clip; ``keyframes_path`` points at its sheet."""
    if not path or not os.path.isfile(path):
        raise ObservationError(404, 'Take media is not on disk')
    os.makedirs(cache_dir, exist_ok=True)
    key = _identity(path)
    record_path = os.path.join(cache_dir, f'{key}.json')
    sheet_path = os.path.join(cache_dir, f'{key}.jpg')
    if os.path.isfile(record_path) and os.path.isfile(sheet_path):
        return json.loads(Path(record_path).read_text())
    try:
        probe = probe_media(path)
        motion = motion_profile(path)
        audio = audio_profile(path)
        times = contact_sheet(path, sheet_path)
    except ObservationError:
        raise
    except Exception as exc:
        raise ObservationError(500, f'Could not observe the take: {exc}') from exc
    record = dict(version=OBSERVATION_VERSION,
                  probe=probe,
                  motion=motion,
                  audio=audio,
                  keyframe_times=times,
                  keyframes_path=sheet_path,
                  flags=flags_for(probe, motion, audio, expected_frames))
    with atomic_output(record_path) as tmp:
        Path(tmp).write_text(json.dumps(record))
    return record


def summarize(observation: dict[str, Any]) -> dict[str, Any]:
    """The part of an observation worth sending to a planner as text."""
    motion = observation['motion']
    audio = observation.get('audio')
    return dict(seconds=observation['probe']['video_seconds'],
                size=f"{observation['probe']['width']}x{observation['probe']['height']}",
                fps=observation['probe']['fps'],
                motion_per_second=motion['per_second'],
                static_seconds=motion['static_seconds'],
                hidden_cuts_at=motion['jumps_at'],
                audio_dbfs_per_second=audio['per_second'] if audio else None,
                keyframe_times=observation['keyframe_times'],
                flags=observation['flags'])
