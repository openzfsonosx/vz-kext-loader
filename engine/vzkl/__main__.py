"""CLI entry point: python3 -m vzkl <command> [--json]

Commands (growing):
  check   Run host requirement checks.

Every command supports --json for machine consumption by the app. Without --json
a human-readable summary is printed.
"""
from __future__ import annotations
import argparse
import json
import sys

from . import __version__
from . import checks
from . import vms
from . import overlay
from . import patchvm


def _print_checks_human(result: dict) -> None:
    glyph = {"ok": "✓", "warn": "!", "fail": "✗", "info": "·"}
    for c in result["checks"]:
        g = glyph.get(c["status"], "?")
        line = f" {g} {c['label']}: {c['detail']}"
        print(line)
        if c["fix"] and c["status"] in ("fail", "warn"):
            print(f"     -> {c['fix']}")
    print()
    print(("READY: " if result["ready"] else "NOT READY: ") + result["summary"])


def cmd_check(args) -> int:
    result = checks.run_all()
    if args.json:
        json.dump(result, sys.stdout)
        sys.stdout.write("\n")
    else:
        _print_checks_human(result)
    return 0 if result["ready"] else 1


def cmd_list_vms(args) -> int:
    result = vms.discover()
    if args.json:
        json.dump(result, sys.stdout)
        sys.stdout.write("\n")
    else:
        for v in result["vms"]:
            mark = "✓ patchable" if v["patchable"] else "✗ " + (v["reason"] or "not patchable")
            print(f" [{v['status']:>7}] {v['name']}")
            print(f"           {v['uuid']}  ({mark})")
        print(f"\n{result['patchable_count']} patchable VM(s)")
    return 0


def cmd_overlay_status(args) -> int:
    result = overlay.status()
    if args.json:
        json.dump(result, sys.stdout); sys.stdout.write("\n")
    else:
        print("overlay mounted:", result["mounted"])
        print("device:", result["device"] or "(none attached)")
    return 0


def cmd_overlay_build(args) -> int:
    try:
        result = overlay.build(args.out) if args.out else overlay.build()
        result["ok"] = True
    except Exception as e:  # noqa: BLE001 - report to caller as JSON
        result = {"ok": False, "error": str(e)}
    if args.json:
        json.dump(result, sys.stdout); sys.stdout.write("\n")
    else:
        if result.get("ok"):
            print("built overlay:", result["dmg"])
            print("device:", result["device"], "  avpbooter:", result["avpbooter_state"])
            print("mount:", " ".join(result["mount_argv"]))
        else:
            print("build failed:", result["error"])
    return 0 if result.get("ok") else 1


def cmd_vm(args) -> int:
    if args.op == "status":
        result = vms.vm_status(args.uuid)
    elif args.op == "start":
        result = vms.vm_start(args.uuid)
    elif args.op == "stop":
        result = vms.vm_stop(args.uuid)
    elif args.op == "ip":
        result = vms.vm_ip(args.uuid)
    else:
        result = {"ok": False, "error": f"unknown op {args.op}"}
    if args.json:
        json.dump(result, sys.stdout); sys.stdout.write("\n")
    else:
        print(result)
    return 0 if result.get("ok") else 1


def cmd_patch_vm(args) -> int:
    fn = patchvm.unpatch_vm if args.unpatch else patchvm.patch_vm
    try:
        result = fn(args.uuid)
    except Exception as e:  # noqa: BLE001
        result = {"ok": False, "error": str(e)}
    if args.json:
        json.dump(result, sys.stdout); sys.stdout.write("\n")
    else:
        if result.get("ok"):
            if args.unpatch:
                print("restored:", len(result["restored"]), "file(s)")
                for p_ in result["restored"]:
                    print("  ", p_)
            else:
                print("patched", result["patched"], "file(s); manifest:", result["manifest"])
                for e in result["entries"]:
                    print("  ", e.get("role"), e.get("state"))
        else:
            print("failed:", result["error"])
    return 0 if result.get("ok") else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="vzkl", description="vz-kext-loader engine")
    p.add_argument("--version", action="version", version=f"vzkl {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    pc = sub.add_parser("check", help="Run host requirement checks")
    pc.add_argument("--json", action="store_true", help="Emit JSON")
    pc.set_defaults(func=cmd_check)

    pv = sub.add_parser("list-vms", help="Discover UTM VMs and classify patchability")
    pv.add_argument("--json", action="store_true", help="Emit JSON")
    pv.set_defaults(func=cmd_list_vms)

    po = sub.add_parser("overlay-status", help="Report AVPBooter overlay mount state")
    po.add_argument("--json", action="store_true", help="Emit JSON")
    po.set_defaults(func=cmd_overlay_status)

    pb = sub.add_parser("overlay-build",
                        help="Build+attach the patched-AVPBooter overlay (non-privileged)")
    pb.add_argument("--out", help="dmg path (default: managed Application Support path)")
    pb.add_argument("--json", action="store_true", help="Emit JSON")
    pb.set_defaults(func=cmd_overlay_build)

    pm = sub.add_parser("vm", help="Control a VM via utmctl")
    pm.add_argument("op", choices=["status", "start", "stop", "ip"])
    pm.add_argument("uuid")
    pm.add_argument("--json", action="store_true", help="Emit JSON")
    pm.set_defaults(func=cmd_vm)

    pp = sub.add_parser("patch-vm",
                        help="Patch (or --unpatch) a VM's guest boot chain; run as root")
    pp.add_argument("uuid")
    pp.add_argument("--unpatch", action="store_true", help="Restore originals from backups")
    pp.add_argument("--json", action="store_true", help="Emit JSON")
    pp.set_defaults(func=cmd_patch_vm)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
