"""Host requirement checks for vz-kext-loader.

Each check returns a Check(id, label, status, detail, fix, blocking). The set is
rendered by the app as green/amber/red rows. Nothing here modifies host state.
"""
from __future__ import annotations
import os
import platform
import plistlib

from .util import run, have, py_import_ok, Check, OK, WARN, FAIL, INFO

AVPBOOTER = (
    "/System/Library/Frameworks/Virtualization.framework"
    "/Versions/A/Resources/AVPBooter.vmapple2.bin"
)
FRAMEWORK_RESOURCES = (
    "/System/Library/Frameworks/Virtualization.framework"
    "/Versions/A/Resources"
)
UTMCTL = "/Applications/UTM.app/Contents/MacOS/utmctl"


def _csrutil_field(which: str) -> str:
    """Return a lowercased csrutil status string, or '' on error.

    which == 'status'             -> `csrutil status`  (SIP line)
    which == 'authenticated-root' -> `csrutil authenticated-root status`
    """
    cmd = ["csrutil", "status"] if which == "status" else ["csrutil", which, "status"]
    cp = run(cmd)
    return (cp.stdout or "").strip().lower()


def check_arch() -> Check:
    m = platform.machine()
    if m == "arm64":
        return Check("arch", "Apple Silicon (arm64)", OK, detail=m)
    return Check(
        "arch", "Apple Silicon (arm64)", FAIL, detail=m,
        fix="This tool patches arm64 boot objects; an Apple Silicon host is required.",
        blocking=True,
    )


def check_macos() -> Check:
    ver = platform.mac_ver()[0] or "?"
    return Check("macos", "Host macOS version", INFO, detail=ver)


def check_python() -> Check:
    v = platform.python_version()
    return Check("python", "python3", OK, detail=v)


def check_pyimg4() -> Check:
    if py_import_ok("pyimg4"):
        try:
            import pyimg4  # noqa
            v = getattr(pyimg4, "__version__", "installed")
        except Exception:
            v = "installed"
        return Check("pyimg4", "pyimg4 (img4 tooling)", OK, detail=str(v))
    return Check(
        "pyimg4", "pyimg4 (img4 tooling)", FAIL, detail="not importable",
        fix="pip3 install --user pyimg4", blocking=True,
    )


def check_capstone() -> Check:
    if py_import_ok("capstone"):
        return Check("capstone", "capstone (disassembler)", OK, detail="installed")
    return Check(
        "capstone", "capstone (disassembler)", FAIL, detail="not importable",
        fix="pip3 install --user capstone", blocking=True,
    )


def check_radare2() -> Check:
    p = have("r2") or have("radare2")
    if p:
        cp = run([p, "-v"])
        first = (cp.stdout or cp.stderr or "").splitlines()
        ver = first[0] if first else "installed"
        return Check("radare2", "radare2 (symbol xrefs)", OK, detail=ver)
    return Check(
        "radare2", "radare2 (symbol xrefs)", FAIL, detail="not on PATH",
        fix="brew install radare2", blocking=True,
    )


def check_utm() -> Check:
    if os.path.exists(UTMCTL):
        cp = run([UTMCTL, "--version"])
        detail = (cp.stdout or "").strip() or "installed"
        return Check("utm", "UTM (utmctl)", OK, detail=detail)
    return Check(
        "utm", "UTM (utmctl)", FAIL, detail="UTM.app not found",
        fix="Install UTM (the Apple-backend build) from https://mac.getutm.app",
        blocking=True,
    )


def check_avpbooter() -> Check:
    if os.path.exists(AVPBOOTER):
        try:
            sz = os.path.getsize(AVPBOOTER)
        except OSError:
            sz = 0
        return Check("avpbooter", "AVPBooter present", OK,
                     detail=f"{AVPBOOTER} ({sz} bytes)")
    return Check(
        "avpbooter", "AVPBooter present", FAIL,
        detail="AVPBooter.vmapple2.bin not found",
        fix="Requires Virtualization.framework with the vmapple2 booter (macOS 13+).",
        blocking=True,
    )


def check_authenticated_root() -> Check:
    """Union-mounting over the sealed framework path needs Authenticated Root off."""
    s = _csrutil_field("authenticated-root")
    if "disabled" in s:
        return Check("authroot", "Host Authenticated Root disabled", OK,
                     detail="disabled (union mount over framework allowed)")
    if "enabled" in s:
        return Check(
            "authroot", "Host Authenticated Root disabled", FAIL,
            detail="enabled",
            fix="In Recovery: csrutil authenticated-root disable  (needed to overlay AVPBooter)",
            blocking=True,
        )
    return Check("authroot", "Host Authenticated Root disabled", WARN,
                 detail=s or "unknown",
                 fix="Could not read csrutil authenticated-root status.")


def check_sip() -> Check:
    s = _csrutil_field("status")
    if "disabled" in s:
        return Check("sip", "Host SIP", INFO, detail="disabled")
    if "enabled" in s:
        # SIP being on is not strictly required for the overlay, but auth-root
        # (checked separately) usually implies SIP off. Surface as info/warn.
        return Check("sip", "Host SIP", WARN, detail="enabled",
                     fix="Typically disabled alongside Authenticated Root for this workflow.")
    return Check("sip", "Host SIP", INFO, detail=s or "unknown")


def check_overlay_mounted() -> Check:
    """Report whether a union overlay is currently mounted over Resources."""
    cp = run(["/sbin/mount"])
    mounted = any(
        FRAMEWORK_RESOURCES in line and "union" in line
        for line in (cp.stdout or "").splitlines()
    )
    if mounted:
        return Check("overlay", "AVPBooter overlay mounted", OK,
                     detail="union overlay active over framework Resources")
    return Check("overlay", "AVPBooter overlay mounted", INFO,
                 detail="not mounted",
                 fix="The app mounts this before booting a VM; reverts on unmount/reboot.")


ALL_CHECKS = [
    check_arch,
    check_macos,
    check_python,
    check_pyimg4,
    check_capstone,
    check_radare2,
    check_utm,
    check_avpbooter,
    check_authenticated_root,
    check_sip,
    check_overlay_mounted,
]


def run_all() -> dict:
    checks = [c().to_dict() for c in ALL_CHECKS]
    blocking_fail = any(c["status"] == FAIL and c["blocking"] for c in checks)
    return {
        "checks": checks,
        "ready": not blocking_fail,
        "summary": _summary(checks),
    }


def _summary(checks) -> str:
    n_fail = sum(1 for c in checks if c["status"] == FAIL)
    n_warn = sum(1 for c in checks if c["status"] == WARN)
    if n_fail:
        return f"{n_fail} blocking issue(s), {n_warn} warning(s)"
    if n_warn:
        return f"ready with {n_warn} warning(s)"
    return "all host requirements satisfied"
