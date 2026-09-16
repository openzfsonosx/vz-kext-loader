"""Patch point #2: LLB `image4_validate_property_callback` (the DGST digest validator).

This is the one anchor that needs real disassembly (see the repo README). We
locate the function by the "DGST" fourcc it loads (mov wN,#0x5354 ; movk
wN,#0x4447,lsl#16 => 0x44475354), then find the function's `retab` epilogue after
the last DGST load, then the `mov x0, x<N>` return-value instruction immediately
before that epilogue, and rewrite it to `mov x0, #0` so the validator always
reports success.

The source register of the return mov varies by build (x27 here, x20 in Michaud's
notes), so we never hardcode instruction bytes — we disassemble and locate.
"""
from __future__ import annotations
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM

MOV_X0_0 = bytes([0x00, 0x00, 0x80, 0xD2])   # mov x0, #0

_md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)


class PatchError(Exception):
    pass


def _decode1(data: bytes, off: int):
    """Decode a single instruction at a 4-byte-aligned offset, or None."""
    for ins in _md.disasm(data[off:off + 4], off):
        return ins
    return None


def _find_dgst_loads(data: bytes) -> list[int]:
    """Offsets of `mov wN,#0x5354` immediately followed by `movk wN,#0x4447,lsl16`."""
    hits = []
    for off in range(0, len(data) - 7, 4):
        a = _decode1(data, off)
        if not a or a.mnemonic not in ("mov", "movz") or "0x5354" not in a.op_str:
            continue
        reg = a.op_str.split(",")[0].strip()
        b = _decode1(data, off + 4)
        if b and b.mnemonic == "movk" and "0x4447" in b.op_str \
                and b.op_str.split(",")[0].strip() == reg:
            hits.append(off)
    return hits


def _next_retab(data: bytes, start: int) -> int | None:
    for off in range(start, len(data) - 3, 4):
        ins = _decode1(data, off)
        if ins and ins.mnemonic == "retab":
            return off
    return None


def _return_mov_before(data: bytes, retab_off: int, window: int = 24):
    """Walk backward from the retab to the return-value `mov x0, …`.

    Returns (offset, already_patched):
      * `mov x0, x<N>`  -> (offset, False)  — the site to patch
      * `mov x0, #0`    -> (offset, True)   — already patched
      * neither found   -> None             — genuine failure (raise upstream)
    """
    off = retab_off - 4
    steps = 0
    while off > 0 and steps < window:
        ins = _decode1(data, off)
        if ins and ins.mnemonic == "mov":
            op = ins.op_str.replace(" ", "")
            if op.startswith("x0,x"):
                return off, False
            if op in ("x0,#0", "x0,#0x0"):
                return off, True
        off -= 4
        steps += 1
    return None


def find_site(payload: bytes) -> dict:
    """Locate the patch site. Returns {site, retab, dgst_loads, insn, already_patched}.

    Raises PatchError only for a genuine structural failure (not an image4
    validator, or an unrecognized epilogue) — NOT for an already-patched LLB,
    which is reported via already_patched=True so callers never silently skip a
    patch they actually needed to apply.
    """
    dgst = _find_dgst_loads(payload)
    if not dgst:
        raise PatchError("DGST constant not found; not an LLB image4 validator?")
    retab = _next_retab(payload, max(dgst) + 4)
    if retab is None:
        raise PatchError("no retab epilogue after the DGST loads")
    found = _return_mov_before(payload, retab)
    if found is None:
        raise PatchError("could not find the return `mov x0, …` before the epilogue")
    site, already = found
    ins = _decode1(payload, site)
    return {
        "site": site,
        "retab": retab,
        "dgst_loads": dgst,
        "insn": f"{ins.mnemonic} {ins.op_str}",
        "already_patched": already,
    }


def patch(payload: bytes) -> tuple[bytes, dict]:
    """Return (patched_payload, info). If already patched, returns unchanged with
    info['already_patched']=True. Raises PatchError on a genuine failure."""
    info = find_site(payload)
    if info["already_patched"]:
        return payload, info
    buf = bytearray(payload)
    buf[info["site"]:info["site"] + 4] = MOV_X0_0
    return bytes(buf), info
