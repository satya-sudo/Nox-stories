# Phase 1 validation — 20 October 2026

Hardware: the existing M2 Mac with 8 GB unified memory. Voice: Kokoro Michael (`am_michael`), speed 0.95. Heavy production stages run serially. This is a development validation record, not a publishing approval.

## Confirmed

- 25 automated tests passed, covering the durable queue, immutable snapshots, bounded model retries, writing recovery, inherited worker lock, speech integrity, subprocess controls, duration repair limits, cancellation races, malformed metadata, completed-package corruption, exact repetition repair and reuse of six accepted scenes after a final-scene failure.
- A real Kokoro speech interruption/resume exercise passed. A completed chunk retained its exact SHA-256 and modification time after resuming. Its 72.625-second static-background video passed full decode and stream checks. Test episode: `c2ef7aacf3894a6b887465b8f13ac0e8`, isolated under `data/media-smoke/`.
- Real story writing resumed after four accepted scenes. All four checkpoint hashes and modification times remained unchanged; subsequent scenes completed. Test episode: `b37a7d021ccf42e8a09d7f02af7ac055`.
- That episode's 2,711-word script synthesized to 1,095.675 seconds (18 minutes 15.675 seconds), without duration repair. Its 1080p/24 fps H.264/AAC video passed full decode and stream validation and stopped at `awaiting_review`.

## Story quality findings

The smaller comparison model, `llama3.2:3b`, completed seven scenes but changed character pronouns and physical details, introduced unrelated props, and repeated the resolution. Its own structured continuity reviews missed these errors. The episode is a technical fixture and needs substantive editorial repair before publication.

Earlier Qwen attempts exposed compressed prose and formatting leakage when long narration was requested through JSON. The current implementation requests plain prose for scenes and reserves JSON for plans and reviews. A fresh run with the default Qwen model and prompt version `phase1.7` produced a 359-word first scene at the requested length. The revised prompt removes a concrete prose example that leaked props and asks for explicit character pronouns and physical form in the plan. Llama has not replaced the configured default.

The Qwen comparison (`5bce59c3e023460cbd69fa37b7f8673d`) was deliberately paused during scene 2 after inspection showed weak outline progression and too much observation/waiting. It is not a completed episode or a passing acceptance result. The next writing improvement should validate and revise the outline before expanding it into narration, rather than spending speech/render time on a weak plot.

Passing media validation or a model review does not establish story quality. Nox's intended warm, dry humor is documented in `NOX.md`; a generated line can still miss that tone. Owner reading and listening review remains required.

## Remaining acceptance work

A user-run episode (`639f38ac87c04a3d9b44c221c0e5fcb9`) stopped after three final-scene drafts retained the same sentence from an earlier scene. The repair now removes exact repeated narration sentences, records raw/edited text and omissions, preserves quoted dialogue, and reruns length and continuity checks. Scene 7 passed after repair and a bounded length revision; all six earlier accepted scene files retained their hashes and modification times.

The repaired 2,474-word Qwen episode then completed at 931.975 seconds (15 minutes 31.975 seconds). Its 1080p H.264/AAC video passed full decode and packaging checks and reached `awaiting_review`. The owner reviewed the result as a starting point; story and narration quality still need improvement. Media completion does not count as editorial acceptance.

Produce and editorially inspect complete fantasy, horror and gentle episodes. The three-genre exit gate is not yet passed. The dashboard and publishing integration remain later phases.

Generated media, audit prompts, local databases and model caches are ignored by Git. No videos have been uploaded by this implementation.

During the end-of-session cleanup, the reviewed episode and its production records were retained locally. Older development episodes, smoke-test outputs, generated Phase 0 media and temporary logs were removed. The measurements above remain the historical validation record; deleted test IDs are no longer available to open or resume. Cached models and the Python environment were retained.
