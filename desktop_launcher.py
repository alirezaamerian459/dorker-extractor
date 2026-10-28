from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent
    return Path(__file__).resolve().parent


def main() -> None:
    root = project_root()
    python = root / ".venv" / "Scripts" / "pythonw.exe"
    if not python.exists():
        python = root / ".venv" / "Scripts" / "python.exe"
    subprocess.Popen(
        [str(python), str(root / "main.py")],
        cwd=str(root),
        close_fds=True,
    )


if __name__ == "__main__":
    main()
