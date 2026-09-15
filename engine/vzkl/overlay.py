"""Build and manage the ephemeral patched-AVPBooter union overlay.

The genuine AVPBooter is never modified. We build a tiny HFS image holding a
patched copy, attach it (keeping the device node), and the app union-mounts that
device over the framework Resources directory. Mount/unmount are the only
privileged steps; the app runs them via an admin prompt. Everything reverts on
unmount/reboot.
"""
from __future__ import annotations
import hashlib
import os

from . import avpbooter
from .util import run

AVPBOOTER_NAME = "AVPBooter.vmapple2.bin"
AVPBOOTER_PATH = (
    "/System/Library/Frameworks/Virtualization.framework"
    "/Versions/A/Resources/" + AVPBOOTER_NAME
)
FRAMEWORK_RESOURCES = (
    "/System/Library/Frameworks/Virtualization.framework/Versions/A/Resources"
)
VOLNAME = "AVPOVER"

DEFAULT_DMG = os.path.expanduser(
    "~/Library/Application Support/vz-kext-loader/avpover.dmg"
)


class OverlayError(Exception):
    pass


def _read_source_avpbooter() -> tuple[bytes, str]:
    """Read the AVPBooter at the framework path and ensure it is patched.

    If an overlay is already mounted, this reads the already-patched bytes, which
    is fine — the result is a patched booter either way.
    """
    with open(AVPBOOTER_PATH, "rb") as f:
        data = f.read()
    patched, _callers, state = avpbooter.ensure_patched(data)
    return patched, state


def status() -> dict:
    """Report overlay mount + our device state."""
    mount_out = run(["/sbin/mount"]).stdout or ""
    mounted = any(
        FRAMEWORK_RESOURCES in ln and "union" in ln for ln in mount_out.splitlines()
    )
    # Find an attached device whose backing image is our dmg.
    device = _find_attached_device(DEFAULT_DMG)
    return {
        "mounted": mounted,
        "device": device or "",
        "dmg": DEFAULT_DMG,
        "framework_resources": FRAMEWORK_RESOURCES,
    }


def _find_attached_device(dmg_path: str) -> str | None:
    """Return the Apple_HFS partition device of the attached dmg, or None.

    `hdiutil info` groups each image as an `image-path` line followed by its
    `/dev/diskNsM  ...` partition rows; match our dmg then take its HFS row.
    """
    info = run(["hdiutil", "info"]).stdout or ""
    base = os.path.basename(dmg_path)
    dev = None
    ours = False
    for ln in info.splitlines():
        if ln.startswith("image-path"):
            ours = base in ln
        elif ours and "/dev/disk" in ln and "Apple_HFS" in ln:
            dev = ln.split()[0]
            ours = False
    return dev


def _detach_existing(dmg_path: str) -> None:
    dev = _find_attached_device(dmg_path)
    if dev:
        # Detach the whole-disk parent (strip sN).
        whole = dev
        if "s" in dev[len("/dev/disk"):]:
            whole = "/dev/disk" + dev[len("/dev/disk"):].split("s")[0]
        run(["hdiutil", "detach", whole, "-force"])


def build(dmg_path: str = DEFAULT_DMG) -> dict:
    """Create the overlay image with a patched AVPBooter and attach it.

    Returns a dict with the device node and the exact (privileged) mount/unmount
    argv for the app to run via an admin prompt. Non-privileged up to this point.
    """
    patched, state = _read_source_avpbooter()

    os.makedirs(os.path.dirname(dmg_path), exist_ok=True)
    _detach_existing(dmg_path)
    if os.path.exists(dmg_path):
        os.remove(dmg_path)

    cp = run([
        "hdiutil", "create", "-size", "16m", "-fs", "HFS+",
        "-volname", VOLNAME, dmg_path,
    ], timeout=60)
    if cp.returncode != 0:
        raise OverlayError(f"hdiutil create failed: {cp.stderr.strip()}")

    cp = run(["hdiutil", "attach", "-nobrowse", dmg_path], timeout=60)
    if cp.returncode != 0:
        raise OverlayError(f"hdiutil attach failed: {cp.stderr.strip()}")

    device = None
    mountpoint = None
    for ln in (cp.stdout or "").splitlines():
        parts = ln.split()
        if parts and parts[0].startswith("/dev/disk") and "Apple_HFS" in ln:
            device = parts[0]
            mountpoint = parts[-1] if parts[-1].startswith("/Volumes") else f"/Volumes/{VOLNAME}"
    if not device:
        # fall back: last /dev/disk line
        for ln in (cp.stdout or "").splitlines():
            if ln.strip().startswith("/dev/disk"):
                device = ln.split()[0]
        mountpoint = f"/Volumes/{VOLNAME}"
    if not device:
        raise OverlayError("could not determine attached device")

    dst = os.path.join(mountpoint or f"/Volumes/{VOLNAME}", AVPBOOTER_NAME)
    with open(dst, "wb") as f:
        f.write(patched)

    # Unmount the volume but KEEP the device node attached (union needs the
    # source device not already mounted).
    run(["diskutil", "unmount", device])

    sha = hashlib.sha256(patched).hexdigest()
    return {
        "dmg": dmg_path,
        "device": device,
        "avpbooter_state": state,
        "avpbooter_sha": sha,
        "mount_argv": [
            "/sbin/mount", "-t", "hfs", "-o", "union,nobrowse",
            device, FRAMEWORK_RESOURCES,
        ],
        "unmount_argv": ["/sbin/umount", FRAMEWORK_RESOURCES],
    }
