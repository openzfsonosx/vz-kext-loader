"""img4 helpers built on pyimg4.

Reliability notes learned in the field:
  * pyimg4's IM4PData accessors are unreliable; use the CLI (`im4p extract`,
    `im4p create`) for payload in/out.
  * Reuse the ORIGINAL manifest (im4m) when rewrapping — no re-signing is needed
    because the parent stage's patched validator ignores the stale digest.
"""
from __future__ import annotations
import os
import subprocess
import tempfile

import pyimg4


def _cli(*args) -> None:
    subprocess.run(["python3", "-m", "pyimg4", *args],
                   check=True, capture_output=True)


class Img4:
    """A parsed IMG4: its im4p/im4m and the raw (decompressed) payload."""

    def __init__(self, fourcc: str, description: str,
                 payload: bytes, im4m: bytes, compression):
        self.fourcc = fourcc
        self.description = description
        self.payload = payload
        self.im4m = im4m
        self.compression = compression   # pyimg4.Compression or None


def parse(img4_bytes: bytes) -> Img4:
    """Split an IMG4 into fourcc/desc/payload/manifest."""
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "x.img4")
        im4p = os.path.join(td, "x.im4p")
        im4m = os.path.join(td, "x.im4m")
        payload = os.path.join(td, "x.bin")
        with open(p, "wb") as f:
            f.write(img4_bytes)
        _cli("img4", "extract", "-i", p, "-p", im4p, "-m", im4m)
        _cli("im4p", "extract", "-i", im4p, "-o", payload)
        im4p_obj = pyimg4.IM4P(open(im4p, "rb").read())
        comp = getattr(im4p_obj.payload, "compression", None)
        return Img4(
            fourcc=im4p_obj.fourcc,
            description=im4p_obj.description or "",
            payload=open(payload, "rb").read(),
            im4m=open(im4m, "rb").read(),
            compression=comp,
        )


def rewrap(orig: Img4, new_payload: bytes, compress: bool) -> bytes:
    """Rebuild an IMG4 from a patched payload, reusing the original manifest.

    compress=False -> uncompressed im4p (iBoot/LLB).
    compress=True  -> LZFSE (kernelcache).
    """
    with tempfile.TemporaryDirectory() as td:
        raw = os.path.join(td, "p.bin")
        im4p = os.path.join(td, "p.im4p")
        with open(raw, "wb") as f:
            f.write(new_payload)
        if compress:
            data = pyimg4.IM4PData(new_payload)
            data.compress(pyimg4.Compression.LZFSE)
            im4p_obj = pyimg4.IM4P(fourcc=orig.fourcc,
                                   description=orig.description,
                                   payload=data)
            im4p_bytes = im4p_obj.output()
        else:
            _cli("im4p", "create", "-i", raw, "-o", im4p,
                 "-f", orig.fourcc, "-d", orig.description or "")
            im4p_bytes = open(im4p, "rb").read()

        img4_obj = pyimg4.IMG4(im4p=pyimg4.IM4P(im4p_bytes),
                               im4m=pyimg4.IM4M(orig.im4m))
        return img4_obj.output()
