from __future__ import annotations

import os
import signal
import subprocess
import time
import wave

from .files import Checkpoints, file_hash


def run_process(command, store, episode_id, lock, log, timeout=1800):
    """Children inherit the production lock and are stopped on pause/cancel."""
    with log.open("a") as output:
        process = subprocess.Popen(command, stdout=output, stderr=output,
                                   start_new_session=True, pass_fds=lock.pass_fds)
        started = time.monotonic()
        try:
            while process.poll() is None:
                store.check_control(episode_id)
                if time.monotonic() - started > timeout:
                    raise RuntimeError(f"Subprocess exceeded {timeout} seconds; see {log.name}")
                time.sleep(0.5)
            store.check_control(episode_id)
            if process.returncode:
                raise RuntimeError(f"Subprocess exited {process.returncode}; see {log.name}")
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            raise


def assets(folder, title, genre):
    from PIL import Image, ImageDraw, ImageFont
    background = Image.new("RGB", (1920, 1080), "#101522")
    draw = ImageDraw.Draw(background)
    draw.ellipse((1430, 100, 1610, 280), fill="#c4c8dd")
    draw.text((150, 340), "NOX STORIES", fill="#eee7d9", font=ImageFont.load_default(size=85))
    # The bundled fallback font is ASCII; do not render missing-glyph boxes.
    title = title.encode("ascii", "replace").decode()
    lines, line = [], ""
    for word in title.split():
        if len(line) + len(word) > 43:
            lines.append(line)
            line = ""
        line += (" " if line else "") + word
    if line:
        lines.append(line)
    draw.multiline_text((155, 475), "\n".join(lines[:3]), fill="#d0d4e2",
                        font=ImageFont.load_default(size=48), spacing=16)
    draw.text((155, 850), f"{genre.capitalize()} | A story with Nox", fill="#a8b0c4", font=ImageFont.load_default(size=30))
    background.save(folder / "background.png")
    background.resize((1280, 720)).save(folder / "thumbnail.jpg", quality=92)


def render(store, episode_id, script, audio, lock):
    import imageio_ffmpeg
    folder = store.folder(episode_id)
    assets(folder, script["title"], store.spec(episode_id)["brief"]["genre"])
    video, part = folder / "video.mp4", folder / "video.part.mp4"
    inputs = {"audio": audio["sha256"], "background": file_hash(folder / "background.png"),
              "encoder": "libx264-v1-loudnorm", "ffmpeg": imageio_ffmpeg.get_ffmpeg_version()}
    cache = Checkpoints(folder)
    cached = cache.load("media", inputs)
    if cached and video.exists() and file_hash(video) == cached["sha256"]:
        return cached
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    started = time.monotonic()
    run_process([exe, "-y", "-hide_banner", "-loglevel", "warning", "-loop", "1", "-framerate", "24",
                 "-i", str(folder / "background.png"), "-i", str(folder / "audio/narration.wav"),
                 "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-tune", "stillimage",
                 "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
                 "-af", "loudnorm=I=-18:TP=-1.5:LRA=11", "-movflags", "+faststart",
                 "-t", str(audio["duration_seconds"]), str(part)],
                store, episode_id, lock, folder / "render.log")
    run_process([exe, "-v", "error", "-xerror", "-i", str(part), "-f", "null", "-"],
                store, episode_id, lock, folder / "validation.log")
    reader = imageio_ffmpeg.read_frames(str(part))
    try:
        metadata = next(reader)
    finally:
        reader.close()
    if tuple(metadata["size"]) != (1920, 1080) or abs(metadata["fps"] - 24) > 0.01:
        raise RuntimeError("Rendered video has unexpected dimensions or frame rate")
    if metadata.get("codec") != "h264" or metadata.get("audio_codec") != "aac":
        raise RuntimeError("Rendered video lacks expected H.264/AAC streams")
    if abs(metadata["duration"] - audio["duration_seconds"]) > 0.25:
        raise RuntimeError("Video duration differs from narration")
    part.replace(video)
    result = {"sha256": file_hash(video), "metadata": metadata, "full_decode_passed": True,
              "wall_seconds": time.monotonic() - started, "audio_normalization": "-18 LUFS / -1.5 dBTP"}
    cache.save("media", inputs, result)
    return result
