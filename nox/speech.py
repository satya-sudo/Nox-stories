"""Isolated heavy speech process. Never imported by the writing worker."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import wave
from pathlib import Path

from .audio import SAMPLE_RATE, chunk_valid, chunks_for, voice_identity
from .files import ROOT, file_hash, write_json
from .store import Interrupted, Store


def synthesize(store, episode_id, package):
    spec = store.spec(episode_id)
    profile = spec["profile"]
    identity = voice_identity(profile)
    if identity != package["voice_identity"]:
        raise RuntimeError("The speech model changed after preflight; create a new episode")
    chunks = chunks_for(package["units"], identity)
    folder = store.folder(episode_id) / "audio" / "chunks"
    folder.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(ROOT / ".runtime" / "huggingface")
    os.environ["XDG_CACHE_HOME"] = str(ROOT / ".runtime" / "cache")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import espeakng_loader
    os.environ["PHONEMIZER_ESPEAK_LIBRARY"] = espeakng_loader.get_library_path()
    os.environ["PHONEMIZER_ESPEAK_DATA_PATH"] = espeakng_loader.get_data_path()
    import numpy as np
    import torch
    from kokoro import KPipeline
    torch.set_num_threads(2)
    pipeline = None
    for index, chunk in enumerate(chunks):
        store.check_control(episode_id)
        if chunk_valid(folder, chunk):
            store.event(episode_id, "speech_reused", {"chunk": index + 1, "total": len(chunks)})
            continue
        if pipeline is None:
            pipeline = KPipeline(lang_code="a", device="cpu", repo_id=profile["voice"]["model"])
        temporary = folder / (chunk["key"] + ".part.wav")
        frames, peak, spoken = 0, 0.0, []
        # A failed chunk is retried twice, without changing the selected voice.
        for attempt in range(3):
            try:
                frames, peak, spoken = 0, 0.0, []
                with wave.open(str(temporary), "wb") as audio:
                    audio.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
                    for text, _, samples in pipeline(chunk["text"], voice=profile["voice"]["name"], speed=profile["voice"]["speed"]):
                        store.check_control(episode_id)
                        if samples is None or len(samples) == 0:
                            raise RuntimeError("Empty speech output")
                        samples = samples.detach().cpu().numpy() if hasattr(samples, "detach") else samples
                        if not np.isfinite(samples).all() or float(np.abs(samples).max()) >= 1:
                            raise RuntimeError("Speech contains non-finite or clipping samples")
                        audio.writeframesraw(np.rint(samples * 32767).astype("<i2").tobytes())
                        frames += len(samples)
                        peak = max(peak, float(np.abs(samples).max()))
                        spoken.append(text)
                normalize = lambda text: re.findall(r"\w+", text.casefold())
                if not frames or peak < 0.0001 or normalize(" ".join(spoken)) != normalize(chunk["text"]):
                    raise RuntimeError("Speech text coverage is incomplete")
                break
            except Interrupted:
                raise
            except (RuntimeError, ValueError):
                if attempt == 2:
                    raise
        final = folder / (chunk["key"] + ".wav")
        temporary.replace(final)
        write_json(folder / (chunk["key"] + ".json"), {
            "key": chunk["key"], "frames": frames, "peak": peak, "sha256": file_hash(final),
            "identity": identity, "text": chunk["text"],
        })
        store.event(episode_id, "speech_saved", {"chunk": index + 1, "total": len(chunks)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("data_root", type=Path)
    parser.add_argument("episode_id")
    args = parser.parse_args()
    store = Store(args.data_root)
    package = json.loads((store.folder(args.episode_id) / "script-package.json").read_text())
    try:
        synthesize(store, args.episode_id, package)
    except Interrupted:
        sys.exit(75)


if __name__ == "__main__":
    main()
