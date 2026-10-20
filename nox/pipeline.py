from __future__ import annotations

import importlib.util
import json
import shutil
import sys

from .audio import assemble, chunks_for, voice_identity
from .files import Checkpoints, ROOT, file_hash, write_json
from .media import render, run_process
from .ollama import Ollama
from .store import Interrupted, ResourceLock
from .writing import Writer


def produce(store, episode_id, lock, until=None, client_factory=Ollama):
    spec = store.spec(episode_id)
    folder = store.folder(episode_id)
    folder.mkdir(parents=True, exist_ok=True)
    control = lambda: store.check_control(episode_id)
    client = client_factory(spec["model"], folder / "requests", control)
    cache = Checkpoints(folder)

    def stage(name):
        control()
        store.state(episode_id, "running", name)
        store.event(episode_id, "stage", {"name": name})

    stage("preflight")
    if shutil.disk_usage(store.root).free < 2 * 1024 ** 3:
        raise RuntimeError("At least 2 GiB of free disk space is required")
    for name in ("kokoro", "torch", "imageio_ffmpeg", "en_core_web_sm", "espeakng_loader"):
        if importlib.util.find_spec(name) is None:
            raise RuntimeError(f"Required dependency {name} is missing; complete the setup instructions")
    identity = {"story_model": client.identity(), "speech": voice_identity(spec["profile"])}
    pin = folder / "runtime.json"
    if pin.exists() and json.loads(pin.read_text()) != identity:
        raise RuntimeError("Model or speech dependencies changed; create a new episode rather than mixing revisions")
    write_json(pin, identity)
    duration_state = cache.load("duration", {"spec": spec}) or {"round": 0, "factor": 1.0}
    while True:
        stage("writing")
        try:
            script = Writer(store, episode_id, spec, client).write(duration_state["factor"])
        finally:
            # Failure to unload is a hard stop: never start speech with the LLM resident.
            client.unload()
        script["voice_identity"] = identity["speech"]
        write_json(folder / "script-package.json", script)
        if until == "script":
            store.state(episode_id, "paused", "script_ready")
            return
        stage("speech")
        run_process([sys.executable, "-m", "nox.speech", str(store.root), episode_id],
                    store, episode_id, lock, folder / "speech.log", timeout=3600)
        control()
        stage("audio_check")
        chunks = chunks_for(script["units"], identity["speech"])
        audio = assemble(folder / "audio", chunks, control)
        duration = audio["duration_seconds"]
        store.event(episode_id, "duration", {"seconds": duration, "repair_round": duration_state["round"]})
        if 900 <= duration <= 1200:
            break
        if duration_state["round"] >= 2:
            raise RuntimeError(f"Narration duration {duration:.1f}s is outside 900–1200s after two script repairs")
        # Speech has exited. Only now may the LLM be loaded to resize the script.
        duration_state = {"round": duration_state["round"] + 1,
                          "factor": duration_state["factor"] * spec["brief"]["target_minutes"] * 60 / duration}
        cache.save("duration", {"spec": spec}, duration_state)
    stage("render")
    media = render(store, episode_id, script, audio, lock)
    stage("package_check")
    files = ["script.txt", "script-package.json", "background.png", "thumbnail.jpg", "video.mp4", "audio/narration.wav", "audio/audio.json"]
    manifest = {"spec_hash": store.get(episode_id)["spec_hash"], "runtime": identity,
                "profile_version": spec["profile"]["version"], "duration_seconds": duration,
                "media": media, "files": {name: file_hash(folder / name) for name in files},
                "review": "Owner review required; model continuity checks are heuristic, not a publishing approval"}
    write_json(folder / "manifest.json", manifest)
    final_status = store.state(episode_id, "awaiting_review", "complete")
    store.event(episode_id, final_status, {"video": str(folder / "video.mp4"), "seconds": duration})


def work(store, episode_id=None, until=None):
    with ResourceLock() as lock:
        store.recover(lock)
        job = store.claim(episode_id)
        if job is None:
            return None
        try:
            produce(store, job, lock, until)
        except Interrupted as exc:
            store.state(job, exc.status)
        except KeyboardInterrupt:
            store.state(job, "paused", error="Worker interrupted; resume to reuse valid checkpoints")
            raise
        except Exception as exc:
            store.state(job, "failed", error=str(exc))
            store.event(job, "failed", {"error": str(exc)})
            raise
        return job
