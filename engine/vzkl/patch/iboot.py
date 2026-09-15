"""Patch point #3: iBoot `validate_boot_object` (img4-wrapped).

iBoot uses the same `validate_boot_object` as AVPBooter (same unique signature),
so we reuse that caller-nulling logic on the decompressed payload, then rewrap
the IMG4 uncompressed, reusing the original manifest.
"""
from __future__ import annotations

from .. import avpbooter
from . import img4 as img4mod


def patch(img4_bytes: bytes, expected_callers: int | None = None) -> tuple[bytes, dict]:
    """Return (new_img4, info). Idempotent: an already-patched iBoot (no bl
    callers left) is returned unchanged with info['already_patched']=True."""
    parsed = img4mod.parse(img4_bytes)
    new_payload, callers, state = avpbooter.ensure_patched(parsed.payload)
    if state == "already-patched":
        return img4_bytes, {"already_patched": True, "callers": []}
    if expected_callers is not None and len(callers) != expected_callers:
        raise avpbooter.PatchError(
            f"iBoot: expected {expected_callers} callers, found {len(callers)}")
    new_img4 = img4mod.rewrap(parsed, new_payload, compress=False)
    return new_img4, {
        "already_patched": False,
        "callers": callers,
        "fourcc": parsed.fourcc,
        "description": parsed.description,
    }
