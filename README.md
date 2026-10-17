# Nox Stories

A local storytelling pipeline for 15–20 minute English narrated videos, with Nox as the recurring narrator. Story generation uses Ollama, narration uses Kokoro, and video assembly uses FFmpeg.

## Current status

Phase 0 contains feasibility benchmarks for an M2 Mac with 8 GB RAM. A full-length static-background video passed media validation at 16 minutes 17 seconds. American English Michael (`am_michael`) is the selected starting voice.

The story model remains provisional: memory headroom is tight and generated stories need stronger continuity and repetition checks. The production worker, dashboard, and YouTube publishing integration are planned but not implemented.

## Start here

- [Architecture and implementation plan](ARCHITECTURE.md)
- [Benchmark setup and commands](phase0/README.md)
- [Measured results and limitations](phase0/RESULTS.md)

Use Python 3.12 and a running local Ollama server. The setup instructions cover the project virtual environment, dependencies, and model download. Run one heavy benchmark at a time.

## Repository contents

The repository includes benchmark source, dependency manifests, setup documentation, and the selected voice configuration. Downloaded models, virtual environments, generated audio/video, temporary logs, and machine-specific preflight output stay local.

Finished videos are intended to require owner review before publishing.
