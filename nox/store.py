from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .files import ROOT, digest, write_json
from .models import Brief, MODEL, PROMPT_VERSION, Profile


def now():
    return datetime.now(timezone.utc).isoformat()


class Busy(RuntimeError):
    pass


class Interrupted(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(status)


class ResourceLock:
    """An OS lock, retained by heavy children even if their parent is killed."""
    def __init__(self, path=None):
        self.path = path or ROOT / ".runtime" / "production.lock"
        self.fd = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = open(self.path, "a+")
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.fd.close()
            self.fd = None
            raise Busy("Another production worker or media child holds the resource lock")
        return self

    def __exit__(self, *exc):
        # Closing (not explicitly unlocking) preserves a child's inherited lock.
        self.fd.close()
        self.fd = None

    @property
    def pass_fds(self):
        return (self.fd.fileno(),)


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "nox.sqlite3"
        with self.db() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("Unsupported database schema version")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS episodes (
                    id TEXT PRIMARY KEY, spec_hash TEXT NOT NULL,
                    status TEXT NOT NULL, stage TEXT NOT NULL,
                    control TEXT, error TEXT, created TEXT NOT NULL, updated TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    episode_id TEXT NOT NULL REFERENCES episodes(id),
                    at TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL
                );
                PRAGMA user_version=1;
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def folder(self, episode_id):
        if not re.fullmatch(r"[a-f0-9]{32}", episode_id):
            raise ValueError("Invalid episode ID")
        return self.root / "episodes" / episode_id / "revisions" / "1"

    def create(self, brief: Brief, profile: Profile, model=MODEL):
        episode_id = uuid.uuid4().hex
        spec = {"revision": 1, "brief": brief.model_dump(), "profile": profile.model_dump(),
                "model": model, "prompt_version": PROMPT_VERSION}
        write_json(self.folder(episode_id) / "spec.json", spec)
        stamp = now()
        with self.db() as db:
            db.execute("INSERT INTO episodes VALUES (?,?, 'queued','preflight',NULL,NULL,?,?)",
                       (episode_id, digest(spec), stamp, stamp))
        return episode_id

    def get(self, episode_id):
        self.folder(episode_id)
        with self.db() as db:
            row = db.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
        if row is None:
            raise ValueError("Episode not found")
        return dict(row)

    def spec(self, episode_id):
        spec = json.loads((self.folder(episode_id) / "spec.json").read_text())
        if digest(spec) != self.get(episode_id)["spec_hash"]:
            raise ValueError("Episode snapshot changed; create a new episode instead of editing a frozen revision")
        if spec["prompt_version"] != PROMPT_VERSION:
            raise ValueError("Prompt version differs; create a new episode using the current prompts")
        Brief.model_validate(spec["brief"])
        Profile.model_validate(spec["profile"])
        return spec

    def list(self):
        with self.db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM episodes ORDER BY created")]

    def state(self, episode_id, status, stage=None, error=None):
        if status not in ("queued", "running", "paused", "cancelled", "failed", "awaiting_review"):
            raise ValueError("Invalid episode status")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT control FROM episodes WHERE id=?", (episode_id,)).fetchone()
            if row is None:
                raise ValueError("Episode not found")
            if status in ("paused", "failed", "awaiting_review") and row["control"] == "cancel":
                status = "cancelled"
            elif status == "awaiting_review" and row["control"] == "pause":
                status = "paused"
            db.execute("UPDATE episodes SET status=?,stage=COALESCE(?,stage),error=?,updated=? WHERE id=?",
                       (status, stage, error, now(), episode_id))
        return status

    def event(self, episode_id, kind, detail):
        with self.db() as db:
            db.execute("INSERT INTO events(episode_id,at,kind,detail) VALUES (?,?,?,?)",
                       (episode_id, now(), kind, json.dumps(detail)))
        print(json.dumps({"episode": episode_id, "kind": kind, **detail}), flush=True)

    def recover(self, lock: ResourceLock):
        if lock.fd is None:
            raise Busy("Recovery requires the production lock")
        with self.db() as db:
            db.execute("UPDATE episodes SET status=CASE control WHEN 'pause' THEN 'paused' WHEN 'cancel' THEN 'cancelled' ELSE 'queued' END, updated=? WHERE status='running'", (now(),))

    def claim(self, episode_id=None):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            query = "SELECT id FROM episodes WHERE status='queued'"
            values = ()
            if episode_id:
                query += " AND id=?"
                values = (episode_id,)
            row = db.execute(query + " ORDER BY created LIMIT 1", values).fetchone()
            if row is None:
                return None
            db.execute("UPDATE episodes SET status='running', error=NULL, updated=? WHERE id=?", (now(), row[0]))
            return row[0]

    def request(self, episode_id, action):
        if action not in ("pause", "resume", "cancel"):
            raise ValueError("Unknown control action")
        self.folder(episode_id)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
            if row is None:
                raise ValueError("Episode not found")
            if action == "resume":
                if row["status"] not in ("paused", "failed"):
                    raise ValueError("Only paused or failed episodes can resume")
                db.execute("UPDATE episodes SET status='queued',control=NULL,error=NULL,updated=? WHERE id=?", (now(), episode_id))
            else:
                if row["status"] in ("cancelled", "awaiting_review"):
                    raise ValueError("This episode is already finished")
                status = row["status"] if row["status"] == "running" else ("paused" if action == "pause" else "cancelled")
                db.execute("UPDATE episodes SET status=?,control=?,updated=? WHERE id=?", (status, action, now(), episode_id))

    def check_control(self, episode_id):
        control = self.get(episode_id)["control"]
        if control:
            raise Interrupted("paused" if control == "pause" else "cancelled")
