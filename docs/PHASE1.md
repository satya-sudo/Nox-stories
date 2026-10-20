# Phase 1 — local production pipeline

This phase adds a command-line production queue. The dashboard and YouTube integration belong to later phases. The new pipeline lives in `nox/`; `phase0/` remains the original benchmark record.

## Narrator

[Nox's character](NOX.md) is a warm, dryly funny night owl with a soft heart. The canonical profile is `config/nox.v1.json`. Michael remains the English voice. The host stays outside the plot and saves his jokes for suitable moments.

Each episode freezes the profile, brief, selected model name, and prompt version. Changing the source profile affects new episodes. Editing a frozen episode snapshot is rejected; create another episode to try changed settings.

## Setup

Run from this source checkout with Python 3.12. On the existing development laptop, the `.venv` and models are already present.

```sh
python3.12 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
ollama pull qwen3:4b-instruct-2507-q4_K_M
```

Start Ollama. Production speech uses cached weights offline. On a fresh setup, run the voice audition once to populate the project cache and install the English language model:

```sh
./.venv/bin/python phase0/benchmark_voice.py --voices am_michael
./.venv/bin/python -m nox doctor
```

The default storage directory is `data/`. Use `--data-root PATH` before the subcommand to select another local database and artifact directory. All workers from this checkout still share the same resource lock.

## Create and run

```sh
./.venv/bin/python -m nox profile
./.venv/bin/python -m nox create --brief config/briefs/fantasy.json
./.venv/bin/python -m nox list
./.venv/bin/python -m nox worker --once
```

`create` prints an episode ID. `worker --once` handles the oldest queued episode. Without `--once`, the worker drains the current queue, one episode at a time. `--watch` also waits for new jobs. A failed episode is not automatically retried forever.

To work on one specific episode, or inspect its script before speech:

```sh
./.venv/bin/python -m nox worker --episode EPISODE_ID --until script
./.venv/bin/python -m nox status EPISODE_ID
./.venv/bin/python -m nox events EPISODE_ID
./.venv/bin/python -m nox resume EPISODE_ID
./.venv/bin/python -m nox worker --episode EPISODE_ID
```

Other briefs are in `config/briefs/horror.json` and `config/briefs/gentle.json`. They target 18 minutes; the accepted measured range is 15–20 minutes.

## Pause, resume and cancel

In another terminal:

```sh
./.venv/bin/python -m nox pause EPISODE_ID
./.venv/bin/python -m nox resume EPISODE_ID
./.venv/bin/python -m nox cancel EPISODE_ID
```

Writing pauses after the active model request returns; accepted scenes remain reusable. Speech checks controls at chunk boundaries. A media subprocess is terminated on pause or cancel, and its partial video is never accepted as a finished package. Resume a paused or failed episode, then run a worker again.

An OS lock prevents two production workers from running heavy stages simultaneously. Long-running children inherit the lock: killing a parent does not permit a second worker to overlap an orphaned speech/render process. Once the lock is available again, a worker can recover stale `running` jobs. Do not run the independent Phase 0 benchmarks or interactive model workloads alongside production on this 8 GB machine.

## Pipeline

1. **Preflight:** validate the immutable snapshot, free storage, local model, dependencies, and cached voice revision. Pin actual model identities for subsequent resumes.
2. **Outline:** generate a structured plan with fixed characters, world rules, scene states, obstacles, decisions and a resolution.
3. **Scenes:** draft plain prose using current scene details, fixed facts, the prior accepted state summary and a short transition. Future discoveries are reserved for the reviewer rather than shown to the writer. Outline/review contracts use JSON; long-form narration does not.
4. **Checks:** remove exact repeated narration sentences before validation, preserving quoted dialogue and recording the original text and removed sentences. Then reject narrator labels, remaining repeats, short/long drafts, stock refrains and obvious padding. A structured model review checks the edited text for continuity and required beats. There are at most three scene drafts; failures remain available for editorial attention. On resume, a last failed draft whose only issue was repetition can go through this repair and all normal checks without another prose-generation request.
5. **Framing:** generate Nox's opening and closing separately. Host asides are allowed only in designated setup scenes, never in a climax or final two scenes.
6. **Speech:** unload the story model, start a separate CPU speech process, synthesize sentence/paragraph chunks, and atomically save each valid WAV and identity/hash record. Resume reuses only matching, intact chunks.
7. **Audio:** assemble a measured timeline with short deliberate pauses. If outside 900–1,200 seconds, revise the word budget and resynthesize changed text, up to two duration-repair rounds. No silence padding or automatic voice change.
8. **Media:** build a simple title background and thumbnail, render 1080p/24 fps H.264/AAC, normalize audio during encoding, fully decode the video with error checking, and inspect stream metadata and duration agreement.
9. **Package:** write file hashes and provenance, then stop at `awaiting_review`. There is no publishing command in this phase.

Checkpoint reuse depends on matching inputs and output hashes. Accepted scene context is part of downstream inputs, so changes cannot silently reuse an incompatible continuation. Speech keys include text, voice settings, cached model revision and library versions.

## Outputs

```text
data/
  nox.sqlite3
  episodes/EPISODE_ID/revisions/1/
    spec.json
    runtime.json
    requests/                  local prompt/response audit records
    writing/                   accepted scenes, drafts, decisions
    script.txt
    script-package.json
    audio/chunks/              reusable WAVs and integrity records
    audio/narration.wav
    audio/audio.json           measured timeline
    background.png
    thumbnail.jpg
    video.mp4
    manifest.json
```

Source and documentation can be committed. Model caches, the database, audit requests, generated artifacts and logs remain local under existing ignore rules.

## Verification and limits

```sh
./.venv/bin/python -m unittest discover -s tests -v
```

Tests cover immutable snapshots, concurrent queue claims, interrupted writing recovery, bounded rejection, inherited resource locking, corrupted checkpoint/audio rejection, chunk text coverage and timeline assembly. They use test fixtures rather than claiming to evaluate real model creativity.

An explicit real-engine smoke test is also available:

```sh
./.venv/bin/python tests/live_media_smoke.py
```

It uses a short handwritten Nox acting passage, requests a pause after the first completed speech chunk, resumes, verifies byte hashes and timestamps of preserved chunks, and renders/decodes a short clip. It is deliberately isolated under `data/media-smoke/` and never counts as a full-length production acceptance episode.

Live acceptance still requires three complete 15–20 minute videos across fantasy, horror and gentle stories, plus a real speech interruption/resume exercise. Model reviews are heuristics from the same small model; owner reading and listening review is still essential. API/dashboard endpoints, approval UI, music, captions, custom background management and YouTube publishing are not implemented here.

Verify the recorded media checks and current package hashes with `./.venv/bin/python tests/check_acceptance.py EPISODE_ID`. Add three completed episode IDs and `--all-genres` for the full genre gate. This reads the worker's full-decode record and checks artifact integrity; it never grants editorial approval.

This is a source-checkout workflow. Standalone application distribution and model provisioning are separate work. SQLite schema version 1 is explicit; incompatible future schema versions are rejected rather than guessed.

Structured model contracts use Ollama's [schema output support](https://docs.ollama.com/capabilities/structured-outputs) and Pydantic validation.
