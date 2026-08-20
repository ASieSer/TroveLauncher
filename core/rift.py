"""El blob del ticket: RC4 + cabecera "RIFT".

Vivía dentro de ``inject.py``, que sólo se puede importar en Windows. Sacarlo
aquí tiene dos motivos: lo usan los dos caminos —el de Windows y el ayudante de
Wine, que lo reimplementa en C— y así se puede comparar una implementación con
la otra en una prueba (``tools/test_wine_helper.py``), en lugar de confiar en
que dos códigos separados hagan lo mismo.

Es código puro: no toca Windows ni el disco.
"""

from __future__ import annotations

import secrets
import struct

RIFT_MAGIC = b"\x54\x46\x49\x52"  # 'TFIR' bytes == "RIFT" leído como uint32 LE


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
    """El ticket tal y como viaja dentro del blob.

    El servidor antepone una línea con el número de bytes; el juego espera el
    documento a partir de ``Signature:`` o de ``<?xml``. El ayudante en C aplica
    esta misma regla, y la prueba compara los dos resultados.
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
