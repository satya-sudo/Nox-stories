# Phase 0 results — technical feasibility demonstrated

Measured on the owner's M2, 8 GB unified memory, macOS 26.2. These observations apply to the current running applications and memory conditions, not a clean-machine benchmark.

## Setup completed

- Ollama 0.40.5 installed and local API verified.
- `qwen3:4b-instruct-2507-q4_K_M` downloaded; Q4_K_M quantization and 4,096-token context confirmed by runtime.
- Isolated Python 3.12 environment created using an app-bundled runtime.
- Kokoro 0.9.4 and FFmpeg dependencies installed; exact resolved packages saved in `requirements.lock.txt`.
- FFmpeg lists software H.264, VideoToolbox H.264, and AAC encoders.

## Writing measurements

| Test | Words | Wall time including load | Generation tokens/second | Stop |
| --- | ---: | ---: | ---: | --- |
| Fantasy | 570 | 49.7 s | 23.9 | Normal |
| Horror | 604 | 46.4 s | 24.1 | Normal |
| Gentle | 684 | 41.5 s | 25.7 | Normal |

The requested range was 450–550 words. Both outputs exceeded it, so word budgeting must be enforced outside the prompt. Both included italic markup. The horror output made Nox a character; production prompts must explicitly separate narrator identity from story characters.

The fantasy story has a warm resolution; the horror story resolves its footsteps as an observatory-glass resonance. The gentle story confuses who bakes bread in its anonymous note exchange, so it does not pass continuity review as written. These are initial reading observations, not owner approval or proof of consistent editorial quality. The three measurements above used the initial prompt; after reading them, the test prompt was amended to explicitly separate Nox from story characters, reject asterisks, and reduce prompt batch size. Those changes have not yet been validated by a repeat of this exact three-story test.

## Memory observations

The first test began with approximately 7.9 GB of swap already used. During the run, swap rose to approximately 10.9 GB. Ollama reported approximately 3.18 GB for the loaded model, and process RSS samples reached approximately 3 GB. System memory-free percentage briefly fell to 2% while loading, then recovered to around 12–13% during generation.

The model completed the bounded test at usable speed, but memory headroom is tight. Do not interpret this as a no-swap configuration or attribute all existing swap to Ollama. Full-length writing and speech/render checks remain necessary before selecting the production default. Closing unused applications before normal production may improve headroom; no applications were closed by the benchmark.

## Voice auditions

| Voice | Audio duration | Wall time | Notes |
| --- | ---: | ---: | --- |
| British George | 86.8 s | 100.8 s | First run includes model/language setup and downloads |
| American Michael | 92.2 s | 27.3 s | Weights already cached; includes voice setup |

Both used the same 224-word passage, CPU execution, and speed 0.95. Michael's measured pace was approximately 146 words per minute; an initial 18-minute budget is approximately 2,625 words. Peak sample amplitude was below clipping in both auditions. These short measurements are not a full-length speed guarantee.

The owner selected **American English Michael** as Nox's starting voice. This is recorded in `selected_voice.json`.

## Full-length sample

Episode: `data/phase0/episode-20261017T160338Z/`.

| Measurement | Result |
| --- | --- |
| Raw script | 2,538 words across seven scenes |
| Spoken script | 2,534 words after formatting/speaker-label cleanup |
| Resumed writing and word-budget repairs | 269.8 s; excludes the earlier interrupted draft-writing segment |
| Narration | American English Michael, CPU, speed 0.95 |
| Actual narration duration | 977.225 s = 16 min 17 s; passes 15–20 minute target |
| Speech generation | 245.1 s; approximately 4× faster than playback |
| Speech chunks | 73; combined chunk text matches the normalized script |
| Peak speech sample amplitude | 0.682; below clipping |
| Video | 1920×1080, 24 fps, H.264/yuv420p + AAC |
| Render and full-decode validation | 101.8 s, software libx264, two encoding threads |
| Video duration | 977.23 s; agrees with narration to within 0.01 s |
| Video size | 15,642,949 bytes; approximately 15.6 MB |

The corrected static background was visually inspected, including a frame extracted from the encoded video at 500 seconds. A font that lacked an em dash produced a missing-glyph box in the first background; the label was changed to ASCII and only the render was rerun. The final file passes complete decoding and resolution/frame-rate/duration checks.

The writing run was interrupted after four scenes to switch to the owner's selected voice. Completed scene checkpoints were reused, and short scenes received at most two word-budget revisions. This exercised writing checkpoint recovery. Speech chunk reuse after interruption is not implemented in this benchmark harness; it belongs to the durable production worker.

During speech, sampled process RSS was approximately 1.6 GB and memory-free percentage was around 47% in one observed sample. These are sampled observations, not guaranteed peak figures. Ollama's server log confirms Apple M2 Metal inference for writing. Qwen was unloaded before speech, and speech exited before rendering.

## Decision and remaining work

**Hardware/media feasibility: demonstrated.** The available M2 with 8 GB can produce this complete narrated static-image video locally with stages serialized. This is one full-length run, not a sustained reliability benchmark or a guarantee of interactive responsiveness under other workloads.

**Voice: selected.** Keep Kokoro American Michael as Nox's starting voice. The full sample's actual pace was approximately 156 words/minute, compared with 146 in the short audition; use roughly 2,700–2,800 words as an initial 18-minute budget and always check actual audio duration.

**Story model: provisional.** Qwen 4B has usable generation speed, but memory headroom is tight and its prose needs stronger editorial controls. The full sample includes repeated narrator refrains, repeated metaphors, and events revealed ahead of their assigned scenes. Formatting cleanup does not fix those story problems. The sample is a hardware/media demonstration, not an approved YouTube episode.

Before channel-ready production, refine scene prompts and continuity/state tracking, validate repetition and narrator boundaries, and compare revised outputs or another small model if needed. The owner still needs to review a complete episode for narration and story quality. The dashboard, durable production queue, and YouTube integration have not been implemented.

The next implementation milestone is the Phase 1 pipeline, with editorial validation addressed before building the full dashboard. The benchmark environment currently uses an app-bundled Python runtime; install a standalone project runtime before packaging the application independently.

Raw outputs, runtime timings, and memory samples are in `data/phase0/`.
