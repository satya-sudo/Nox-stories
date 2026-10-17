"""Sequential full episode spike: local writing, local voice, local render."""
import json
import argparse
import subprocess
import sys

from benchmark_episode import main as write_episode
from benchmark_story import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--resume')
    parser.add_argument('--voice', default='am_michael')
    args = parser.parse_args()
    folder = write_episode(args.resume)
    subprocess.run([sys.executable, str(ROOT / 'phase0' / 'benchmark_voice.py'),
                    '--text', str(folder / 'story.txt'), '--voices', args.voice,
                    '--output', str(folder / 'audio')], check=True)
    audio_folder = folder / 'audio' / args.voice
    metrics = json.loads((audio_folder / 'metrics.json').read_text())
    duration = metrics['duration_seconds']
    print(f'Measured narration: {duration/60:.2f} minutes. Duration target passed: {900 <= duration <= 1200}', flush=True)
    subprocess.run([sys.executable, str(ROOT / 'phase0' / 'render_sample.py'),
                    str(audio_folder / 'narration.wav')], check=True)
    summary = {'episode_folder': str(folder), 'narration_seconds': duration,
               'duration_target_passed': 900 <= duration <= 1200,
               'video': str(audio_folder / 'sample.mp4'),
               'voice': args.voice,
               'voice_status': 'Owner selected American English Michael as starting voice.' if args.voice == 'am_michael' else 'Provisional benchmark voice.',
               'editorial_status': 'Needs reading and listening review.'}
    (folder / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__':
    main()
