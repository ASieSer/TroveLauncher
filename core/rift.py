"""The ticket blob: RC4 plus a "RIFT" header.

This used to live inside ``inject.py``, which can only be imported on Windows.
It sits on its own here because both launch paths need it: the Windows one
imports this module, and the Wine helper reimplements it in C
(``native/troveinject.c``). Keeping it apart and free of platform calls is what
makes the two versions easy to read side by side and keep in step.

Pure code: it touches neither Windows nor the disk.
"""

from __future__ import annotations

import secrets
import struct

RIFT_MAGIC = b"\x54\x46\x49\x52"  # 'TFIR' bytes == "RIFT" read as a little-endian uint32


def rc4(key: bytes, data: bytes) -> bytes:
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 0xFF
        s[i], s[j] = s[j], s[i]
    out = bytearray(len(data))
    i = j = 0
    for n, b in enumerate(data):
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        out[n] = b ^ s[(s[i] + s[j]) & 0xFF]
    return bytes(out)


def clean_ticket(ticket: str) -> str:
    """The ticket exactly as it travels inside the blob.

    The server prefixes a line with the byte count; the game expects the
    document from ``Signature:`` or ``<?xml`` onwards. The C helper applies this
    same rule, and the test compares the two results.
    """
    lines = ticket.replace("\r", "").split("\n")
    start = next((i for i, ln in enumerate(lines)
                  if ln.startswith("Signature:") or ln.startswith("<?xml")), 0)
    return "\n".join(lines[start:]).rstrip()


def build_rift_buffer(ticket: str, rc_key: bytes | None = None) -> bytes:
    """rcKey(8) ++ ciphertextLen(uint32 LE) ++ RC4(magic ++ ticket ++ \\0)."""
    content = clean_ticket(ticket).encode("utf-8") + b"\x00"
    plaintext = RIFT_MAGIC + content
    rc_key = rc_key or secrets.token_bytes(8)
    ct = rc4(rc_key, plaintext)
    return rc_key + struct.pack("<I", len(ct)) + ct
