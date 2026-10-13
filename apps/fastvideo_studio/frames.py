# SPDX-License-Identifier: Apache-2.0
"""Save the end of a finished clip, so the next clip can continue from it.

Two forms: the final frame alone (a still, for a hard "opens on this exact
picture" continuation), or a short trailing clip (a few hundred milliseconds of
video, attached as an H3 video reference so the model has actual motion to
continue rather than guessing it from one still). Kept apart from ``server.py``
because it needs nothing from FastVideo -- only PyAV -- and so can be exercised
without a GPU.
"""

from __future__ import annotations

import hashlib
import json
import os
from fractions import Fraction
from pathlib import Path
from typing import Any

from fastvideo_studio.media import atomic_output, media_lock

VIDEO_EXTENSIONS = {".mp4", ".webm", ".avi", ".mov", ".mkv"}


class FrameError(Exception):
    """A request that can't be satisfied, carrying the HTTP status to report."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def save_last_frame(video_path: str, out_path: str) -> None:
    """Decode ``video_path`` and write its final frame to ``out_path`` as a PNG.

    Clips are a few hundred frames, so decoding through to the end is simpler
    and more reliable than seeking (which lands on keyframes, not the last frame).
    """
    import av

    with av.open(video_path) as container:
        last = None
        for frame in container.decode(container.streams.video[0]):
            last = frame
    if last is None:
        raise ValueError("The video has no frames.")
    with atomic_output(out_path) as tmp_path:
        last.to_image().save(tmp_path)


def last_frame_for_job(job: Any, upload_dir: str) -> str:
    """Path of a PNG of the last frame of ``job``'s video, extracting it if needed.

    Raises :class:`FrameError` with the status the API should return.
    """
    return _ending_for_job(job, upload_dir, "last_frame", ".png", save_last_frame)


def _source_identity(path: str) -> tuple:
    stat = os.stat(path)
    return (os.path.abspath(path), stat.st_mtime_ns, stat.st_size, stat.st_ctime_ns, stat.st_dev, stat.st_ino)


def _ending_for_job(job: Any, upload_dir: str, kind: str, extension: str, save: Any, *args: Any) -> str:
    with media_lock(job.id):
        status = getattr(job.status, "value", job.status)
        if status != "completed" or not job.output_path:
            raise FrameError(404, "No output available for this job")
        if not os.path.isfile(job.output_path):
            raise FrameError(404, "Output file not found on disk")
        if Path(job.output_path).suffix.lower() not in VIDEO_EXTENSIONS:
            raise FrameError(400, "This job's output is not a video")
        if not upload_dir:
            raise FrameError(503, "Upload directory not configured")

        out_dir = os.path.join(upload_dir, f"{kind}s")
        os.makedirs(out_dir, exist_ok=True)
        try:
            source = job.output_path
            identity = _source_identity(source)
            # Store the source version and extraction parameters in the immutable
            # artifact's name. No separately published metadata can get out of sync.
            version = hashlib.sha256(json.dumps((identity, args)).encode()).hexdigest()
            out_path = os.path.abspath(os.path.join(out_dir, f"{kind}_{job.id}_{version}{extension}"))
            if not os.path.isfile(out_path):
                save(source, out_path, *args)
                # Detect external writers that do not participate in our lock.
                if job.output_path != source or _source_identity(source) != identity:
                    os.unlink(out_path)
                    raise FrameError(409, "The source video changed during extraction; try again.")
        except FrameError:
            raise
        except Exception as e:
            description = "the last frame" if kind == "last_frame" else "the end of the clip"
            raise FrameError(500, f"Could not read {description}: {e}") from e
        return out_path


DEFAULT_LAST_CLIP_SECONDS = 1.0


def save_last_clip(video_path: str, out_path: str, seconds: float = DEFAULT_LAST_CLIP_SECONDS) -> None:
    """Decode the final ``seconds`` of ``video_path`` and re-encode them to ``out_path``.

    Re-encoded rather than stream-copied: the source's keyframes rarely align
    with an arbitrary cut ``seconds`` from the end, and a copy can only start on
    one. Silent -- the trailing video is for motion continuity; voice continuity
    already comes from each job's own audio reference.
    """
    import av

    if seconds <= 0:
        raise ValueError(f"seconds must be positive, got {seconds}.")

    with av.open(video_path) as src:
        video_stream = src.streams.video[0]
        fps = float(video_stream.average_rate or video_stream.guessed_rate or 24)
        frames = list(src.decode(video_stream))
    if not frames:
        raise ValueError("The video has no frames.")
    keep = max(1, round(seconds * fps))
    tail = frames[-keep:]

    with atomic_output(out_path) as tmp_path, av.open(tmp_path, mode="w") as out:
        out_stream = out.add_stream("h264", rate=round(fps))
        out_stream.width, out_stream.height = tail[0].width, tail[0].height
        out_stream.pix_fmt = "yuv420p"
        # Each frame needs its own increasing pts at a known time base, or PyAV
        # stamps every frame identically (pts=0), which corrupts the muxed
        # container's average-frame-rate metadata -- H3's reference pipeline
        # trusts that metadata to resample the clip, so a wrong rate there
        # silently collapses a short clip down to as little as one frame.
        frame_time_base = Fraction(1, round(fps))
        for i, frame in enumerate(tail):
            frame.pts = i
            frame.time_base = frame_time_base
            for packet in out_stream.encode(frame):
                out.mux(packet)
        for packet in out_stream.encode():
            out.mux(packet)


def last_clip_for_job(job: Any, upload_dir: str, seconds: float = DEFAULT_LAST_CLIP_SECONDS) -> str:
    """Path of a short video of the end of ``job``'s video, extracting it if needed.

    Raises :class:`FrameError` with the status the API should return.
    """
    return _ending_for_job(job, upload_dir, "last_clip", ".mp4", save_last_clip, seconds)
