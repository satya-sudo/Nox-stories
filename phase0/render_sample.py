"""Render a simple local video from narration; no image or cloud service needed."""
import argparse
import json
import subprocess
import time
import wave
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('audio', type=Path)
    parser.add_argument('--encoder', choices=['libx264', 'h264_videotoolbox'], default='libx264')
    args = parser.parse_args()
    from PIL import Image, ImageDraw, ImageFont
    import imageio_ffmpeg
    folder = args.audio.resolve().parent
    background = folder / 'background.png'
    image = Image.new('RGB', (1920, 1080), '#101522')
    draw = ImageDraw.Draw(image)
    draw.ellipse((1360, 100, 1570, 310), fill='#c4c8dd')
    draw.text((150, 430), 'NOX STORIES', fill='#eee7d9', font=ImageFont.load_default(size=100))
    draw.text((155, 570), 'Phase 0 - local narration test', fill='#a8b0c4', font=ImageFont.load_default(size=38))
    image.save(background)
    with wave.open(str(args.audio)) as audio:
        duration = audio.getnframes() / audio.getframerate()
    output = folder / 'sample.mp4'
    command = [imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-hide_banner', '-loglevel', 'warning',
               '-loop', '1', '-framerate', '24', '-i', str(background), '-i', str(args.audio.resolve()),
               '-c:v', args.encoder, '-threads', '2']
    if args.encoder == 'libx264':
        command += ['-preset', 'veryfast', '-tune', 'stillimage', '-crf', '23']
    else:
        command += ['-b:v', '2500k']
    command += ['-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart',
                '-t', str(duration), str(output)]
    started = time.monotonic()
    subprocess.run(command, check=True, timeout=1800)
    # Decode the complete result to catch media errors, not just file existence.
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-i', str(output),
                    '-f', 'null', '-'], check=True, timeout=1800)
    reader = imageio_ffmpeg.read_frames(str(output))
    try:
        media = next(reader)
    finally:
        reader.close()
    if tuple(media['size']) != (1920, 1080) or abs(media['fps'] - 24) > 0.01:
        raise RuntimeError(f'Unexpected video dimensions/frame rate: {media}')
    if abs(media['duration'] - duration) > 0.25:
        raise RuntimeError(f'Video/audio duration differs: {media["duration"]} vs {duration}')
    metrics = {'source_duration_seconds': duration, 'render_and_validation_seconds': time.monotonic() - started,
               'encoder': args.encoder, 'output_bytes': output.stat().st_size, 'full_decode_passed': True,
               'video_metadata': media}
    (folder / 'render-metrics.json').write_text(json.dumps(metrics, indent=2))
    print(json.dumps(metrics, indent=2))


if __name__ == '__main__':
    main()
