from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .audio import voice_identity
from .files import ROOT
from .models import Brief, MODEL, Profile
from .ollama import Ollama
from .pipeline import work
from .store import Store


def main():
    parser = argparse.ArgumentParser(description="Local Nox Stories production queue")
    parser.add_argument("--data-root", type=Path, default=ROOT / "data")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("profile", help="Show Nox's narrator profile")
    commands.add_parser("doctor", help="Check local model and cached voice dependencies")
    create = commands.add_parser("create", help="Snapshot a brief and enqueue an episode")
    create.add_argument("--brief", type=Path, required=True)
    create.add_argument("--profile", type=Path, default=ROOT / "config/nox.v1.json")
    create.add_argument("--model", default=MODEL)
    commands.add_parser("list")
    for name in ("status", "events", "pause", "resume", "cancel"):
        commands.add_parser(name).add_argument("episode_id")
    worker = commands.add_parser("worker", help="Process queued episodes one at a time")
    worker.add_argument("--episode")
    worker.add_argument("--until", choices=["script"])
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "profile":
            print((ROOT / "config/nox.v1.json").read_text())
            return 0
        if args.command == "doctor":
            profile = Profile.model_validate_json((ROOT / "config/nox.v1.json").read_text())
            client = Ollama(MODEL, args.data_root / "doctor")
            print(json.dumps({"story_model": client.identity(), "speech": voice_identity(profile.model_dump())}, indent=2))
            return 0
        store = Store(args.data_root)
        if args.command == "create":
            if args.model.endswith(":cloud"):
                raise ValueError("Production uses local models only")
            brief = Brief.model_validate_json(args.brief.read_text())
            profile = Profile.model_validate_json(args.profile.read_text())
            print(store.create(brief, profile, args.model))
        elif args.command == "list":
            print(json.dumps(store.list(), indent=2))
        elif args.command == "status":
            print(json.dumps({**store.get(args.episode_id), "folder": str(store.folder(args.episode_id))}, indent=2))
        elif args.command == "events":
            store.get(args.episode_id)
            with store.db() as db:
                rows = [dict(row) for row in db.execute("SELECT * FROM events WHERE episode_id=? ORDER BY seq", (args.episode_id,))]
            print(json.dumps(rows, indent=2))
        elif args.command in ("pause", "resume", "cancel"):
            store.request(args.episode_id, args.command)
            print(json.dumps(store.get(args.episode_id), indent=2))
        elif args.command == "worker":
            if args.episode:
                store.get(args.episode)
            while True:
                result = work(store, args.episode, args.until)
                if args.once or args.episode or (result is None and not args.watch):
                    break
                if result is None:
                    time.sleep(2)
        return 0
    except KeyboardInterrupt:
        print("Worker paused; valid checkpoints are retained.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
