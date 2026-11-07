import os
from pathlib import Path
import subprocess
import tempfile

repo = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    log = root / "log"
    systemctl = root / "systemctl"
    systemctl.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$LOG"\nif [ "$1" = is-active ]; then exit "$ACTIVE_STATUS"; fi\n'
    )
    systemctl.chmod(0o755)
    python = root / "python3"
    python.write_text(
        '#!/bin/sh\nprintf "backup\\n" >> "$LOG"\nexit "$BACKUP_STATUS"\n'
    )
    python.chmod(0o755)

    def run(active, backup):
        log.write_text("")
        env = dict(
            os.environ,
            PATH=f"{root}:" + os.environ["PATH"],
            LOG=str(log),
            ACTIVE_STATUS=str(active),
            BACKUP_STATUS=str(backup),
        )
        result = subprocess.run(["/bin/sh", str(repo / "deploy/backup.sh")], env=env)
        return result.returncode, log.read_text().splitlines()

    code, calls = run(0, 1)
    assert code != 0 and calls == [
        "is-active --quiet asterbook.service",
        "stop asterbook.service",
        "backup",
        "start asterbook.service",
    ]
    code, calls = run(3, 0)
    assert code == 0 and calls == ["is-active --quiet asterbook.service", "backup"]
