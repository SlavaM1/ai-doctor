"""Restart only our verified host processes; preserve checkpoints, journals and logs."""
import os
from pathlib import Path
import signal
import subprocess
import time


root = Path(__file__).resolve().parents[1]
os.chdir(root)
for name, marker in [('runner', 'doctor.runner:app'), ('collector', 'doctor.collector')]:
    path = Path(f'.runtime/{name}.pid')
    if not path.exists():
        continue
    pid = int(path.read_text())
    process = Path(f'/proc/{pid}/cmdline')
    if process.exists() and marker in process.read_bytes().decode(errors='replace'):
        if Path(f'/proc/{pid}/cwd').resolve() != root:
            raise SystemExit('Refusing to stop a process from a different working directory')
        os.kill(pid, signal.SIGTERM)
        for _ in range(100):
            if not process.exists() or not process.read_bytes():
                break
            time.sleep(.1)
        else:
            raise SystemExit(f'{name} did not stop; inspect it manually')
    elif process.exists() and process.read_bytes():
        raise SystemExit(f'Refusing stale {name} PID pointing to another process')
    path.unlink(missing_ok=True)
subprocess.run(['bash', 'scripts/host-services.sh'], check=True)
