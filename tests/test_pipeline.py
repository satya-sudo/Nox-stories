import json
import re
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from nox.audio import SAMPLE_RATE, assemble, chunk_valid, chunks_for
from nox.files import Checkpoints, ROOT, digest, file_hash, write_json
from nox.models import Aside, Brief, Character, Framing, Outline, Profile, Review, Scene
from nox.media import run_process
from nox.ollama import Ollama
from nox.pipeline import produce
from nox.store import Busy, Interrupted, ResourceLock, Store
from nox.writing import Writer, remove_repeated_narration, scene_issues
from check_acceptance import check as check_package


def profile():
    return Profile.model_validate_json((ROOT / "config/nox.v1.json").read_text())


def brief():
    return Brief.model_validate_json((ROOT / "config/briefs/fantasy.json").read_text())


def plan():
    return Outline(title="The Raincloud Delivery", premise="A tollkeeper and dragon deliver a lost cloud.",
        characters=[Character(name="Ada", role="The bridge tollkeeper", facts=["Ada handles the map"]),
                    Character(name="Pip", role="A young dragon", facts=["Pip guides wind but cannot create water"])],
        world_rules=["Wind can guide clouds"], resolution="The orchard receives rain.",
        scenes=[Scene(number=i, location="The valley", goal=f"Complete delivery step {i}",
                      beats=[f"Take action {i}", f"Resolve obstacle {i}"], start_state=f"Step {i} is pending",
                      end_state=f"Step {i} is finished", obstacle="A practical obstacle blocks progress",
                      decision="Choose a route despite the cost", climax=i == 6) for i in range(1, 8)])


