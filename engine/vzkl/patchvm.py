"""Orchestrate the guest-side patch of a VM: LLB (AuxiliaryStorage), main-OS
Preboot iBoot+kernelcache, and Recovery iBoot+kernelcache. Keeps a backup of
every original and a manifest so `unpatch` cleanly reverses it.

Runs as root (the app invokes it via one admin prompt) because Preboot/Recovery
files are root-owned; set PYTHONPATH to the user site-packages so pyimg4/capstone
import under root.

Backups:
  * AuxiliaryStorage (a user-owned bundle file): backed up under
    <bundle>/Data/.vzkl-backup/.
  * Preboot/Recovery files (inside the disk image): backed up in place as
    "<name>.vzkl-orig" so they travel with the VM.
  * A manifest at <bundle>/Data/.vzkl-backup/manifest.json records every entry.
"""
from __future__ import annotations
import datetime
import hashlib
import json
import os
import shutil
import time

from . import vms
from .util import run
from .patch import auxstorage, iboot, kernelcache

BACKUP_SUBDIR = ".vzkl-backup"
MANIFEST = "manifest.json"


class PatchVMError(Exception):
    pass


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _bundle(uuid: str) -> dict:
    for vm in vms.discover()["vms"]:
        if vm["uuid"].upper() == uuid.upper():
            if not vm["bundle_path"]:
                raise PatchVMError(f"bundle for {uuid} not found in search paths")
            return vm
    raise PatchVMError(f"VM {uuid} not found")


def _backup_dir(bundle_path: str) -> str:
    d = os.path.join(bundle_path, "Data", BACKUP_SUBDIR)
    os.makedirs(d, exist_ok=True)
    return d


# --- disk image attach / volume discovery -----------------------------------

def _attach(img: str) -> list[dict]:
    """Attach a disk image (no auto-mount browsing); return its entities."""
    cp = run(["hdiutil", "attach", "-nobrowse", "-plist", img], timeout=120)
    if cp.returncode != 0:
        raise PatchVMError(f"hdiutil attach failed: {cp.stderr.strip()}")
    import plistlib
    info = plistlib.loads(cp.stdout.encode())
    ents = []
    for e in info.get("system-entities", []):
        ents.append({"dev": e.get("dev-entry"),
                     "mountpoint": e.get("mount-point"),
                     "content": e.get("content-hint", "")})
    return ents


def _detach(dev: str) -> None:
    run(["hdiutil", "detach", dev, "-force"], timeout=60)


def _find_os_disk(bundle_path: str):
    """Attach candidate images; return (whole_dev, preboot_mnt, recovery_dev,
    recovery_mnt_or_None) for the image that has a Preboot + Recovery volume."""
    data = os.path.join(bundle_path, "Data")
    imgs = [os.path.join(data, f) for f in os.listdir(data) if f.endswith(".img")]
    for img in sorted(imgs, key=os.path.getsize, reverse=True):
        ents = _attach(img)
        preboot = next((e for e in ents if e["mountpoint"]
                        and os.path.basename(e["mountpoint"]) == "Preboot"), None)
        recovery = next((e for e in ents if e["mountpoint"]
                         and os.path.basename(e["mountpoint"]) == "Recovery"), None)
        if preboot:
            whole = next((e["dev"] for e in ents
                          if e["content"] == "GUID_partition_scheme"), ents[0]["dev"])
            return {"img": img, "whole_dev": whole, "ents": ents,
                    "preboot_mnt": preboot["mountpoint"],
                    "recovery": recovery}
        # not the OS disk; detach and try next
        whole = next((e["dev"] for e in ents
                      if e["content"] == "GUID_partition_scheme"), None)
        if whole:
            _detach(whole)
    raise PatchVMError("no disk image with a Preboot volume found")


def _active_nsih(preboot_mnt: str) -> tuple[str, str]:
    """Return (guid_dir, nsih) for the active boot object set."""
    for guid in os.listdir(preboot_mnt):
        bootdir = os.path.join(preboot_mnt, guid, "boot")
        active = os.path.join(bootdir, "active")
        if os.path.exists(active):
            nsih = open(active).read().strip()
            if nsih and os.path.isdir(os.path.join(bootdir, nsih)):
                return os.path.join(preboot_mnt, guid), nsih
    raise PatchVMError("could not find active NSIH boot dir on Preboot")


def _iboot_path(root: str, guid_dir: str, nsih: str) -> str:
    return os.path.join(root, os.path.basename(guid_dir), "boot", nsih,
                        "usr", "standalone", "firmware", "iBoot.img4")


def _kc_path(root: str, guid_dir: str, nsih: str) -> str:
    return os.path.join(root, os.path.basename(guid_dir), "boot", nsih,
                        "System", "Library", "Caches",
                        "com.apple.kernelcaches", "kernelcache")


# --- patch step helpers ------------------------------------------------------

def _backup_inplace(path: str) -> str:
    bak = path + ".vzkl-orig"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
    return bak


def _patch_file(path: str, patch_fn, entries: list, role: str,
                backup_path: str | None = None) -> None:
    """Backup `path`, patch it with patch_fn(bytes)->(bytes,info), record entry."""
    orig_sha = _sha(path)
    bak = backup_path or _backup_inplace(path)
    data = open(path, "rb").read()
    new, info = patch_fn(data)
    if info.get("already_patched"):
        entries.append({"role": role, "path": path, "backup": bak,
                        "orig_sha": orig_sha, "state": "already-patched"})
        return
    tmp = path + ".vzkl-tmp"
    with open(tmp, "wb") as f:
        f.write(new)
    os.replace(tmp, path)
    entries.append({"role": role, "path": path, "backup": bak,
                    "orig_sha": orig_sha, "patched_sha": _sha(path),
                    "state": "patched", "detail": _jsonable(info)})


