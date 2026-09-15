"""Patch point #1: AVPBooter `validate_boot_object`.

Anchor on the unique 8-byte prologue of `validate_boot_object`
(`mov x5,x4; mov w4,#0` = e5 03 04 aa 04 00 80 52), which occurs exactly once in
AVPBooter and once in iBoot. Find every `bl` that targets that function and
rewrite it to `mov x0,#0`, so the caller always sees "valid".

This is the robust, signature-anchored patch; it refuses if the signature is not
unique or if no callers are found, rather than guessing.
"""
from __future__ import annotations

VBO_SIG = bytes([0xE5, 0x03, 0x04, 0xAA, 0x04, 0x00, 0x80, 0x52])
MOV_X0_0 = bytes([0x00, 0x00, 0x80, 0xD2])   # mov x0, #0


class PatchError(Exception):
    pass


def _find_unique(data: bytes, sig: bytes) -> int:
    first = data.find(sig)
    if first < 0:
        raise PatchError("validate_boot_object signature not found")
    if data.find(sig, first + 1) >= 0:
        raise PatchError("validate_boot_object signature is not unique")
    return first


def _bl_callers(data: bytes, target_off: int) -> list[int]:
    """Offsets of every `bl` instruction whose target == target_off.

    Works in flat file-offset space (AVPBooter/iBoot images are contiguous and
    load such that a `bl` target computed as PC + imm26*4 lands at the callee's
    file offset).
    """
    callers = []
    n = len(data)
    for off in range(0, n - 3, 4):
        w = int.from_bytes(data[off:off + 4], "little")
        if (w >> 26) == 0x25:                    # BL: bits[31:26] = 0b100101
            imm = w & 0x03FFFFFF
            if imm & (1 << 25):
                imm -= (1 << 26)
            if off + imm * 4 == target_off:
                callers.append(off)
    return callers


def patch(data: bytes, expected_callers: int | None = None) -> tuple[bytes, list[int]]:
    """Return (patched_bytes, caller_offsets). Raises PatchError on anomalies."""
    vbo = _find_unique(data, VBO_SIG)
    callers = _bl_callers(data, vbo)
    if not callers:
        raise PatchError(
            f"found validate_boot_object @0x{vbo:x} but no bl callers to patch"
        )
    if expected_callers is not None and len(callers) != expected_callers:
        raise PatchError(
            f"expected {expected_callers} caller(s), found {len(callers)} "
            f"@ {[hex(c) for c in callers]}"
        )
    buf = bytearray(data)
    for off in callers:
        buf[off:off + 4] = MOV_X0_0
    return bytes(buf), callers


def info(data: bytes) -> dict:
    """Non-mutating: locate the function and its callers, for reporting/preview."""
    vbo = _find_unique(data, VBO_SIG)
    callers = _bl_callers(data, vbo)
    return {
        "vbo_offset": vbo,
        "callers": callers,
        "caller_count": len(callers),
    }


def ensure_patched(data: bytes) -> tuple[bytes, list[int], str]:
    """Idempotent: return (bytes, callers, state).

    state == 'patched'          -> we rewrote `bl` callers just now.
    state == 'already-patched'  -> signature present, no `bl` callers remain.

    Raises PatchError only if the signature is missing or not unique.
    """
    vbo = _find_unique(data, VBO_SIG)          # validates presence + uniqueness
    callers = _bl_callers(data, vbo)
    if not callers:
        return data, [], "already-patched"
    patched, sites = patch(data)
    return patched, sites, "patched"
