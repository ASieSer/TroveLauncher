"""Where secrets are kept, depending on the system.

Two things about an account are secret: its Glyph **password** and its Trion
**ticket**, which is a session credential live for about 48 hours. Neither may
end up in the clear on disk.

On Windows DPAPI solves that (``CryptProtectData``, user scope): the encrypted
blob is written to a file in our folder and only this user on this machine can
read it back. Linux has no DPAPI, so the equivalent place is the desktop
**Secret Service** (GNOME Keyring, KWallet...) through ``keyring``: the secret
never touches our folder at all.

This module hides that difference behind a store with four operations. Each
secret's key (``auth-<hash>``, ``cred-<hash>``) is the same on both systems, and
on Windows it matches the filename already in use: upgrading loses nothing.

**If there is nowhere to keep it, it is not kept.** Without DPAPI or a Secret
Service the password is simply not remembered and the ticket stays in memory,
alive only for as long as the application session. The ticket used to fall back
to plaintext, which is exactly what must not happen to a live credential.
"""

from __future__ import annotations

import base64
import ctypes
import sys
import threading
from pathlib import Path

from .paths import app_data_dir

# The name we appear under in the desktop keyring.
KEYRING_SERVICE = "Trove Accounts Hub"


class Vault:
    """The common interface. ``available`` False means "store nothing"."""

    name = "none"
    available = False

    def set(self, key: str, data: bytes, entropy: bytes | None = None) -> bool:
        return False

    def get(self, key: str, entropy: bytes | None = None) -> bytes | None:
        return None

    def delete(self, key: str) -> None:
        pass


# --- Windows: DPAPI + a file ----------------------------------------------


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _DataBlob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _blob_bytes(blob: _DataBlob) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def dpapi_protect(data: bytes, entropy: bytes | None = None) -> bytes:
    ent = ctypes.byref(_blob(entropy)) if entropy else None
    out = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(_blob(data)), None, ent, None, None, 0, ctypes.byref(out)):
        raise OSError("CryptProtectData failed")
    try:
        return _blob_bytes(out)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def dpapi_unprotect(data: bytes, entropy: bytes | None = None) -> bytes:
    ent = ctypes.byref(_blob(entropy)) if entropy else None
    out = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(_blob(data)), None, ent, None, None, 0, ctypes.byref(out)):
        raise OSError("CryptUnprotectData failed")
    try:
        return _blob_bytes(out)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


class DpapiVault(Vault):
    """Encrypts with DPAPI and writes the blob to ``<data>/<key>.bin``.

    The names and the entropy are the long-standing ones on purpose: this module
    is new, but the files it reads were already there.
    """

    name = "DPAPI"
    available = True

    def _path(self, key: str) -> Path:
        return app_data_dir() / f"{key}.bin"

    def set(self, key: str, data: bytes, entropy: bytes | None = None) -> bool:
        try:
            blob = dpapi_protect(data, entropy)
        except Exception:
            return False
        try:
            self._path(key).write_bytes(blob)
            return True
        except OSError:
            return False

    def get(self, key: str, entropy: bytes | None = None) -> bytes | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            return dpapi_unprotect(path.read_bytes(), entropy)
        except Exception:
            return None

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink(missing_ok=True)
        except OSError:
            pass


# --- Linux: the desktop Secret Service ----------------------------------


class KeyringVault(Vault):
    """Stores in the desktop keyring through ``keyring``.

    The secret is kept base64-encoded because the Secret Service handles strings,
    not bytes. DPAPI's ``entropy`` has no equivalent here and is ignored: the
    isolation comes from the keyring itself, which only the user's session opens.
    """

    name = "Secret Service"
    available = True

    def __init__(self, backend):
        self._keyring = backend

    def set(self, key: str, data: bytes, entropy: bytes | None = None) -> bool:
        try:
            self._keyring.set_password(
                KEYRING_SERVICE, key, base64.b64encode(data).decode("ascii"))
            return True
        except Exception:
            return False

    def get(self, key: str, entropy: bytes | None = None) -> bytes | None:
        try:
            raw = self._keyring.get_password(KEYRING_SERVICE, key)
        except Exception:
            return None
        if not raw:
            return None
        try:
            return base64.b64decode(raw)
        except Exception:
            return None

    def delete(self, key: str) -> None:
        try:
            self._keyring.delete_password(KEYRING_SERVICE, key)
        except Exception:
            pass  # not there, or the keyring is not answering: nothing to delete


# --- picking one -------------------------------------------------------------

_vault: Vault | None = None
_lock = threading.Lock()
_reason = ""


def _pick(log=print) -> tuple[Vault, str]:
    if sys.platform == "win32":
        return DpapiVault(), ""

    try:
        import keyring                                  # noqa: PLC0415
    except Exception as exc:
        return Vault(), (f"the keyring module is missing ({exc}); passwords will not be "
                         f"remembered and the ticket will live in memory only")

    # The module being present does not mean there is a keyring behind it: in a
    # session without D-Bus, keyring picks a backend that fails on first use. It
    # is probed here, once, rather than discovered while saving a password.
    try:
        backend = keyring.get_keyring()
        probe = f"{KEYRING_SERVICE} probe"
        keyring.set_password(KEYRING_SERVICE, probe, "ok")
        ok = keyring.get_password(KEYRING_SERVICE, probe) == "ok"
        keyring.delete_password(KEYRING_SERVICE, probe)
    except Exception as exc:
        return Vault(), (f"the desktop keyring is not answering ({exc}); passwords "
                         f"will not be remembered and the ticket will live in "
                         f"memory only")
    if not ok:
        return Vault(), "the desktop keyring does not return what it is given"
    return KeyringVault(keyring), f"keyring: {backend.__class__.__name__}"


def vault(log=print) -> Vault:
    """This system's store. Picked once and remembered."""
    global _vault, _reason
    with _lock:
        if _vault is None:
            _vault, _reason = _pick(log)
            if _reason:
                log(f"[vault] {_reason}")
            _purge_plaintext_leftovers(log)
        return _vault


def status() -> dict:
    """For the interface: which store there is and, if there is none, why."""
    v = vault()
    return {"backend": v.name, "available": v.available, "detail": _reason}


def _purge_plaintext_leftovers(log=print) -> None:
    """Deletes tickets an earlier version may have left in the clear.

    Off Windows, ``trionauth`` used to write the ticket unencrypted when DPAPI
    was unavailable. It no longer does, but a file from back then would still be
    sitting there with a readable credential inside, so it is removed on sight.
    """
    if sys.platform == "win32":
        return
    try:
        for path in app_data_dir().glob("auth*.bin"):
            path.unlink(missing_ok=True)
            log(f"[vault] removed {path.name}: a plaintext ticket left by an "
                f"earlier version")
    except OSError:
        pass
