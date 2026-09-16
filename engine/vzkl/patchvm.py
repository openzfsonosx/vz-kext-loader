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


def _progress(msg: str) -> None:
    """Append a progress line to VZKL_PROGRESS_FILE (if set) so the app can tail
    it live while the single privileged patch-vm call is still running."""
    path = os.environ.get("VZKL_PROGRESS_FILE")
    if not path:
        return
    try:
        with open(path, "a") as f:
            f.write(msg + "\n")
            f.flush()
    except OSError:
        pass


def _ensure_path() -> None:
    """When run as root via an admin prompt, PATH is minimal and omits /usr/sbin
    (diskutil), /sbin (mount) and the Homebrew dirs (r2). Ensure they're present
    so every tool resolves regardless of how we were launched."""
    need = ["/usr/bin", "/bin", "/usr/sbin", "/sbin",
            "/usr/local/bin", "/opt/homebrew/bin"]
    have = os.environ.get("PATH", "").split(":")
    os.environ["PATH"] = ":".join(have + [d for d in need if d not in have])


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


def _reown(path: str, uid: int, gid: int, mode: int | None = None) -> None:
    try:
        os.chown(path, uid, gid)
        if mode is not None:
            os.chmod(path, mode)
    except OSError:
        pass


def _reown_tree(path: str, uid: int, gid: int) -> None:
    _reown(path, uid, gid)
    for root, dirs, files in os.walk(path):
        for n in dirs + files:
            _reown(os.path.join(root, n), uid, gid)


def _restore_bundle_ownership(bundle_path: str, aux_path: str, bdir: str) -> None:
    """Running as root leaves patched bundle files root-owned, which stops UTM
    (running as the user) from opening AuxiliaryStorage read-write. Restore the
    bundle owner on the files we touched. Disk-internal files are inside the
    image and don't need this."""
    try:
        st = os.stat(bundle_path)
    except OSError:
        return
    _reown(aux_path, st.st_uid, st.st_gid, 0o644)
    _reown_tree(bdir, st.st_uid, st.st_gid)


# --- disk image attach / volume discovery -----------------------------------
#
# UTM disk images are RAW (no .dmg wrapper), so hdiutil needs
# diskimage-class=CRawDiskImage or it prompts "there may be a problem with this
# disk image". We attach with -nomount so the FileVault-encrypted Data volume is
# never touched, then mount only Preboot and Recovery (both unencrypted).

def _plist(text: str):
    import plistlib
    return plistlib.loads(text.encode()) if text.strip() else {}


def _attach_raw(img: str) -> tuple[str, list[str]]:
    """Attach a raw UTM disk image without mounting. Return (whole_dev, apfs_parts).

    apfs_parts is every APFS-bearing partition on the image (main, ISC, recovery);
    the caller checks each container for the one with a Preboot volume, since the
    system-entities order is not deterministic.
    """
    cp = run(["hdiutil", "attach", "-nobrowse", "-nomount", "-noverify",
              "-imagekey", "diskimage-class=CRawDiskImage", "-plist", img],
             timeout=180)
    if cp.returncode != 0:
        raise PatchVMError(f"hdiutil attach failed: {cp.stderr.strip() or cp.stdout.strip()}")
    ents = _plist(cp.stdout).get("system-entities", [])
    whole = next((e["dev-entry"] for e in ents
                  if e.get("content-hint") == "GUID_partition_scheme"), None)
    apfs_parts = [e["dev-entry"] for e in ents
                  if str(e.get("content-hint", "")).startswith("Apple_APFS")]
    if not whole:
        whole = ents[0]["dev-entry"] if ents else None
    if not whole:
        raise PatchVMError("attach produced no devices")
    return whole, apfs_parts


def _detach(dev: str) -> None:
    run(["hdiutil", "detach", dev, "-force"], timeout=60)


def _mountpoint(dev: str) -> str | None:
    info = _plist(run(["diskutil", "info", "-plist", dev]).stdout)
    mp = info.get("MountPoint")
    return mp or None


def _volumes_on(apfs_part: str) -> dict:
    """Return {role: device_id} for volumes in the APFS container whose physical
    store is `apfs_part` (the just-attached image partition).

    Matching by physical store is a hard safety requirement: the host's own disk
    also has Preboot/Recovery/Data volumes, and we must never touch it.
    """
    d = _plist(run(["diskutil", "apfs", "list", "-plist"]).stdout)
    part = (apfs_part or "").replace("/dev/", "")
    if not part:
        return {}
    for cont in d.get("Containers", []):
        stores = [s.get("DeviceIdentifier") for s in cont.get("PhysicalStores", [])]
        if part in stores:
            out = {}
            for v in cont.get("Volumes", []):
                for role in (v.get("Roles") or ["Data"]):
                    out.setdefault(role, v["DeviceIdentifier"])
            return out
    return {}


def _mount(dev_id: str, rw: bool) -> str:
    dev = "/dev/" + dev_id
    run(["diskutil", "mount", dev])
    mp = _mountpoint(dev)
    if not mp:
        raise PatchVMError(f"could not mount {dev}")
    if rw:
        run(["mount", "-u", "-o", "rw", mp])
    return mp


