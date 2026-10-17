"""Full-length writing spike with a fixed outline to isolate model capability."""
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from benchmark_story import ROOT, api, snapshot
from monitor import MemoryMonitor

SCENES = [
    'Introduce Mara, the lonely librarian, her late grandmother Elin, and the snowbound library. A cat-sized blue dragon named Pip arrives at midnight to return a book borrowed a century ago. End with Mara inviting him inside.',
    'Mara and Pip examine the green-ribbon book. It contains an unfinished map of a village swallowed by winter. Elin left a note asking the next keeper to help Pip restore the village bell. Explain that Pip slept under an enchantment, not by choice.',
    'Mara and Pip follow the map through a hidden library doorway into the frozen village. Establish that the bell can awaken the village only when its missing clapper is returned before dawn. Show trust developing between the two travelers.',
    'Search the silent village and discover the clapper inside the old clockmakers workshop. Mara must decipher a simple clue Elin left. The winter enchantment tempts her with a vision of her grandmother, but Pip helps her recognize the illusion.',
    'Carrying the clapper, Mara and Pip cross a failing bridge to the bell tower. Pip fears his tiny flame is useless; Mara shows how a small steady flame can thaw the locked tower door. They arrive together before dawn.',
    'Mara and Pip restore and ring the bell. Resolve the enchantment: the village was held in a protective winter sleep after a magical storm, awaiting the returned bell part. People awaken safely. Elins map becomes complete. No new villain or unexplained rescue.',
    'Return Mara and Pip to the library. Reveal Elins final note expressing faith in Mara. Pip chooses to become her companion and help visitors find stories. Close all major threads with a warm, hopeful ending and one short Nox sign-off. Do not introduce a sequel hook.',
]


def main(resume=None):
    model = 'qwen3:4b-instruct-2507-q4_K_M'
    folder = Path(resume).resolve() if resume else ROOT / 'data' / 'phase0' / ('episode-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'outline.json').write_text(json.dumps(SCENES, indent=2))
    before = snapshot()
    monitor = MemoryMonitor(folder / ('memory-' + datetime.now(timezone.utc).strftime('%H%M%S') + '.jsonl'))
    monitor.start()
    started = time.monotonic()
    generated = []
    metrics = []
    try:
        for index, scene in enumerate(SCENES):
            scene_path = folder / f'scene-{index+1:02d}.txt'
            previous = generated[-1][-2400:] if generated else 'The story has not begun.'
            prompt = ('Write ONLY scene ' + str(index + 1) + ' of 7, in 340–380 English words. '
                      'Continue seamlessly from the previous scene; do not recap or add scene headings. '
                      'Use past tense, third person, sensory detail and natural dialogue. '
                      'Names stay fixed: Mara (human librarian), Elin (late grandmother), Pip (tiny blue dragon). '
                      'Do not resolve events assigned to later scenes.\nFULL OUTLINE:\n' +
                      '\n'.join(f'{i+1}. {s}' for i, s in enumerate(SCENES)) +
                      '\nPREVIOUS ENDING:\n' + previous + '\nCURRENT SCENE:\n' + scene)
            response = {'response': scene_path.read_text(), 'done_reason': 'reused_checkpoint'} if scene_path.exists() else api('generate', {'model': model, 'system': 'You write original stories narrated by Nox. Nox is the framing narrator only, never a character. Output only polished spoken story prose, no reasoning, asterisks or markdown.',
                                       'prompt': prompt, 'stream': False, 'keep_alive': '2m',
                                       'options': {'num_ctx': 4096, 'num_predict': 850, 'num_batch': 128, 'temperature': 0.75, 'seed': 42 + index}})
            content = response.get('response', '').strip()
            for repair in range(2):
                count = len(re.findall(r"\b[\w'-]+\b", content))
                if 330 <= count <= 430:
                    break
                (folder / f'scene-{index+1:02d}-before-repair-{repair+1}.txt').write_text(content)
                repair_response = api('generate', {
                    'model': model, 'stream': False, 'keep_alive': '2m',
                    'system': 'You edit narrated stories. Output only the revised scene, no headings, asterisks, notes, or reasoning.',
                    'prompt': f'Rewrite this {count}-word scene into 370–410 words. ' +
                              ('Expand it with concrete action, sensory details, and natural dialogue. ' if count < 330 else 'Remove repetition and excess description. ') +
                              'Preserve all names, facts, events and the final situation. Do not add plot events from other scenes. Keep the final sentence unchanged.\nSCENE PURPOSE:\n' + scene + '\nSCENE:\n' + content,
                    'options': {'num_ctx': 4096, 'num_predict': 850, 'num_batch': 128, 'temperature': 0.6, 'seed': 142 + index + repair},
                })
                content = repair_response.get('response', '').strip()
                metrics.append({'scene': index + 1, 'repair': repair + 1, **{k: v for k, v in repair_response.items() if k not in {'context', 'response'}}})
                print(f'Scene {index+1}: word-budget repair {repair+1}, now {len(re.findall(r"\b[\w\x27-]+\b", content))} words', flush=True)
            (folder / f'scene-{index+1:02d}.txt').write_text(content)
            generated.append(content)
            metrics.append({k: v for k, v in response.items() if k not in {'context', 'response'}})
            words = len(re.findall(r"\b[\w'-]+\b", content))
            print(f'Scene {index+1}/7 saved: {words} words; stop={response.get("done_reason")}', flush=True)
            if not content or response.get('done_reason') == 'length' or not 330 <= words <= 430:
                raise RuntimeError('Scene empty or token-truncated; inspect saved result before proceeding.')
        story = "Good evening. I'm Nox. Tonight, a very small dragon has a very overdue book to return.\n\n" + '\n\n'.join(generated)
        (folder / 'story.txt').write_text(story)
        results = {'model': model, 'words': len(re.findall(r"\b[\w'-]+\b", story)),
                   'wall_seconds': time.monotonic() - started, 'before': before, 'after': snapshot(),
                   'loaded_models': api('ps'), 'scenes': metrics,
                   'scope': 'Fixed-outline full-length writing spike; not yet the automatic outline pipeline.'}
        (folder / 'metrics.json').write_text(json.dumps(results, indent=2))
        print(json.dumps({k: results[k] for k in ['model', 'words', 'wall_seconds']}, indent=2), flush=True)
        print(f'Artifacts: {folder}', flush=True)
    finally:
        monitor.stop()
        api('generate', {'model': model, 'keep_alive': 0})
        print('Story model unloaded.', flush=True)
    return folder


if __name__ == '__main__':
    main()
