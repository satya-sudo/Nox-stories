"""Verify completed local packages. This does not approve their story content."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nox.files import ROOT, digest, file_hash, write_json
from nox.store import Store


def check(store, episode_id):
    row = store.get(episode_id)
    if row["status"] != "awaiting_review":
        raise ValueError(f"{episode_id}: package is {row['status']}, not awaiting_review")
    folder = store.folder(episode_id)
    # Completed packages remain inspectable after the writer's prompt version changes.
    spec = json.loads((folder / "spec.json").read_text())
    if digest(spec) != row["spec_hash"]:
        raise ValueError("Episode snapshot mismatch")
    manifest = json.loads((folder / "manifest.json").read_text())
    if manifest["spec_hash"] != row["spec_hash"]:
        raise ValueError("Manifest snapshot mismatch")
    required = {"script.txt", "script-package.json", "background.png", "thumbnail.jpg",
                "video.mp4", "audio/narration.wav", "audio/audio.json"}
    if set(manifest["files"]) != required:
        raise ValueError("Unexpected package file list")
    for name, expected in manifest["files"].items():
        if file_hash(folder / name) != expected:
            raise ValueError(f"Integrity failure: {name}")
    seconds = manifest["duration_seconds"]
    media = manifest["media"]
    metadata = media["metadata"]
    if not 900 <= seconds <= 1200:
        raise ValueError("Narration must last 15–20 minutes")
    if (metadata["size"] != [1920, 1080] or abs(metadata["fps"] - 24) > .01
            or metadata["codec"] != "h264" or metadata["audio_codec"] != "aac"
            or abs(metadata["duration"] - seconds) > .25
            or media.get("full_decode_passed") is not True
            or media["sha256"] != manifest["files"]["video.mp4"]):
        raise ValueError("Media validation record failed")
    return {"episode": episode_id, "genre": spec["brief"]["genre"],
            "model": spec["model"], "seconds": seconds,
            "hashes_verified": True, "full_decode_record_verified": True,
            "editorial_review": "required", "video": str(folder / "video.mp4")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episodes", nargs="+")
    parser.add_argument("--data-root", type=Path, default=ROOT / "data")
    parser.add_argument("--all-genres", action="store_true")
    args = parser.parse_args()
    store = Store(args.data_root)
    results = [check(store, episode) for episode in args.episodes]
    if args.all_genres and {r["genre"] for r in results} != {"fantasy", "horror", "gentle"}:
        raise ValueError("Full Phase 1 acceptance needs fantasy, horror and gentle packages")
    report = {"packages": results, "all_genres_checked": args.all_genres,
              "editorial_approval": False}
    write_json(store.root / "package-verification.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