class FakeClient:
    """Tests orchestration and recovery, never claims to evaluate model prose."""
    def __init__(self, interrupt_at=None):
        self.interrupt_at = interrupt_at
        self.drafts = []
        self.unloaded = False

    def identity(self):
        return {"name": "test-local-model", "digest": "fixture"}

    def unload(self):
        self.unloaded = True

    def structured(self, schema, system, prompt, **kwargs):
        if schema is Outline:
            return plan()
        if schema is Review:
            return Review(approved=True, issues=[], continuity_summary="The planned action finished and the travelers continued.")
        if schema is Aside:
            return Aside(text="")
        return Framing(introduction="Evening. I'm Nox. Tonight we have a story about a misplaced cloud and someone who really ought to have checked the map before setting out.",
                       closing="That's our story tonight. I'm glad you stayed. Get comfortable and get some rest.",
                       description="An original fictional narrated story about an unlikely friendship.", tags=["fantasy"])

    def text(self, system, prompt, **kwargs):
        number = int(re.search(r'Current scene ONLY: \{"number":(\d+)', prompt)[1])
        if self.interrupt_at == number:
            raise Interrupted("paused")
        self.drafts.append(number)
        return "\n\n".join(" ".join(f"w{number}{p}{i:03d}" for i in range(95)) + "." for p in range(4))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "data")
        self.id = self.store.create(brief(), profile())
        self.output = redirect_stdout(StringIO())
        self.output.__enter__()

    def tearDown(self):
        self.output.__exit__(None, None, None)
        self.temp.cleanup()

    def test_frozen_profile_survives_later_config_changes(self):
        original = self.store.spec(self.id)["profile"]["voice"]["speed"]
        changed = profile()
        changed.voice.speed = 1.1
        self.assertEqual(self.store.spec(self.id)["profile"]["voice"]["speed"], original)
        path = self.store.folder(self.id) / "spec.json"
        data = json.loads(path.read_text())
        data["brief"]["seed"] += 1
        write_json(path, data)
        with self.assertRaisesRegex(ValueError, "snapshot changed"):
            self.store.spec(self.id)

    def test_framing_rejects_schema_fragments_in_tags(self):
        valid = FakeClient().structured(Framing, "", "").model_dump()
        valid["tags"] = ["fantasy", "]}}"]
        with self.assertRaises(ValueError):
            Framing.model_validate(valid)

    def test_completed_package_verification_detects_corruption_after_prompt_change(self):
        folder = self.store.folder(self.id)
        spec = self.store.spec(self.id)
        spec["prompt_version"] = "historical-writer"
        write_json(folder / "spec.json", spec)
        with self.store.db() as db:
            db.execute("UPDATE episodes SET spec_hash=?,status='awaiting_review' WHERE id=?", (digest(spec), self.id))
        names = ["script.txt", "script-package.json", "background.png", "thumbnail.jpg",
                 "video.mp4", "audio/narration.wav", "audio/audio.json"]
        for name in names:
            path = folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"integrity fixture")
        hashes = {name: file_hash(folder / name) for name in names}
        write_json(folder / "manifest.json", {"spec_hash": digest(spec), "duration_seconds": 1000,
            "files": hashes, "media": {"sha256": hashes["video.mp4"], "full_decode_passed": True,
                "metadata": {"size": [1920, 1080], "fps": 24, "codec": "h264",
                             "audio_codec": "aac", "duration": 1000}}})
        self.assertEqual(check_package(self.store, self.id)["editorial_review"], "required")
        (folder / "video.mp4").write_bytes(b"truncated")
        with self.assertRaisesRegex(ValueError, "Integrity failure"):
            check_package(self.store, self.id)

    def test_queue_claim_is_atomic(self):
        other = Store(self.store.root)
        with ThreadPoolExecutor(2) as workers:
            claims = list(workers.map(lambda store: store.claim(), [self.store, other]))
        self.assertEqual(claims.count(self.id), 1)
        self.assertEqual(claims.count(None), 1)

    def test_pause_cancel_and_recovery_require_explicit_resume(self):
        self.store.claim(self.id)
        self.store.request(self.id, "pause")
        with self.assertRaises(Interrupted):
            self.store.check_control(self.id)
        with ResourceLock(self.root / "lock") as lock:
            self.store.recover(lock)
        self.assertEqual(self.store.get(self.id)["status"], "paused")
        self.assertIsNone(self.store.claim())
        self.store.request(self.id, "resume")
        self.assertEqual(self.store.claim(), self.id)
        self.store.request(self.id, "cancel")
        with ResourceLock(self.root / "lock") as lock:
            self.store.recover(lock)
        self.assertEqual(self.store.get(self.id)["status"], "cancelled")

    def test_recovery_cannot_run_without_resource_lock(self):
        with self.assertRaises(Busy):
            self.store.recover(ResourceLock(self.root / "lock"))

    def test_cancellation_wins_a_completion_race(self):
        self.store.claim(self.id)
        self.store.request(self.id, "cancel")
        self.assertEqual(self.store.state(self.id, "awaiting_review", "complete"), "cancelled")
        self.assertEqual(self.store.get(self.id)["status"], "cancelled")

    def test_heavy_child_retains_lock_after_parent_releases_its_copy(self):
        path = self.root / "lock"
        with ResourceLock(path) as lock:
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.7)"], pass_fds=lock.pass_fds)
        try:
            with self.assertRaises(Busy):
                with ResourceLock(path):
                    pass
        finally:
            child.wait(timeout=5)
        with ResourceLock(path):
            pass

    def test_checkpoint_rejects_tampering_and_changed_inputs(self):
        cache = Checkpoints(self.root / "cache")
        cache.save("step", {"voice": "a"}, {"text": "original"})
        self.assertIsNone(cache.load("step", {"voice": "b"}))
        path = self.root / "cache/step.json"
        data = json.loads(path.read_text())
        data["data"]["text"] = "changed"
        write_json(path, data)
        self.assertIsNone(cache.load("step", {"voice": "a"}))

    def test_scene_checks_catch_known_phase_zero_failures(self):
        body = "\n\n".join(" ".join(f"item{p}_{i}" for i in range(95)) + "." for p in range(4))
        self.assertFalse(scene_issues(body, "", 387))
        self.assertTrue(scene_issues(body + " Nox.", "", 387))
        self.assertTrue(scene_issues(body, body, 387))
        self.assertTrue(scene_issues("A tiny draft.", "", 387))
        self.assertTrue(scene_issues(body + "\nNarrator: ending", "", 387))
        self.assertTrue(scene_issues(body + ' {aside: "hello"} ✅', "", 387))

    def test_outline_cannot_make_nox_a_character(self):
        data = plan().model_dump()
        data["characters"][0]["name"] = "Nox"
        with self.assertRaises(ValueError):
            Outline.model_validate(data)

    def test_exact_repetition_repair_preserves_other_text_and_dialogue(self):
        repeated = "And so, for the first time in months, the land answered—not with sound, but with silence, deep and full."
        novel = "Mira set the watering can down beside the gate."
        cleaned, removed = remove_repeated_narration(novel + "\n\n" + repeated, repeated)
        self.assertEqual(cleaned, novel)
        self.assertEqual(removed, [repeated])
        for dialogue in ('"Listen. ' + repeated + '"', "‘Listen. " + repeated + "’", "'Listen. " + repeated + "'"):
            self.assertEqual(remove_repeated_narration(dialogue, repeated), (dialogue, []))
        self.assertTrue(scene_issues(cleaned, repeated, 387))  # Still too short.

    def test_resuming_duplicate_only_failure_repairs_last_draft_and_reuses_six_scenes(self):
        repeated = "And so, for the first time in months, the land answered—not with sound, but with silence, deep and full."
        class EarlierRefrain(FakeClient):
            def text(self, *args, **kwargs):
                text = super().text(*args, **kwargs)
                return text + "\n\n" + repeated if self.drafts[-1] == 1 else text
        first = EarlierRefrain(interrupt_at=7)
        with self.assertRaises(Interrupted):
            Writer(self.store, self.id, self.store.spec(self.id), first).write()
        folder = self.store.folder(self.id) / "writing"
        before = {p.name: (file_hash(p), p.stat().st_mtime_ns) for p in folder.glob("scene-*.json")}
        class DuplicateFailure(EarlierRefrain):
            def text(self, *args, **kwargs):
                raise RuntimeError("Any newly generated draft should not be needed")
        # Persist a failed draft with the real final-scene checkpoint inputs.
        original_load = Checkpoints.load
        def with_failure(cache, name, inputs):
            if name == "draft-07-3":
                text = FakeClient().text("", 'Current scene ONLY: {"number":7}') + "\n\n" + repeated
                cache.save(name, inputs, {"text": text, "issues": ["A sentence repeats earlier narration: " + repeated]})
            return original_load(cache, name, inputs)
        with patch.object(Checkpoints, "load", with_failure):
            result = Writer(self.store, self.id, self.store.spec(self.id), DuplicateFailure()).write()
        self.assertEqual(len(before), 6)
        for name, identity in before.items():
            self.assertEqual((file_hash(folder / name), (folder / name).stat().st_mtime_ns), identity)
        self.assertNotIn(repeated, result["units"][7]["text"])

    def test_outline_rejects_language_leakage(self):
        data = plan().model_dump()
        data["premise"] += " 创造水"
        with self.assertRaises(ValueError):
            Outline.model_validate(data)

    def test_provider_only_sets_thinking_for_supported_models(self):
        for supports_thinking in (False, True):
            client = Ollama("fixture", self.root / f"requests-{supports_thinking}")
            client.supports_thinking = supports_thinking
            captured = []
            def respond(endpoint, payload):
                captured.append(payload)
                return {"response": json.dumps({"approved": True, "issues": [], "continuity_summary": "The traveler crossed the bridge."}), "done_reason": "stop"}
            client.api = respond
            client.structured(Review, "Review", "A short scene")
            self.assertEqual("think" in captured[0], supports_thinking)
            self.assertEqual(captured[0]["options"]["num_ctx"], 4096)

    def test_token_truncation_is_rejected_even_if_json_is_valid(self):
        client = Ollama("fixture", self.root / "truncated")
        calls = []
        def respond(endpoint, payload):
            calls.append(payload)
            return {"response": json.dumps({"approved": True, "issues": [], "continuity_summary": "A syntactically complete but truncated response."}), "done_reason": "length"}
        client.api = respond
        with self.assertRaisesRegex(RuntimeError, "three attempts"):
            client.structured(Review, "Review", "A short scene")
        self.assertEqual(len(calls), 3)

    def test_writing_recovers_accepted_scenes_after_interruption(self):
        first = FakeClient(interrupt_at=2)
        with self.assertRaises(Interrupted):
            Writer(self.store, self.id, self.store.spec(self.id), first).write()
        second = FakeClient()
        result = Writer(self.store, self.id, self.store.spec(self.id), second).write()
        self.assertEqual(second.drafts, [2, 3, 4, 5, 6, 7])
        self.assertEqual(len(result["units"]), 9)

    def test_missing_host_name_gets_a_bounded_framing_repair(self):
        class MissingHost(FakeClient):
            def __init__(self):
                super().__init__()
                self.frames = 0
            def structured(self, schema, *args, **kwargs):
                if schema is Framing:
                    self.frames += 1
                    if self.frames == 1:
                        return Framing(introduction="Welcome to another evening of remarkable stories.",
                                       closing="Thank you for staying. Rest well tonight.",
                                       description="An original fictional story for the narrator framing fixture.", tags=["fantasy"])
                return super().structured(schema, *args, **kwargs)
        client = MissingHost()
        result = Writer(self.store, self.id, self.store.spec(self.id), client).write()
        self.assertEqual(client.frames, 2)
        self.assertEqual(result["units"][0]["text"].count("Nox"), 1)

    def test_rejected_prose_is_never_accepted_as_a_checkpoint(self):
        class BadClient(FakeClient):
            def text(self, *args, **kwargs):
                return "Nox walked inside.\n\n" + super().text(*args, **kwargs)
        client = BadClient()
        with self.assertRaisesRegex(RuntimeError, "editorial attention"):
            Writer(self.store, self.id, self.store.spec(self.id), client).write()
        self.assertEqual(len(client.drafts), 3)
        self.assertFalse((self.store.folder(self.id) / "writing/scene-01.json").exists())

    def test_script_only_mode_unloads_model_and_waits_for_resume(self):
        self.store.claim(self.id)
        client = FakeClient()
        with patch("nox.pipeline.voice_identity", return_value={"cached": True}), patch("nox.pipeline.importlib.util.find_spec", return_value=object()):
            with ResourceLock(self.root / "lock") as lock:
                produce(self.store, self.id, lock, until="script", client_factory=lambda *args: client)
        self.assertTrue(client.unloaded)
        self.assertEqual(self.store.get(self.id)["status"], "paused")
        self.assertEqual(self.store.get(self.id)["stage"], "script_ready")
        self.assertFalse((self.store.folder(self.id) / "video.mp4").exists())

    def test_chunk_boundaries_preserve_words_and_voice_changes_invalidate_keys(self):
        text = "A short first sentence. Another complete sentence. A final sentence."
        chunks = chunks_for([{"section": "intro", "text": text}], {"voice": "a"}, limit=48)
        self.assertEqual(" ".join(c["text"] for c in chunks), text)
        self.assertTrue(all(c["text"].endswith(".") for c in chunks))
        changed = chunks_for([{"section": "intro", "text": text}], {"voice": "b"}, limit=48)
        self.assertNotEqual(chunks[0]["key"], changed[0]["key"])

    def test_child_pause_exit_is_not_reported_as_a_failed_media_job(self):
        self.store.claim(self.id)
        child_code = "from pathlib import Path; import sys; from nox.store import Store; Store(Path(sys.argv[1])).request(sys.argv[2], 'pause'); sys.exit(75)"
        with ResourceLock(self.root / "lock") as lock:
            with self.assertRaises(Interrupted) as result:
                run_process([sys.executable, "-c", child_code, str(self.store.root), self.id],
                            self.store, self.id, lock, self.root / "child.log", timeout=10)
        self.assertEqual(result.exception.status, "paused")

    def test_speech_never_starts_when_model_unload_fails(self):
        class UnloadFailure(FakeClient):
            def unload(self):
                raise RuntimeError("unload failed")
        client = UnloadFailure()
        self.store.claim(self.id)
        with patch("nox.pipeline.voice_identity", return_value={"cached": True}), patch("nox.pipeline.importlib.util.find_spec", return_value=object()), patch("nox.pipeline.run_process") as speech:
            with ResourceLock(self.root / "lock") as lock:
                with self.assertRaisesRegex(RuntimeError, "unload failed"):
                    produce(self.store, self.id, lock, client_factory=lambda *args: client)
            speech.assert_not_called()

    def test_duration_repairs_are_bounded_and_the_budget_survives_resume(self):
        client = FakeClient()
        spec = self.store.spec(self.id)
        with patch("nox.pipeline.voice_identity", return_value={"cached": True}), patch("nox.pipeline.importlib.util.find_spec", return_value=object()), patch("nox.pipeline.Writer") as writer, patch("nox.pipeline.run_process"), patch("nox.pipeline.assemble", return_value={"duration_seconds": 890, "sha256": "fixture"}) as audio, patch("nox.pipeline.render") as renderer:
            writer.return_value.write.return_value = {"title": "Duration fixture", "units": []}
            with ResourceLock(self.root / "lock") as lock:
                with self.assertRaisesRegex(RuntimeError, "after two script repairs"):
                    produce(self.store, self.id, lock, client_factory=lambda *args: client)
            self.assertEqual(audio.call_count, 3)
            renderer.assert_not_called()
            checkpoint = Checkpoints(self.store.folder(self.id)).load("duration", {"spec": spec})
            self.assertEqual(checkpoint["round"], 2)
            audio.reset_mock()
            with ResourceLock(self.root / "lock") as lock:
                with self.assertRaisesRegex(RuntimeError, "after two script repairs"):
                    produce(self.store, self.id, lock, client_factory=lambda *args: client)
            self.assertEqual(audio.call_count, 1)
            self.assertNotEqual(self.store.get(self.id)["status"], "awaiting_review")

    def test_speech_cache_rejects_corrupt_audio_and_assembly_preserves_timing(self):
        identity = {"voice": "fixture"}
        units = [{"section": "intro", "text": "Hello there."}, {"section": "scene-01", "text": "A different sentence."}]
        chunks = chunks_for(units, identity)
        folder = self.root / "audio"
        (folder / "chunks").mkdir(parents=True)
        for chunk in chunks:
            path = folder / "chunks" / (chunk["key"] + ".wav")
            with wave.open(str(path), "wb") as audio:
                audio.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
                audio.writeframes(struct.pack("<100h", *([100] * 100)))
            write_json(path.with_suffix(".json"), {"key": chunk["key"], "frames": 100, "peak": 0.01,
                       "sha256": file_hash(path), "text": chunk["text"], "identity": identity})
        result = assemble(folder, chunks, lambda: None)
        self.assertAlmostEqual(result["duration_seconds"], (200 + round(0.65 * SAMPLE_RATE)) / SAMPLE_RATE)
        self.assertGreater(result["chunks"][1]["start"], result["chunks"][0]["duration"])
        path = folder / "chunks" / (chunks[0]["key"] + ".wav")
        path.write_bytes(path.read_bytes()[:-3])
        self.assertIsNone(chunk_valid(folder / "chunks", chunks[0]))


if __name__ == "__main__":
    unittest.main()
