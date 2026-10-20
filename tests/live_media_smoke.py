"""Explicit local integration check, not a production-duration acceptance run.

Run manually: ./.venv/bin/python tests/live_media_smoke.py
Uses cached speech weights and real FFmpeg, pauses after a completed chunk,
resumes, checks chunk reuse, then renders a short Nox personality sample.
"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# Keep this script usable without installing the source checkout as a package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nox.audio import assemble, chunk_valid, chunks_for, voice_identity
from nox.files import ROOT, file_hash, write_json
from nox.media import render, run_process
from nox.models import Brief, Profile
from nox.store import ResourceLock, Store

UNITS = [
    {"section": "intro", "text": "Evening. I'm Nox. Get comfortable. Tonight's story begins with a perfectly ordinary door. It was labeled 'perfectly safe,' which is a remarkably unsettling thing to find written on a door."},
    {"section": "scene-01", "text": "Ellis knocked anyway. A woman inside called, 'Just a moment!' Something clattered. There was a brief argument with what sounded like a saucepan. Then Ivy opened the door with flour on her cheek and an oven glove on each hand."},
    {"section": "scene-02", "text": "'You said there was an emergency,' Ellis told her. 'There is. I made soup for eight people.' 'How many are coming?' 'At present? You.' He looked at the enormous pot behind her. Then he took out his phone. 'Give me ten minutes.'"},
    {"section": "scene-03", "text": "By seven, every chair in Ivy's little kitchen was occupied. Someone brought bread. Someone else brought a folding stool. Ellis washed the bowls while Ivy wrote the names of her new neighbors on the back of a shopping receipt. She left the last line blank. There would be room for one more."},
    {"section": "closing", "text": "That's our story for tonight. I'm glad you stayed. Get some rest."},
]


def main():
    store = Store(ROOT / "data" / "media-smoke")
    profile = Profile.model_validate_json((ROOT / "config/nox.v1.json").read_text())
    brief = Brief(genre="gentle", premise="A short integration fixture for Nox narration, checkpoint recovery and media validation.")
    episode_id = store.create(brief, profile)
    store.claim(episode_id)
    folder = store.folder(episode_id)
    package = {"title": "Meet Nox", "units": UNITS, "voice_identity": voice_identity(profile.model_dump())}
    write_json(folder / "script-package.json", package)
    command = [sys.executable, "-m", "nox.speech", str(store.root), episode_id]
    with ResourceLock() as lock:
        with (folder / "speech.log").open("a") as log:
            child = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=log, start_new_session=True, pass_fds=lock.pass_fds)
            deadline = time.monotonic() + 180
            try:
                while child.poll() is None:
                    with store.db() as db:
                        saved = db.execute("SELECT count(*) FROM events WHERE episode_id=? AND kind='speech_saved'", (episode_id,)).fetchone()[0]
                    if saved:
                        store.request(episode_id, "pause")
                        break
                    if time.monotonic() > deadline:
                        raise RuntimeError("Speech smoke test timed out before its first checkpoint")
                    time.sleep(0.2)
                if child.wait(timeout=30) != 75:
                    raise RuntimeError("Speech did not stop at the requested checkpoint")
            finally:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGTERM)
                    child.wait(timeout=10)
        completed = [folder / "audio/chunks" / (chunk["key"] + ".wav")
                     for chunk in chunks_for(UNITS, package["voice_identity"])
                     if chunk_valid(folder / "audio/chunks", chunk)]
        if not completed:
            raise RuntimeError("No completed speech chunk was preserved")
        before = {p.name: (file_hash(p), p.stat().st_mtime_ns) for p in completed}
        store.state(episode_id, "paused", "speech_smoke")
        store.request(episode_id, "resume")
        store.claim(episode_id)
        run_process(command, store, episode_id, lock, folder / "speech.log", timeout=300)
        if any((file_hash(folder / "audio/chunks" / name), (folder / "audio/chunks" / name).stat().st_mtime_ns) != original for name, original in before.items()):
            raise RuntimeError("Resuming speech regenerated a valid completed chunk")
        chunks = chunks_for(UNITS, package["voice_identity"])
        audio = assemble(folder / "audio", chunks, lambda: store.check_control(episode_id))
        media = render(store, episode_id, package, audio, lock)
    store.state(episode_id, "paused", "smoke_complete")
    result = {"test_only": True, "episode_id": episode_id, "speech_pause_resume_passed": True,
              "reused_chunks_unchanged": len(before), "duration_seconds": audio["duration_seconds"],
              "full_decode_passed": media["full_decode_passed"], "video": str(folder / "video.mp4")}
    write_json(folder / "smoke-result.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