def _jsonable(info: dict) -> dict:
    out = {}
    for k, v in info.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        else:
            out[k] = str(v)
    return out


# --- top-level orchestration -------------------------------------------------

def patch_vm(uuid: str) -> dict:
    vm = _bundle(uuid)
    if vm.get("status") == "started":
        raise PatchVMError("VM is running; stop it before patching its disk")
    bundle = vm["bundle_path"]
    bdir = _backup_dir(bundle)
    entries: list = []

    # 1) AuxiliaryStorage LLB (user-owned bundle file)
    aux = vm["aux_path"] or os.path.join(bundle, "Data", "AuxiliaryStorage")
    aux_bak = os.path.join(bdir, "AuxiliaryStorage.orig")
    if not os.path.exists(aux_bak):
        shutil.copy2(aux, aux_bak)
    _patch_file(aux, auxstorage.patch, entries, "auxstorage", backup_path=aux_bak)

    # 2-3) Preboot + Recovery, via the attached OS disk
    disk = _find_os_disk(bundle)
    try:
        guid_dir, nsih = _active_nsih(disk["preboot_mnt"])
        _patch_file(_iboot_path(disk["preboot_mnt"], guid_dir, nsih),
                    lambda d: iboot.patch(d, expected_callers=2),
                    entries, "preboot-iboot")
        _patch_file(_kc_path(disk["preboot_mnt"], guid_dir, nsih),
                    kernelcache.patch, entries, "preboot-kernelcache")

        rec = disk["recovery"]
        if rec:
            rmnt = rec["mountpoint"]
            run(["mount", "-u", "-o", "rw", rmnt])   # Recovery mounts ro
            _patch_file(_iboot_path(rmnt, guid_dir, nsih),
                        lambda d: iboot.patch(d, expected_callers=2),
                        entries, "recovery-iboot")
            _patch_file(_kc_path(rmnt, guid_dir, nsih),
                        kernelcache.patch, entries, "recovery-kernelcache")
        else:
            entries.append({"role": "recovery", "state": "no-recovery-volume"})
    finally:
        run(["sync"])
        _detach(disk["whole_dev"])

    manifest = {
        "version": 1,
        "uuid": uuid,
        "bundle": bundle,
        "nsih": nsih,
        "patched_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "entries": entries,
    }
    with open(os.path.join(bdir, MANIFEST), "w") as f:
        json.dump(manifest, f, indent=2)
    return {"ok": True, "patched": sum(1 for e in entries if e.get("state") == "patched"),
            "manifest": os.path.join(bdir, MANIFEST), "entries": entries}


_DISK_ROLES = {
    "preboot-iboot": ("preboot", _iboot_path),
    "preboot-kernelcache": ("preboot", _kc_path),
    "recovery-iboot": ("recovery", _iboot_path),
    "recovery-kernelcache": ("recovery", _kc_path),
}


def unpatch_vm(uuid: str) -> dict:
    """Restore every original from its backup. Disk paths are reconstructed from
    the freshly-attached disk (mountpoints differ from patch time)."""
    vm = _bundle(uuid)
    if vm.get("status") == "started":
        raise PatchVMError("VM is running; stop it before unpatching")
    bundle = vm["bundle_path"]
    mpath = os.path.join(bundle, "Data", BACKUP_SUBDIR, MANIFEST)
    if not os.path.exists(mpath):
        raise PatchVMError("no vz-kext-loader backup manifest found for this VM")
    manifest = json.load(open(mpath))
    nsih = manifest.get("nsih")
    restored, missing = [], []

    # Bundle-file entries (auxstorage): stable absolute path + stable backup.
    for e in manifest["entries"]:
        if e.get("role") == "auxstorage":
            if e.get("backup") and os.path.exists(e["backup"]):
                shutil.copy2(e["backup"], e["path"])
                restored.append(e["path"])
            else:
                missing.append(e.get("backup"))

    # Disk-internal entries: re-attach, reconstruct current paths, restore from
    # the in-disk "<name>.vzkl-orig" backups.
    disk_roles = [e["role"] for e in manifest["entries"] if e.get("role") in _DISK_ROLES]
    if disk_roles:
        disk = _find_os_disk(bundle)
        try:
            guid_dir, live_nsih = _active_nsih(disk["preboot_mnt"])
            use_nsih = nsih or live_nsih
            if disk["recovery"]:
                run(["mount", "-u", "-o", "rw", disk["recovery"]["mountpoint"]])
            roots = {"preboot": disk["preboot_mnt"],
                     "recovery": disk["recovery"]["mountpoint"] if disk["recovery"] else None}
            for role in disk_roles:
                which, path_fn = _DISK_ROLES[role]
                root = roots.get(which)
                if not root:
                    continue
                cur = path_fn(root, guid_dir, use_nsih)
                bak = cur + ".vzkl-orig"
                if os.path.exists(bak):
                    shutil.copy2(bak, cur)
                    restored.append(cur)
                else:
                    missing.append(bak)
        finally:
            run(["sync"])
            _detach(disk["whole_dev"])

    return {"ok": True, "restored": restored, "missing_backups": missing}
