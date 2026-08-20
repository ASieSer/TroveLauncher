"""Dónde se guardan los secretos, según el sistema.

Dos cosas de una cuenta son secretas: su **contraseña** de Glyph y su **ticket**
de Trion, que es una credencial de sesión viva durante unas 48 horas. Ninguna de
las dos puede acabar en claro en el disco.

En Windows eso lo resuelve DPAPI (``CryptProtectData``, ámbito de usuario): el
blob cifrado se escribe en un fichero de nuestra carpeta y sólo este usuario en
esta máquina puede volver a leerlo. En Linux no existe DPAPI, así que el sitio
equivalente es el **Secret Service** del escritorio (GNOME Keyring, KWallet…) a
través de ``keyring``: el secreto no llega a tocar nuestra carpeta.

Este módulo esconde esa diferencia detrás de un almacén con cuatro operaciones.
La clave de cada secreto (``auth-<hash>``, ``cred-<hash>``) es la misma en los
dos sistemas, y en Windows coincide con el nombre de fichero que ya se venía
usando: quien actualice no pierde nada.

**Si no hay dónde guardar, no se guarda.** Sin DPAPI o sin Secret Service, la
contraseña sencillamente no se recuerda y el ticket se queda en memoria, vivo
sólo mientras dure la sesión de la aplicación. Antes el ticket caía a texto
plano, que es exactamente lo que no debe pasar con una credencial viva.
"""

from __future__ import annotations

import base64
import ctypes
import sys
import threading
from pathlib import Path

from .paths import app_data_dir

# Nombre con el que aparecemos en el llavero del escritorio.
KEYRING_SERVICE = "Trove Accounts Hub"


class Vault:
    """Interfaz común. ``available`` en False significa "no guardes nada"."""

    name = "none"
    available = False

    def set(self, key: str, data: bytes, entropy: bytes | None = None) -> bool:
        return False

    def get(self, key: str, entropy: bytes | None = None) -> bytes | None:
        return None

    def delete(self, key: str) -> None:
        pass


# --- Windows: DPAPI + fichero ----------------------------------------------


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
    """Cifra con DPAPI y escribe el blob en ``<datos>/<clave>.bin``.

    Los nombres y la entropía son los de siempre a propósito: este módulo es
    nuevo, pero los ficheros que lee ya estaban ahí.
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


# --- Linux: Secret Service del escritorio ----------------------------------


class KeyringVault(Vault):
    """Guarda en el llavero del escritorio a través de ``keyring``.

    El secreto se guarda en base64 porque el Secret Service maneja cadenas, no
    bytes. La ``entropy`` de DPAPI no tiene equivalente aquí y se ignora: el
    aislamiento lo da el propio llavero, que sólo abre la sesión del usuario.
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
            pass  # no estaba, o el llavero no responde: nada que borrar


# --- selección -------------------------------------------------------------

_vault: Vault | None = None
_lock = threading.Lock()
_reason = ""


def _pick(log=print) -> tuple[Vault, str]:
    if sys.platform == "win32":
        return DpapiVault(), ""

    try:
        import keyring                                  # noqa: PLC0415
    except Exception as exc:
        return Vault(), (f"no está el módulo keyring ({exc}); las contraseñas no se "
                         f"recordarán y el ticket vivirá sólo en memoria")

    # Que el módulo esté no significa que haya un llavero detrás: en una sesión
    # sin D-Bus, keyring elige un backend que falla al primer uso. Se comprueba
    # aquí, una vez, en lugar de descubrirlo al guardar una contraseña.
    try:
        backend = keyring.get_keyring()
        probe = f"{KEYRING_SERVICE} probe"
        keyring.set_password(KEYRING_SERVICE, probe, "ok")
        ok = keyring.get_password(KEYRING_SERVICE, probe) == "ok"
        keyring.delete_password(KEYRING_SERVICE, probe)
    except Exception as exc:
        return Vault(), (f"el llavero del escritorio no responde ({exc}); las "
                         f"contraseñas no se recordarán y el ticket vivirá sólo "
                         f"en memoria")
    if not ok:
        return Vault(), "el llavero del escritorio no devuelve lo que se le guarda"
    return KeyringVault(keyring), f"llavero: {backend.__class__.__name__}"


def vault(log=print) -> Vault:
    """El almacén de este sistema. Se elige una vez y se recuerda."""
    global _vault, _reason
    with _lock:
        if _vault is None:
            _vault, _reason = _pick(log)
            if _reason:
                log(f"[vault] {_reason}")
            _purge_plaintext_leftovers(log)
        return _vault


def status() -> dict:
    """Para la interfaz: qué almacén hay y, si no hay, por qué."""
    v = vault()
    return {"backend": v.name, "available": v.available, "detail": _reason}


def _purge_plaintext_leftovers(log=print) -> None:
    """Borra tickets que una versión anterior pudo dejar en claro.

    Fuera de Windows, ``trionauth`` escribía el ticket sin cifrar cuando DPAPI no
    estaba disponible. Ya no lo hace, pero un fichero de entonces seguiría ahí
    con una credencial legible dentro, así que se retira en cuanto lo vemos.
    """
    if sys.platform == "win32":
        return
    try:
        for path in app_data_dir().glob("auth*.bin"):
            path.unlink(missing_ok=True)
            log(f"[vault] retirado {path.name}: era un ticket en claro de una "
                f"versión anterior")
    except OSError:
        pass
