"""Discover UTM virtual machines and classify which are patchable.

A VM is patchable by this tool when it is a UTM **Apple**-backend **macOS**
**aarch64** guest with an AuxiliaryStorage blob (i.e. a real Apple
Virtualization.framework macOS VM, not a QEMU/Linux guest).

`utmctl list` is the source of truth for which VMs exist and their run status;
we locate each bundle by scanning candidate directories for a config.plist whose
UUID matches.
"""
from __future__ import annotations
import os
import glob
import plistlib
from dataclasses import dataclass, asdict, field
from typing import Optional

from .util import run

UTMCTL = "/Applications/UTM.app/Contents/MacOS/utmctl"

DEFAULT_SEARCH = [
    os.path.expanduser(
        "~/Library/Containers/com.utmapp.UTM/Data/Documents"
    ),
]


def _search_dirs() -> list[str]:
    dirs = list(DEFAULT_SEARCH)
    extra = os.environ.get("VZKL_VM_SEARCH_PATHS", "")
    for d in extra.split(":"):
        d = d.strip()
        if d and d not in dirs:
            dirs.append(d)
    return [d for d in dirs if os.path.isdir(d)]


@dataclass
class VM:
    uuid: str = ""
    name: str = ""
    status: str = "unknown"     # started | stopped | unknown
    backend: str = ""           # Apple | QEMU | ...
    os: str = ""                # macOS | ...
    arch: str = ""              # aarch64 | ...
    bundle_path: str = ""
    aux_path: str = ""
    patchable: bool = False
    reason: str = ""            # why not patchable, if applicable

    def to_dict(self) -> dict:
        return asdict(self)


def _utmctl_list() -> dict[str, dict]:
    """Return {uuid: {'status':..., 'name':...}} from `utmctl list`."""
    out: dict[str, dict] = {}
    if not os.path.exists(UTMCTL):
        return out
    cp = run([UTMCTL, "list"])
    for line in (cp.stdout or "").splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        uuid, status, name = parts[0], parts[1], parts[2]
        if uuid.count("-") != 4:      # skip the header row
            continue
        out[uuid.upper()] = {"status": status, "name": name}
    return out


def _parse_bundle(bundle: str) -> Optional[VM]:
    cfg_path = os.path.join(bundle, "config.plist")
    if not os.path.exists(cfg_path):
        return None
    try:
        with open(cfg_path, "rb") as f:
            cfg = plistlib.load(f)
    except Exception:
        return None

    info = cfg.get("Information", {}) or {}
    system = cfg.get("System", {}) or {}
    boot = system.get("Boot", {}) or {}
    macplat = system.get("MacPlatform", {}) or {}

    vm = VM(
        uuid=str(info.get("UUID", "")).upper(),
        name=str(info.get("Name", os.path.basename(bundle))),
        backend=str(cfg.get("Backend", "")),
        os=str(boot.get("OperatingSystem", "")),
        arch=str(system.get("Architecture", "")),
        bundle_path=bundle,
    )

    # Locate AuxiliaryStorage.
    data_dir = os.path.join(bundle, "Data")
    aux_name = macplat.get("AuxiliaryStoragePath") or "AuxiliaryStorage"
    cand = os.path.join(data_dir, os.path.basename(str(aux_name)))
    if os.path.exists(cand):
        vm.aux_path = cand
    elif os.path.exists(os.path.join(data_dir, "AuxiliaryStorage")):
        vm.aux_path = os.path.join(data_dir, "AuxiliaryStorage")

    _classify(vm)
    return vm


def _classify(vm: VM) -> None:
    problems = []
    if vm.backend != "Apple":
        problems.append(f"backend is {vm.backend or 'unknown'} (need Apple)")
    if vm.os != "macOS":
        problems.append(f"OS is {vm.os or 'unknown'} (need macOS)")
    if vm.arch != "aarch64":
        problems.append(f"arch is {vm.arch or 'unknown'} (need aarch64)")
    if not vm.aux_path:
        problems.append("no AuxiliaryStorage found")
    vm.patchable = not problems
    vm.reason = "" if vm.patchable else "; ".join(problems)


def discover() -> dict:
    """Merge bundle scan with utmctl status; return {vms:[...]}."""
    status_by_uuid = _utmctl_list()

    vms: dict[str, VM] = {}
    for d in _search_dirs():
        for bundle in sorted(glob.glob(os.path.join(d, "*.utm"))):
            vm = _parse_bundle(bundle)
            if vm and vm.uuid:
                vms[vm.uuid] = vm

    # Attach run status; add any utmctl VM whose bundle we couldn't find.
    for uuid, meta in status_by_uuid.items():
        if uuid in vms:
            vms[uuid].status = meta["status"]
        else:
            vm = VM(uuid=uuid, name=meta["name"], status=meta["status"])
            vm.reason = "bundle not found in search paths"
            vms[uuid] = vm

    ordered = sorted(vms.values(), key=lambda v: (not v.patchable, v.name.lower()))
    return {
        "vms": [v.to_dict() for v in ordered],
        "patchable_count": sum(1 for v in ordered if v.patchable),
        "search_paths": _search_dirs(),
    }


# --- VM control (utmctl; non-privileged) ------------------------------------

def _utmctl(*args) -> tuple[int, str, str]:
    if not os.path.exists(UTMCTL):
        return 127, "", "utmctl not found"
    cp = run([UTMCTL, *args], timeout=40)
    return cp.returncode, (cp.stdout or "").strip(), (cp.stderr or "").strip()


def vm_status(uuid: str) -> dict:
    rc, out, err = _utmctl("status", uuid)
    return {"uuid": uuid, "status": out or "unknown", "ok": rc == 0, "error": err}


def vm_start(uuid: str) -> dict:
    rc, out, err = _utmctl("start", uuid)
    return {"uuid": uuid, "ok": rc == 0, "output": out, "error": err}


def vm_stop(uuid: str) -> dict:
    rc, out, err = _utmctl("stop", uuid)
    return {"uuid": uuid, "ok": rc == 0, "output": out, "error": err}


def vm_ip(uuid: str) -> dict:
    rc, out, err = _utmctl("ip-address", uuid)
    ips = [ln.strip() for ln in out.splitlines() if ln.strip()]
    return {"uuid": uuid, "ok": rc == 0, "ips": ips, "error": err}
