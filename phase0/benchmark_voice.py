"""Audition Kokoro voices or narrate an existing English script on CPU."""
import argparse
import atexit
import gc
import json
import os
import re
import time
from pathlib import Path
from monitor import MemoryMonitor

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault('HF_HOME', str(ROOT / '.runtime' / 'huggingface'))
os.environ.setdefault('XDG_CACHE_HOME', str(ROOT / '.runtime' / 'cache'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--text', type=Path, default=ROOT / 'phase0' / 'audition.txt')
    parser.add_argument('--voices', nargs='+', default=['bm_george', 'am_michael'])
    parser.add_argument('--speed', type=float, default=0.95)
    parser.add_argument('--output', type=Path, default=ROOT / 'data' / 'phase0' / 'voices')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    monitor = MemoryMonitor(args.output / 'memory.jsonl')
    monitor.start()
    atexit.register(monitor.stop)
    import espeakng_loader
    os.environ['PHONEMIZER_ESPEAK_LIBRARY'] = espeakng_loader.get_library_path()
    os.environ['PHONEMIZER_ESPEAK_DATA_PATH'] = espeakng_loader.get_data_path()
    import torch
    import soundfile as sf
    from kokoro import KPipeline
    torch.set_num_threads(2)
    args.output.mkdir(parents=True, exist_ok=True)
    original_text = args.text.read_text()
    # Formatting and an isolated speaker label are not spoken narration.
    text = original_text.replace('*', '')
    text = re.sub(r'(?m)^\s*Nox[.:]\s*$', '', text)
    (args.output / 'spoken_text.txt').write_text(text)
    for voice in args.voices:
        started = time.monotonic()
        language = voice[0]
        pipeline = KPipeline(lang_code=language, device='cpu', repo_id='hexgrad/Kokoro-82M')
        voice_dir = args.output / voice
        voice_dir.mkdir(exist_ok=True)
        chunks = []
        frames = 0
        peak = 0.0
        with sf.SoundFile(voice_dir / 'narration.wav', mode='w', samplerate=24000, channels=1, subtype='PCM_16') as combined:
            for index, (spoken, phonemes, audio) in enumerate(pipeline(text, voice=voice, speed=args.speed, split_pattern=r'\n+')):
                if audio is None or len(audio) == 0:
                    raise RuntimeError(f'Empty speech chunk {index}')
                samples = audio.detach().cpu().numpy() if hasattr(audio, 'detach') else audio
                peak = max(peak, float(abs(samples).max()))
                sf.write(voice_dir / f'chunk-{index:03d}.wav', samples, 24000)
                combined.write(samples)
                chunks.append({'index': index, 'text': spoken, 'start_seconds': frames / 24000, 'duration_seconds': len(samples) / 24000})
                frames += len(samples)
                print(f'{voice}: saved chunk {index + 1}, total {frames / 24000:.1f}s', flush=True)
        elapsed = time.monotonic() - started
        metrics = {'voice': voice, 'speed': args.speed, 'device': 'cpu', 'sample_rate': 24000,
                   'duration_seconds': frames / 24000, 'wall_seconds': elapsed,
                   'real_time_factor': elapsed / (frames / 24000), 'sample_peak': peak,
                   'source': str(args.text.resolve()), 'normalization_changed_text': text != original_text,
                   'spoken_words': len(re.findall(r"\b[\w'-]+\b", text)), 'chunks': chunks}
        (voice_dir / 'metrics.json').write_text(json.dumps(metrics, indent=2))
        print(json.dumps({key: value for key, value in metrics.items() if key != 'chunks'}, indent=2), flush=True)
        del pipeline
        gc.collect()
    monitor.stop()


if __name__ == '__main__':
    main()
