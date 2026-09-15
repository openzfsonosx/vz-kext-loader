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


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="vzkl", description="vz-kext-loader engine")
    p.add_argument("--version", action="version", version=f"vzkl {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    pc = sub.add_parser("check", help="Run host requirement checks")
    pc.add_argument("--json", action="store_true", help="Emit JSON")
    pc.set_defaults(func=cmd_check)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
