from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path: Path, data):
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


class Checkpoints:
    def __init__(self, folder: Path):
        self.folder = folder

    def load(self, name: str, inputs):
        path = self.folder / f"{name}.json"
        try:
            record = json.loads(path.read_text())
            if record["input_hash"] == digest(inputs) and record["output_hash"] == digest(record["data"]):
                return record["data"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def save(self, name: str, inputs, data):
        write_json(self.folder / f"{name}.json", {
            "input_hash": digest(inputs), "output_hash": digest(data), "data": data,
        })
