"""Patch point #4: kernelcache ACM defeat.

nop the `bl __validate_acm_context` inside exactly two functions,
`__command_create_linked_manifest` and `__command_update_local_policy_for_kcos`,
so the auxKC bless's ACM check passes. Symbol-anchored via radare2 (not offset
hardcoded); disassembly by capstone.

Important: only calls to `__validate_acm_context` are patched, never the
similarly named `__mdm_validate_acm_context`.
"""
from __future__ import annotations
import json
import subprocess

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM

from . import img4 as img4mod

NOP = bytes([0x1F, 0x20, 0x03, 0xD5])

ACM_SYM = "__validate_acm_context"
TARGET_FUNCS = ("__command_create_linked_manifest",
                "__command_update_local_policy_for_kcos")

_md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)


class KcError(Exception):
    pass


def _r2_symbols(payload_path: str) -> list[dict]:
    """Return the symbol table via `r2 -c isj` as a list of dicts."""
    cp = subprocess.run(
        ["r2", "-q", "-c", "isj", "-e", "bin.relocs.apply=false", payload_path],
        capture_output=True, text=True, timeout=300,
    )
    if cp.returncode != 0 and not cp.stdout:
        raise KcError(f"r2 failed: {cp.stderr.strip()[:200]}")
    # isj output may have log lines before the JSON; find the array.
    out = cp.stdout
    start = out.find("[")
    if start < 0:
        raise KcError("r2 isj produced no JSON")
    return json.loads(out[start:])


def _funcs_by_paddr(symbols: list[dict]) -> list[tuple[int, int, str]]:
    """Sorted (paddr, vaddr, name) for FUNC symbols, for size bounding."""
    fs = [(s["paddr"], s.get("vaddr", 0), s["name"])
          for s in symbols if s.get("type") == "FUNC" and "paddr" in s]
    return sorted(fs)


def find_sites(payload: bytes, symbols: list[dict]) -> list[dict]:
    """Locate the `bl __validate_acm_context` inside each target function."""
    by_name = {s["name"]: s for s in symbols}
    for name in (ACM_SYM, *TARGET_FUNCS):
        if name not in by_name:
            raise KcError(f"symbol {name} not found (kernelcache too new / stripped?)")
    acm_vaddr = by_name[ACM_SYM]["vaddr"]

    funcs = _funcs_by_paddr(symbols)
    paddrs = [f[0] for f in funcs]

    sites = []
    for fname in TARGET_FUNCS:
        f = by_name[fname]
        fpaddr, fvaddr = f["paddr"], f["vaddr"]
        # size = distance to the next FUNC symbol (bounds the scan)
        import bisect
        i = bisect.bisect_right(paddrs, fpaddr)
        end = paddrs[i] if i < len(paddrs) else fpaddr + 0x2000
        size = min(end - fpaddr, 0x4000)

        found = []
        for ins in _md.disasm(payload[fpaddr:fpaddr + size], fvaddr):
            if ins.mnemonic == "bl":
                # bl target = vaddr + imm; capstone gives it in op_str as #0x...
                tgt = int(ins.op_str.lstrip("#"), 16)
                if tgt == acm_vaddr:
                    off = fpaddr + (ins.address - fvaddr)
                    found.append(off)
        # 1 = patch it; 0 = already nopped (idempotent re-run); >1 = ambiguous.
        if len(found) > 1:
            raise KcError(
                f"{fname}: expected 1 bl to {ACM_SYM}, found {len(found)} "
                f"{[hex(x) for x in found]}")
        if len(found) == 1:
            sites.append({"func": fname, "offset": found[0]})
    return sites


def patch(img4_bytes: bytes) -> tuple[bytes, dict]:
    """Decompress, nop the two ACM calls, LZFSE-rewrap. Returns (new_img4, info)."""
    parsed = img4mod.parse(img4_bytes)
    payload = bytearray(parsed.payload)

    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        pp = os.path.join(td, "kc.bin")
        with open(pp, "wb") as fh:
            fh.write(payload)
        symbols = _r2_symbols(pp)

    sites = find_sites(bytes(payload), symbols)
    if not sites:
        # Both ACM calls already nopped -> idempotent no-op (skip the recompress).
        return img4_bytes, {
            "sites": [], "already_patched": True,
            "fourcc": parsed.fourcc, "description": parsed.description,
        }
    for s in sites:
        payload[s["offset"]:s["offset"] + 4] = NOP

    new_img4 = img4mod.rewrap(parsed, bytes(payload), compress=True)
    return new_img4, {
        "sites": sites,
        "already_patched": False,
        "fourcc": parsed.fourcc,
        "description": parsed.description,
    }
