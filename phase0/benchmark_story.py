"""Bounded local Ollama story smoke test; saves text and measured metrics."""
import argparse
import json
import re
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from monitor import MemoryMonitor

ROOT = Path(__file__).resolve().parents[1]


def api(endpoint, payload=None):
    request = urllib.request.Request(
        'http://127.0.0.1:11434/api/' + endpoint,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        return json.load(response)


def snapshot():
    result = {}
    for name, command in {
        'swap': ['/usr/sbin/sysctl', 'vm.swapusage'],
        'memory': ['/usr/bin/vm_stat'],
        'physical_memory': ['/usr/sbin/sysctl', 'hw.memsize'],
    }.items():
        proc = subprocess.run(command, capture_output=True, text=True, timeout=10)
        result[name] = (proc.stdout + proc.stderr).strip()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', default='qwen3:4b-instruct-2507-q4_K_M')
    parser.add_argument('--genre', choices=['fantasy', 'horror', 'gentle'], default='fantasy')
    args = parser.parse_args()
    premises = {
        'fantasy': 'A lonely keeper of a moonlit library discovers a small dragon returning overdue books. Warm mystery and a hopeful ending.',
        'horror': 'A night caretaker hears an extra set of footsteps in a closed observatory. Atmospheric suspense, no gore, and a complete explanation or resolution.',
        'gentle': 'An elderly baker and a shy new neighbor exchange anonymous notes. A gentle, sweet story with a satisfying ending.',
    }
    output = ROOT / 'data' / 'phase0' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True)
    before = snapshot()
    monitor = MemoryMonitor(output / 'memory.jsonl')
    monitor.start()
    started = time.monotonic()
    try:
        response = api('generate', {
            'model': args.model,
            'system': 'You write original English stories narrated by Nox, a calm, warm host. Nox is only the framing narrator: never make Nox a story character. Output only narration. Use clear spoken prose, consistent names, and a complete ending. Do not output reasoning, production notes, asterisks, or markdown.',
            'prompt': 'Write a complete 450–550 word short story. ' + premises[args.genre],
            'stream': False,
            'keep_alive': '2m',
            'options': {'num_ctx': 4096, 'num_predict': 1100, 'num_batch': 128, 'temperature': 0.8, 'seed': 42},
        })
        elapsed = time.monotonic() - started
        text = response.get('response', '')
        (output / 'story.txt').write_text(text)
        metrics = {key: value for key, value in response.items() if key not in {'response', 'context'}}
        tokens = response.get('eval_count', 0)
        seconds = response.get('eval_duration', 0) / 1e9
        metrics.update({
            'genre': args.genre, 'wall_seconds': elapsed,
            'words': len(re.findall(r"\b[\w'-]+\b", text)),
            'tokens_per_second': tokens / seconds if seconds else None,
            'before': before, 'after': snapshot(),
            'loaded_models': api('ps'),
            'scope': 'Short story smoke test; does not establish full-episode quality or peak memory.',
        })
        (output / 'metrics.json').write_text(json.dumps(metrics, indent=2))
        print(json.dumps({'output': str(output), 'words': metrics['words'], 'wall_seconds': elapsed,
                          'tokens_per_second': metrics['tokens_per_second'],
                          'done_reason': response.get('done_reason')}, indent=2), flush=True)
    finally:
        monitor.stop()
        api('generate', {'model': args.model, 'keep_alive': 0})
        print('Story model unloaded.', flush=True)


if __name__ == '__main__':
    main()
