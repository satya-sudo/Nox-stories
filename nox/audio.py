from __future__ import annotations

import importlib.metadata
import json
import re
import wave
from pathlib import Path

from .files import ROOT, digest, file_hash, write_json

SAMPLE_RATE = 24000


def voice_identity(profile):
    reference = ROOT / ".runtime/huggingface/hub/models--hexgrad--Kokoro-82M/refs/main"
    if not reference.exists():
        raise RuntimeError("Kokoro weights are not cached. Run the Phase 0 voice audition once before production")
    return {"voice": profile["voice"], "weights_revision": reference.read_text().strip(),
            "kokoro": importlib.metadata.version("kokoro"), "torch": importlib.metadata.version("torch"),
            "frontends": {name: importlib.metadata.version(name) for name in
                          ("misaki", "phonemizer-fork", "espeakng-loader", "spacy", "en-core-web-sm")},
            "format": "pcm16-mono-24000-v1"}


def chunks_for(units, identity, limit=500):
    chunks = []
    for unit in units:
        for paragraph in re.split(r"\n\s*\n", unit["text"].strip().replace("*", "")):
            pieces, current = [], ""
            for sentence in re.split(r"(?<=[.!?])\s+", paragraph.strip()):
                if current and len(current) + len(sentence) + 1 > limit:
                    pieces.append(current)
                    current = ""
                if len(sentence) > limit:
                    for word in sentence.split():
                        if current and len(current) + len(word) + 1 > limit:
                            pieces.append(current)
                            current = ""
                        current += (" " if current else "") + word
                else:
                    current += (" " if current else "") + sentence
            if current:
                pieces.append(current)
            for text in pieces:
                chunks.append({"section": unit["section"], "text": text, "key": digest({"text": text, "identity": identity})})
    return chunks


def chunk_valid(folder: Path, chunk):
    audio, metadata = folder / (chunk["key"] + ".wav"), folder / (chunk["key"] + ".json")
    try:
        info = json.loads(metadata.read_text())
        with wave.open(str(audio)) as stream:
            valid_format = (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) == (1, 2, SAMPLE_RATE)
            frames = stream.getnframes()
            complete = len(stream.readframes(frames)) == frames * 2
        valid_identity = digest({"text": info["text"], "identity": info["identity"]}) == chunk["key"]
        if valid_format and complete and valid_identity and 0 < info["peak"] < 1 and frames > 0 and frames == info["frames"] and info["key"] == chunk["key"] and file_hash(audio) == info["sha256"]:
            return info
    except (OSError, ValueError, KeyError, EOFError, wave.Error):
        pass
    return None


def assemble(folder, chunks, check_control):
    output = folder / "narration.wav"
    temporary = folder / "narration.part.wav"
    timeline, frames, peak = [], 0, 0
    with wave.open(str(temporary), "wb") as destination:
        destination.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        for index, chunk in enumerate(chunks):
            check_control()
            info = chunk_valid(folder / "chunks", chunk)
            if info is None:
                raise RuntimeError("A speech chunk is missing or invalid; resume speech generation")
            start = frames / SAMPLE_RATE
            with wave.open(str(folder / "chunks" / (chunk["key"] + ".wav"))) as source:
                while samples := source.readframes(SAMPLE_RATE * 5):
                    destination.writeframesraw(samples)
            frames += info["frames"]
            peak = max(peak, info["peak"])
            timeline.append({**chunk, "start": start, "duration": info["frames"] / SAMPLE_RATE})
            if index < len(chunks) - 1:
                pause = 0.65 if chunks[index + 1]["section"] != chunk["section"] else 0.25
                silence = round(pause * SAMPLE_RATE)
                destination.writeframesraw(b"\0\0" * silence)
                frames += silence
    temporary.replace(output)
    result = {"duration_seconds": frames / SAMPLE_RATE, "peak": peak, "chunks": timeline,
              "sha256": file_hash(output), "format": "pcm16-mono-24000"}
    write_json(folder / "audio.json", result)
    return result
