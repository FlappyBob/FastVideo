# SPDX-License-Identifier: Apache-2.0
"""Render a story's editorial decisions into one film with ffmpeg.

``merge.py`` joins whole clips by stream copy. A cut is more than a join:
each shot keeps only its in/out range, shots meet on a cut, a crossfade or a
dip to black, captions are burned in for their time window, and an optional
soundtrack is mixed under the generated audio with ducking and a fade-out.
That needs one re-encode, built here as a single ``filter_complex`` so the
whole timeline is rendered in one pass.

The graph builder is pure (clip facts in, command out) and tested on its
own; running ffmpeg is a thin wrapper. Needs an ``ffmpeg`` binary with
libx264, xfade and drawtext, which the project already relies on for merges.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from fastvideo_studio.media import atomic_output, media_locks
from fastvideo_studio.merge import MERGED_DIRNAME, MergeError, find_ffmpeg, probe_clip

CAPTION_FONT_CANDIDATES = (
    '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
)
TRANSITIONS = {'cut': None, 'crossfade': 'fade', 'fade_black': 'fadeblack'}
MIN_SEGMENT_SECONDS = 0.25


def find_caption_font() -> str | None:
    """A font file for burned-in captions: $FASTVIDEO_CAPTION_FONT, else a CJK-capable system font."""
    configured = os.getenv('FASTVIDEO_CAPTION_FONT')
    if configured and os.path.isfile(configured):
        return configured
    return next((path for path in CAPTION_FONT_CANDIDATES if os.path.isfile(path)), None)


@dataclass(frozen=True)
class Segment:
    path: str
    label: str
    source_seconds: float
    has_audio: bool
    in_seconds: float = 0.0
    out_seconds: float | None = None
    transition: str = 'cut'  # how this segment joins the previous one
    transition_seconds: float = 0.5
    caption: str = ''
    caption_start: float = 0.0
    caption_end: float | None = None

    @property
    def end(self) -> float:
        return min(self.source_seconds, self.out_seconds) if self.out_seconds else self.source_seconds

    @property
    def seconds(self) -> float:
        return self.end - self.in_seconds


@dataclass(frozen=True)
class SoundtrackSpec:
    path: str
    gain_db: float = -14.0
    duck: bool = True
    fade_out_seconds: float = 2.0


@dataclass(frozen=True)
class CutSpec:
    segments: list[Segment]
    width: int
    height: int
    fps: float
    soundtrack: SoundtrackSpec | None = None
    font_size: int = 44
    caption_position: str = 'bottom'
    font_path: str | None = None
    temp_files: list[str] = field(default_factory=list)


def timeline(spec: CutSpec) -> list[dict[str, Any]]:
    """Where each segment lands on the output clock, after transition overlaps."""
    placed = []
    clock = 0.0
    for index, segment in enumerate(spec.segments):
        overlap = 0.0
        if index and TRANSITIONS[segment.transition]:
            overlap = min(segment.transition_seconds, segment.seconds / 2, placed[-1]['seconds'] / 2)
        start = clock - overlap
        placed.append(
            dict(label=segment.label,
                 start=round(start, 3),
                 seconds=round(segment.seconds, 3),
                 transition=segment.transition,
                 overlap=round(overlap, 3)))
        clock = start + segment.seconds
    return placed


def total_seconds(spec: CutSpec) -> float:
    placed = timeline(spec)
    return round(placed[-1]['start'] + placed[-1]['seconds'], 3) if placed else 0.0


def validate(spec: CutSpec) -> None:
    if not spec.segments:
        raise MergeError(400, 'No shots to render.')
    for segment in spec.segments:
        if segment.in_seconds >= segment.source_seconds:
            raise MergeError(409, f'{segment.label}: in point {segment.in_seconds:.2f}s is past the end of the take '
                             f'({segment.source_seconds:.2f}s).')
        if segment.seconds < MIN_SEGMENT_SECONDS:
            raise MergeError(409, f'{segment.label}: kept range is shorter than {MIN_SEGMENT_SECONDS}s.')
        if segment.transition not in TRANSITIONS:
            raise MergeError(422, f'{segment.label}: unknown transition {segment.transition!r}.')
        if segment.caption and not spec.font_path:
            raise MergeError(503, 'No caption font is available; set FASTVIDEO_CAPTION_FONT to a .ttf/.ttc file.')


def _drawtext(spec: CutSpec, segment: Segment, text_file: str) -> str:
    end = segment.caption_end if segment.caption_end is not None else segment.seconds
    y = f'h-th-{max(24, spec.height // 12)}' if spec.caption_position == 'bottom' else str(max(24, spec.height // 12))
    font = (spec.font_path or '').replace('\\', '\\\\').replace(':', '\\:').replace("'", "\\'")
    return (f"drawtext=fontfile='{font}':textfile='{text_file}':fontsize={spec.font_size}:fontcolor=white:"
            f"borderw={max(1, spec.font_size // 16)}:bordercolor=black@0.85:x=(w-tw)/2:y={y}:"
            f"enable='between(t,{segment.caption_start:.3f},{end:.3f})'")


def build_command(spec: CutSpec, out_path: str, ffmpeg: str) -> list[str]:
    """The complete ffmpeg invocation for ``spec``; pure, so it can be inspected."""
    validate(spec)
    inputs: list[str] = []
    filters: list[str] = []
    index = 0
    video_labels: list[str] = []
    audio_labels: list[str] = []
    for number, segment in enumerate(spec.segments):
        inputs += ['-i', segment.path]
        video = (f'[{index}:v]trim=start={segment.in_seconds:.3f}:end={segment.end:.3f},setpts=PTS-STARTPTS,'
                 f'scale={spec.width}:{spec.height}:flags=bicubic,fps={spec.fps:g},format=yuv420p')
        if segment.caption:
            video += ',' + _drawtext(spec, segment, spec.temp_files[number])
        filters.append(f'{video}[v{number}]')
        if segment.has_audio:
            filters.append(f'[{index}:a]atrim=start={segment.in_seconds:.3f}:end={segment.end:.3f},'
                           f'asetpts=PTS-STARTPTS,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo'
                           f'[a{number}]')
        else:
            filters.append(f'anullsrc=r=48000:cl=stereo,atrim=0:{segment.seconds:.3f},asetpts=PTS-STARTPTS'
                           f'[a{number}]')
        video_labels.append(f'[v{number}]')
        audio_labels.append(f'[a{number}]')
        index += 1

    placed = timeline(spec)
    current_v, current_a = video_labels[0], audio_labels[0]
    clock = spec.segments[0].seconds
    for number in range(1, len(spec.segments)):
        segment = spec.segments[number]
        kind = TRANSITIONS[segment.transition]
        overlap = placed[number]['overlap']
        if kind and overlap > 0:
            filters.append(f'{current_v}{video_labels[number]}xfade=transition={kind}:duration={overlap:.3f}:'
                           f'offset={clock - overlap:.3f}[vx{number}]')
            filters.append(f'{current_a}{audio_labels[number]}acrossfade=d={overlap:.3f}:c1=tri:c2=tri[ax{number}]')
            clock = clock - overlap + segment.seconds
        else:
            filters.append(f'{current_v}{video_labels[number]}concat=n=2:v=1:a=0[vx{number}]')
            filters.append(f'{current_a}{audio_labels[number]}concat=n=2:v=0:a=1[ax{number}]')
            clock += segment.seconds
        current_v, current_a = f'[vx{number}]', f'[ax{number}]'

    final_audio = current_a
    if spec.soundtrack:
        inputs += ['-stream_loop', '-1', '-i', spec.soundtrack.path]
        music = (f'[{index}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,'
                 f'atrim=0:{clock:.3f},asetpts=PTS-STARTPTS,volume={spec.soundtrack.gain_db:.1f}dB')
        fade = min(spec.soundtrack.fade_out_seconds, clock)
        if fade > 0:
            music += f',afade=t=out:st={clock - fade:.3f}:d={fade:.3f}'
        filters.append(f'{music}[music]')
        filters.append(f'{current_a}asplit=2[voice][key]')
        if spec.soundtrack.duck:
            filters.append('[music][key]sidechaincompress=threshold=0.03:ratio=5:attack=40:release=500:'
                           'makeup=1[ducked]')
            bed = '[ducked]'
        else:
            filters.append('[key]anullsink')
            bed = '[music]'
        filters.append(f'[voice]{bed}amix=inputs=2:duration=first:normalize=0[aout]')
        final_audio = '[aout]'
    filters.append(f'{current_v}format=yuv420p[vout]')
    return [
        ffmpeg, '-y', '-loglevel', 'error', *inputs, '-filter_complex', ';'.join(filters), '-map', '[vout]', '-map',
        final_audio, '-c:v', 'libx264', '-crf', '18', '-preset', 'medium', '-r', f'{spec.fps:g}', '-c:a', 'aac',
        '-b:a', '192k', '-movflags', '+faststart', '-shortest', out_path
    ]


def render(spec: CutSpec, out_path: str, ffmpeg: str | None = None, timeout: float = 1800) -> float:
    """Write the film to ``out_path`` and return its planned length in seconds."""
    ffmpeg = ffmpeg or find_ffmpeg()
    if not ffmpeg:
        raise MergeError(503, "ffmpeg isn't available on the server. Install it or set FASTVIDEO_FFMPEG_BIN.")
    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    temp_files = []
    try:
        for segment in spec.segments:
            fd, path = tempfile.mkstemp(suffix='.txt', prefix='caption-')
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                handle.write(segment.caption)
            temp_files.append(path)
        prepared = CutSpec(**{**spec.__dict__, 'temp_files': temp_files})
        command = build_command(prepared, '', ffmpeg)[:-1]
        with atomic_output(out_path) as tmp_out:
            result = subprocess.run([*command, tmp_out], capture_output=True, text=True, timeout=timeout, check=False)
            if result.returncode != 0:
                tail = ' '.join(result.stderr.strip().splitlines()[-4:])
                raise MergeError(500, f'ffmpeg failed: {tail or "unknown error"}')
    except subprocess.TimeoutExpired as exc:
        raise MergeError(500, 'ffmpeg took too long.') from exc
    finally:
        for path in temp_files:
            with suppress(OSError):
                os.remove(path)
    return total_seconds(spec)


def _safe_stem(name: str) -> str:
    return re.sub(r'[^\w.-]+', '-', name).strip('-.')[:60] or 'cut'


@dataclass(frozen=True)
class CutResult:
    filename: str
    path: str
    clips: int
    seconds: float
    timeline: list[dict[str, Any]]


def segments_from_shots(shots: list[dict[str, Any]], paths: dict[str, str]) -> list[Segment]:
    """Segments for the ordered shots of a blueprint, probing each selected take."""
    segments = []
    for shot in shots:
        path = paths[shot['id']]
        info = probe_clip(path)
        edit = shot.get('edit') or {}
        segments.append(
            Segment(path=path,
                    label=shot.get('title') or shot['id'],
                    source_seconds=info.video_seconds,
                    has_audio=info.audio is not None,
                    in_seconds=float(edit.get('in_seconds', 0) or 0),
                    out_seconds=edit.get('out_seconds'),
                    transition=edit.get('transition', 'cut'),
                    transition_seconds=float(edit.get('transition_seconds', 0.5)),
                    caption=edit.get('caption', '') or '',
                    caption_start=float(edit.get('caption_start', 0) or 0),
                    caption_end=edit.get('caption_end')))
    return segments


def render_cut(shots: list[dict[str, Any]],
               paths: dict[str, str],
               out_dir: str,
               *,
               name: str = '',
               soundtrack: SoundtrackSpec | None = None,
               font_size: int = 44,
               caption_position: str = 'bottom',
               job_ids: list[str] | None = None) -> CutResult:
    """Render the shots' selected takes with their editorial decisions into ``<out_dir>/merged``."""
    with media_locks(job_ids or []):
        for shot in shots:
            if not os.path.isfile(paths.get(shot['id'], '')):
                raise MergeError(409, f"{shot.get('title') or shot['id']}'s video is missing from disk.")
        segments = segments_from_shots(shots, paths)
        first = probe_clip(segments[0].path)
        spec = CutSpec(segments=segments,
                       width=first.width,
                       height=first.height,
                       fps=first.fps or 24,
                       soundtrack=soundtrack,
                       font_size=font_size,
                       caption_position=caption_position,
                       font_path=find_caption_font())
        filename = f"{_safe_stem(name)}-cut-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex}.mp4"
        out_path = os.path.join(out_dir, MERGED_DIRNAME, filename)
        seconds = render(spec, out_path)
        return CutResult(filename=filename, path=out_path, clips=len(segments), seconds=seconds,
                         timeline=timeline(spec))


__all__ = [
    'CutSpec', 'Segment', 'SoundtrackSpec', 'CutResult', 'build_command', 'render', 'render_cut', 'timeline',
    'total_seconds', 'find_caption_font', 'segments_from_shots'
]