def _find_os_disk(bundle_path: str) -> dict:
    """Attach the OS disk image and mount Preboot (rw) + Recovery (rw)."""
    data = os.path.join(bundle_path, "Data")
    imgs = [os.path.join(data, f) for f in os.listdir(data) if f.endswith(".img")]
    diag = [f"PATH={os.environ.get('PATH','')}",
            f"diskutil={__import__('shutil').which('diskutil')}",
            f"imgs={[os.path.basename(i) for i in imgs]}"]
    for img in sorted(imgs, key=os.path.getsize, reverse=True):
        whole, apfs_parts = _attach_raw(img)
        # Pick the MAIN OS container: it has Preboot AND Data AND System. (The
        # iSC/SEP container also exposes a "Preboot" role but no Data; the
        # recovery container has Recovery but no Preboot.)
        vols, chosen = {}, None
        for part in apfs_parts:
            v = _volumes_on(part)
            if "Preboot" in v and "Data" in v and "System" in v:
                vols, chosen = v, part
                break
        diag.append(f"{os.path.basename(img)}: whole={whole} apfs_parts={apfs_parts} "
                    f"chosen={chosen} roles={list(vols.keys())}")
        if vols:
            preboot_mnt = _mount(vols["Preboot"], rw=True)
            # Safety: never operate on the host's live volumes.
            if preboot_mnt.startswith("/System/Volumes"):
                _detach(whole)
                raise PatchVMError(
                    f"refusing to patch: Preboot mounted at host path {preboot_mnt}")
            recovery = None
            if "Recovery" in vols:
                recovery = {"dev": vols["Recovery"],
                            "mountpoint": _mount(vols["Recovery"], rw=True)}
            return {"img": img, "whole_dev": whole,
                    "preboot_mnt": preboot_mnt, "recovery": recovery}
        _detach(whole)
    raise PatchVMError("no disk image with a Preboot volume found | "
                       + " || ".join(diag))


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


# The restore bundle (Preboot only) holds pristine THIRD copies of the boot
# objects — a bare-IM4P iBoot/mBoot and kernelcache — plus a signed trustcache.
# GG's startup-disk verification appears to repair the active objects from here,
# reverting our patches; patch these too so a repair restores patched bytes.
# These live under guid_dir/restore (not per-NSIH), so root/nsih are ignored;
# the 3-arg signature matches _iboot_path/_kc_path for reuse in _DISK_ROLES.
def _restore_iboot_path(root: str, guid_dir: str, nsih: str) -> str:
    return os.path.join(guid_dir, "restore", "Firmware", "all_flash",
                        "iBoot.vma2.RELEASE.im4p")


def _restore_kc_path(root: str, guid_dir: str, nsih: str) -> str:
    return os.path.join(guid_dir, "restore", "kernelcache.release.vma2")


# --- patch step helpers ------------------------------------------------------

def _backup_inplace(path: str) -> str:
    bak = path + ".vzkl-orig"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
    return bak


def _patch_file(path: str, patch_fn, entries: list, role: str,
                backup_path: str | None = None) -> None:
    """Backup `path`, patch it with patch_fn(bytes)->(bytes,info), record entry."""
    _progress(f"patching {role}…")
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
    _ensure_path()
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
    _progress("attaching disk image…")
    disk = _find_os_disk(bundle)
    try:
        guid_dir, nsih = _active_nsih(disk["preboot_mnt"])
        # iBoot's validate_boot_object call-site count varies by macOS version
        # (1 on Ventura, 2 on Sonoma+/Tahoe). The patch is signature-anchored and
        # exact-target, so patch whatever real callers exist rather than pinning a
        # version-specific count; iboot.patch still refuses if it finds none.
        _patch_file(_iboot_path(disk["preboot_mnt"], guid_dir, nsih),
                    iboot.patch,
                    entries, "preboot-iboot")
        _patch_file(_kc_path(disk["preboot_mnt"], guid_dir, nsih),
                    kernelcache.patch, entries, "preboot-kernelcache")

        rec = disk["recovery"]
        if rec:
            rmnt = rec["mountpoint"]                  # already mounted rw
            _patch_file(_iboot_path(rmnt, guid_dir, nsih),
                        iboot.patch,
                        entries, "recovery-iboot")
            _patch_file(_kc_path(rmnt, guid_dir, nsih),
                        kernelcache.patch, entries, "recovery-kernelcache")
        else:
            entries.append({"role": "recovery", "state": "no-recovery-volume"})

        # Restore-bundle third copies (Preboot only; bare IM4P). Absent on some
        # versions — patch only if present.
        r_ib = _restore_iboot_path(disk["preboot_mnt"], guid_dir, nsih)
        r_kc = _restore_kc_path(disk["preboot_mnt"], guid_dir, nsih)
        if os.path.exists(r_ib):
            _patch_file(r_ib, iboot.patch, entries, "restore-iboot")
        if os.path.exists(r_kc):
            _patch_file(r_kc, kernelcache.patch, entries, "restore-kernelcache")
        if not os.path.exists(r_ib) and not os.path.exists(r_kc):
            entries.append({"role": "restore", "state": "no-restore-bundle"})
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

    # We ran as root; hand the bundle files back to the VM's owner so UTM can
    # open AuxiliaryStorage read-write.
    _restore_bundle_ownership(bundle, aux, bdir)

    return {"ok": True, "patched": sum(1 for e in entries if e.get("state") == "patched"),
            "manifest": os.path.join(bdir, MANIFEST), "entries": entries}


_DISK_ROLES = {
    "preboot-iboot": ("preboot", _iboot_path),
    "preboot-kernelcache": ("preboot", _kc_path),
    "recovery-iboot": ("recovery", _iboot_path),
    "recovery-kernelcache": ("recovery", _kc_path),
    "restore-iboot": ("preboot", _restore_iboot_path),
    "restore-kernelcache": ("preboot", _restore_kc_path),
}


def unpatch_vm(uuid: str) -> dict:
    """Restore every original from its backup. Disk paths are reconstructed from
    the freshly-attached disk (mountpoints differ from patch time)."""
    _ensure_path()
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
