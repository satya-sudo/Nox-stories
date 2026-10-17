# Phase 0: local feasibility tests

These are benchmark tools, not the production application. No YouTube integration exists here.

## Environment

Run commands from the Nox Stories directory. Use Python 3.12 and a project-local virtual environment. The benchmark was run on macOS; its memory sampler uses macOS utilities.

With Python 3.12 installed, create the environment before running the commands below:

```sh
python3.12 -m venv .venv
```

Install and start Ollama before writing benchmarks. Models remain in Ollama's normal storage. Voice model caches are directed into `.runtime/` inside this project. FFmpeg and the speech library are installed through the Python dependencies.

## Commands

```sh
./.venv/bin/python -m pip install -r phase0/requirements.txt
ollama pull qwen3:4b-instruct-2507-q4_K_M
./.venv/bin/python phase0/benchmark_story.py --genre fantasy
./.venv/bin/python phase0/benchmark_story.py --genre horror
./.venv/bin/python phase0/benchmark_story.py --genre gentle
./.venv/bin/python phase0/benchmark_voice.py
./.venv/bin/python phase0/benchmark_episode.py
./.venv/bin/python phase0/run_full_sample.py --voice am_michael
```

Voice tests download Kokoro weights/selected voices on their first run, and may fetch the English linguistic model required by Misaki. Use one heavy benchmark at a time. Story scripts unload the LLM when complete. Do not run narration while a story benchmark is active.

To narrate a generated story, pass its actual path:

```sh
./.venv/bin/python phase0/benchmark_voice.py --text data/phase0/EPISODE_FOLDER/story.txt --voices am_michael --output data/phase0/EPISODE_FOLDER/audio
./.venv/bin/python phase0/render_sample.py data/phase0/EPISODE_FOLDER/audio/am_michael/narration.wav
```

Output is kept in `data/phase0/`. Voice metrics record measured duration and synthesis time; story metrics include model timing and before/after memory snapshots. Writing benchmarks also sample memory pressure, swap, and relevant process RSS every five seconds into `memory.jsonl`. Sampling can miss brief peaks, and RSS does not capture all unified GPU memory. Rendering performs a complete decode check, but final stream metadata verification using ffprobe still belongs to the production pipeline.

## Completion criteria

- Short stories in three genres finish without truncation and pass human reading for coherence.
- Owner auditions candidate voices and selects or rejects them.
- A full-length story produces 900–1,200 seconds of real narration at a natural speed.
- Its 1080p static-background video renders and decodes without errors.
- Observe memory pressure and benchmark runtime; record limitations before selecting the production defaults.

The fixed outline in `benchmark_episode.py` deliberately isolates full-length writing capability. It does not establish automatic outline quality. It saves scene checkpoints and makes at most two targeted word-budget repairs per scene. Resume a saved writing run with `run_full_sample.py --resume data/phase0/EPISODE_FOLDER --voice am_michael`. A second narration run may be necessary after measuring actual full-story duration.
