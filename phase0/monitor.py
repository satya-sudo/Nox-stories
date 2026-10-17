"""Sample system memory during benchmarks; RSS is not total unified GPU memory."""
import json
import subprocess
import threading
import time


class MemoryMonitor:
    def __init__(self, path):
        self.path = path
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.sample, daemon=True)

    def sample(self):
        started = time.monotonic()
        with self.path.open('w') as log:
            while not self.stop_event.is_set():
                row = {'elapsed_seconds': time.monotonic() - started}
                for name, command in {
                    'swap': ['/usr/sbin/sysctl', 'vm.swapusage'],
                    'memory_pressure': ['/usr/bin/memory_pressure', '-Q'],
                    'processes': ['/bin/ps', '-axo', 'pid,rss,comm'],
                }.items():
                    try:
                        result = subprocess.run(command, capture_output=True, text=True, timeout=5)
                        value = (result.stdout + result.stderr).strip()
                        if name == 'processes':
                            value = '\n'.join(line for line in value.splitlines() if any(word in line.lower() for word in ['ollama', 'python', 'ffmpeg', 'rss']))
                        row[name] = value
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        row[name] = str(exc)
                log.write(json.dumps(row) + '\n')
                log.flush()
                self.stop_event.wait(5)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=20)
