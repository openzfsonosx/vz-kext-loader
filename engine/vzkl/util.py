"""Small helpers shared across the engine."""
from __future__ import annotations
import subprocess
import shutil
from dataclasses import dataclass, asdict
from typing import Optional


def run(cmd, timeout: int = 20) -> subprocess.CompletedProcess:
    """Run a command list, capturing output; never raises on non-zero."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def have(binary: str) -> Optional[str]:
    """Return the full path to a binary on PATH, or None."""
    return shutil.which(binary)


def py_import_ok(module: str) -> bool:
    """True if a python module can be imported in this interpreter."""
    try:
        __import__(module)
        return True
    except Exception:
        return False


# Status levels for a requirement / step.
OK = "ok"        # satisfied
WARN = "warn"    # usable but attention needed
FAIL = "fail"    # blocks the workflow
INFO = "info"    # neutral/context


@dataclass
class Check:
    id: str
    label: str
    status: str            # OK | WARN | FAIL | INFO
    detail: str = ""       # human-readable current value / message
    fix: str = ""          # one-line remediation hint, empty if none
    blocking: bool = False  # a FAIL here stops the patch/boot flow

    def to_dict(self) -> dict:
        return asdict(self)
