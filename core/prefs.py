"""Preferencias, cuentas, grupos y contraseñas recordadas.

Modelo de datos
---------------

Una **cuenta** es un objeto, no sólo un email::

    {"email": "...", "name": "XxWolf_AlexX", "color": "#8b5cf6",
     "group": "a1b2c3d4" | None, "region": "NA" | "EU" | "PTS"}

Un **grupo** agrupa cuentas y sólo lleva presentación::

    {"id": "a1b2c3d4", "name": "Goats", "color": "#8b5cf6", "collapsed": false}

El orden de las listas ES el orden que se ve en pantalla: reordenar arrastrando
consiste en reescribir las listas. Las cuentas con ``group: None`` caen en la
zona «sin grupo».

Seguridad de las contraseñas
----------------------------

Ni la contraseña ni el ticket se guardan aquí: van al almacén de secretos del
sistema (ver ``vault.py``) — DPAPI en Windows, el llavero del escritorio en
Linux. Si no hay ninguno de los dos, NUNCA caemos a texto plano: sencillamente
no se recuerda nada.

Cada cuenta guarda su ticket y su contraseña bajo claves separadas, nombradas
con un hash del email (sólo como discriminador, jamás como comprobación de
integridad o autenticación), para que varias cuentas de Glyph puedan estar
activas a la vez.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import uuid
from pathlib import Path

from . import vault as vault_mod
from .paths import app_data_dir, prefs_path

# Entropía específica de la app para el cifrado DPAPI de las credenciales. Sólo
# la usa el almacén de Windows; es un identificador fijo y no un nombre a la
# vista, así que no sigue a la aplicación cuando ésta se renombra: cambiarlo
# dejaría ilegibles las contraseñas ya guardadas.
_CRED_ENTROPY = b"TroveLauncher.credentials.v1"

_LOCK = threading.RLock()

REGIONS = ("NA", "EU", "PTS")
DEFAULT_REGION = "EU"

# Paleta para los puntos de grupo y los avatares de cuenta.
PALETTE = [
    "#8b5cf6", "#22c55e", "#38bdf8", "#f43f5e", "#f59e0b",
    "#14b8a6", "#ec4899", "#6366f1", "#84cc16", "#fb7185",
]

DEFAULTS = {
    "schema": 2,
    "groups": [],
    "accounts": [],
    "remember_password": True,
    "update_first": True,
    "game_path": "",          # instalación de Live
    "pts_game_path": "",      # instalación de PTS (se autodetecta si está vacía)
    "custom_dirs": [],
    "reparent_glyph": False,
    "hide_emails": True,
    # Apariencia: acento, acentos propios guardados, partículas del fondo,
    # familia tipográfica ("system" | "quicksand" | "comfortaa") y tema de club
    # ("" | "mystic-cave" | "arsyn" | "sayro"), que fija el acento mientras
    # esté puesto.
    "theme": {"accent": "#22c55e", "customs": [], "stars": True,
              "font": "system", "club": ""},
}


# --- lectura/escritura del fichero -----------------------------------------


def _read_json(path: Path) -> dict | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except Exception:
        return None


def load() -> dict:
    """Preferencias del disco, con la copia de seguridad como red.

    Si el fichero principal no se puede leer (truncado por un corte de luz, por
    ejemplo) se intenta el ``.bak`` ANTES de rendirse. Sin esto, un JSON roto
    daría una configuración vacía y el primer guardado posterior borraría cuentas
    y grupos para siempre.
    """
    path = prefs_path()
    data = dict(DEFAULTS)

    raw = _read_json(path)
    if raw is None and path.exists():
        backup = path.with_suffix(".json.bak")
        raw = _read_json(backup)
        if raw is not None:
            print(f"[prefs] {path.name} ilegible; recuperado desde {backup.name}")

    if raw is not None:
        data.update(raw)
    # Resto de la disposición en tabla, que ya no existe: se descarta al cargar
    # y el primer guardado la borra del fichero.
    data.pop("layout", None)
    return _migrate(data)


def save(**changes) -> dict:
    """Fusiona ``changes`` (ignorando los None) y persiste."""
    with _LOCK:
        data = load()
        data.update({k: v for k, v in changes.items() if v is not None})
        _write(data)
        return data


def _write(data: dict) -> None:
    """Guarda de forma atómica: temporal -> fsync -> reemplazo.

    Escribir directamente sobre prefs.json lo trunca antes de rellenarlo, así que
    un corte de luz en ese instante deja el fichero vacío y se pierden todas las
    cuentas. ``os.replace`` es atómico, de modo que el fichero real pasa del
    contenido viejo al nuevo sin estado intermedio. Antes de sustituirlo se
    guarda una copia ``.bak`` que ``load()`` sabe usar.
    """
    path = prefs_path()
    tmp = path.with_suffix(".json.tmp")
    try:
        payload = json.dumps(data, indent=2, ensure_ascii=False)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())   # que los bytes estén en disco, no en caché
        if path.exists():
            try:
                shutil.copy2(path, path.with_suffix(".json.bak"))
            except OSError:
                pass  # sin copia de seguridad, pero el guardado sigue siendo atómico
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


# --- migración desde el formato antiguo ------------------------------------


def _migrate(data: dict) -> dict:
    """Convierte el formato v1 (cuentas como lista de emails + dict de alias) al
    modelo de objetos. Se ejecuta en cada carga; en cuanto los datos ya están en
    v2 no toca nada."""
    accounts = data.get("accounts") or []
    if data.get("schema", 1) >= 2 and all(isinstance(a, dict) for a in accounts):
        return data

    aliases = data.get("aliases", {}) or {}
    # La región global de v1 pasa a ser la región inicial de cada cuenta.
    legacy_region = {"live-na": "NA", "live-eu": "EU", "pts": "PTS"}.get(
        data.get("server", ""), DEFAULT_REGION)

    migrated = []
    for item in accounts:
        email = item if isinstance(item, str) else (item or {}).get("email", "")
        if not email:
            continue
        migrated.append({
            "email": email,
            "name": aliases.get(email.strip().lower(), ""),
            "color": color_for(email),
            "group": None,
            "region": legacy_region,
            "auto_relog": False,
        })

    data["accounts"] = migrated
    data["schema"] = 2
    data.pop("aliases", None)
    data.pop("server", None)
    data.pop("selected_email", None)
    data.pop("remember_email", None)
    _write(data)
    return data


# --- utilidades -------------------------------------------------------------


def color_for(email: str) -> str:
    """Color estable derivado del email, para que una cuenta nueva ya nazca con
    un avatar distinguible sin que el usuario elija nada."""
    digest = hashlib.sha256((email or "").strip().lower().encode("utf-8"),
                            usedforsecurity=False).digest()
    return PALETTE[digest[0] % len(PALETTE)]


def _email_hash(email: str) -> str:
    # Sólo discrimina nombres de fichero; nunca es una comprobación de seguridad.
    return hashlib.sha256(
        (email or "").strip().lower().encode("utf-8"), usedforsecurity=False
    ).hexdigest()[:12]


def mask_email(email: str) -> str:
    """Ofusca un email dejando la inicial: ``a**********@dominio``."""
    email = (email or "").strip()
    if "@" not in email:
        return "****" if email else ""
    local, _, domain = email.partition("@")
    head = local[0] if local else ""
    return f"{head}{'*' * max(len(local) - 1, 3)}@{domain}"


def display_name(email: str) -> str:
    """Lo que mostramos de una cuenta: su nombre si lo tiene, si no el email
    enmascarado. Nunca la dirección completa."""
    account = get_account(email)
    if account and account.get("name"):
        return account["name"]
    return mask_email(email)


# --- rutas por cuenta -------------------------------------------------------


def auth_key(email: str = "") -> str:
    """Clave del ticket en el almacén de secretos.

    En Windows es además el nombre del fichero que ya existía
    (``auth-<hash>.bin``), para que actualizar no pierda la sesión de nadie.
    """
    return f"auth-{_email_hash(email)}" if email else "auth_cache"


def cred_key(email: str = "") -> str:
    """Clave de la contraseña en el almacén de secretos."""
    return f"cred-{_email_hash(email)}" if email else "credentials"


def update_db_path(branch: str, game_dir: Path) -> Path:
    """Estado 'qué hay en disco' por (rama, carpeta): dos instalaciones pueden
    estar en versiones distintas, así que no pueden compartir base de datos."""
    key = hashlib.sha256(
        str(Path(game_dir).resolve()).lower().encode("utf-8"), usedforsecurity=False
    ).hexdigest()[:12]
    return app_data_dir() / f"update-{branch}-{key}.sqlite"


# --- cuentas ----------------------------------------------------------------


def accounts() -> list[dict]:
    return [dict(a) for a in load().get("accounts", []) if isinstance(a, dict)]


def get_account(email: str) -> dict | None:
    email = (email or "").strip().lower()
    for account in load().get("accounts", []):
        if isinstance(account, dict) and account.get("email", "").lower() == email:
            return dict(account)
    return None


def upsert_account(email: str, **fields) -> dict | None:
    """Crea la cuenta si no existe y aplica los campos indicados.

    Sólo se aceptan las claves del modelo: así una llamada desde la interfaz no
    puede colar campos arbitrarios en el fichero de preferencias.
    """
    email = (email or "").strip()
    if not email:
        return None

    allowed = {"name", "color", "group", "region", "auto_relog", "flagged"}
    changes = {k: v for k, v in fields.items() if k in allowed}
    for flag in ("auto_relog", "flagged"):
        if flag in changes:
            changes[flag] = bool(changes[flag])
    if "region" in changes and changes["region"] not in REGIONS:
        changes.pop("region")

    with _LOCK:
        data = load()
        items = [a for a in data.get("accounts", []) if isinstance(a, dict)]
        for account in items:
            if account.get("email", "").lower() == email.lower():
                account.update(changes)
                result = dict(account)
                break
        else:
            account = {
                "email": email,
                "name": changes.get("name", ""),
                "color": changes.get("color") or color_for(email),
                "group": changes.get("group"),
                "region": changes.get("region", DEFAULT_REGION),
                "auto_relog": bool(changes.get("auto_relog", False)),
                # Marca manual (p. ej. cuenta baneada): sólo afecta a cómo se pinta.
                "flagged": bool(changes.get("flagged", False)),
            }
            items.append(account)
            result = dict(account)
        data["accounts"] = items
        _write(data)
        return result


def remove_account(email: str) -> None:
    """Olvida la cuenta por completo: entrada, ticket cacheado y contraseña."""
    email = (email or "").strip()
    clear_credentials(email)
    try:
        vault_mod.vault().delete(auth_key(email))
    except OSError:
        pass
    with _LOCK:
        data = load()
        data["accounts"] = [a for a in data.get("accounts", [])
                            if isinstance(a, dict)
                            and a.get("email", "").lower() != email.lower()]
        _write(data)


# --- grupos -----------------------------------------------------------------


def groups() -> list[dict]:
    return [dict(g) for g in load().get("groups", []) if isinstance(g, dict)]


def create_group(name: str = "", color: str = "") -> dict:
    with _LOCK:
        data = load()
        items = [g for g in data.get("groups", []) if isinstance(g, dict)]
        group = {
            "id": uuid.uuid4().hex[:8],
            "name": (name or "").strip() or f"Grupo {len(items) + 1}",
            "color": color or PALETTE[len(items) % len(PALETTE)],
            "collapsed": False,
        }
        items.append(group)
        data["groups"] = items
        _write(data)
        return dict(group)


def update_group(group_id: str, **fields) -> dict | None:
    allowed = {"name", "color", "collapsed"}
    changes = {k: v for k, v in fields.items() if k in allowed}
    with _LOCK:
        data = load()
        for group in data.get("groups", []):
            if isinstance(group, dict) and group.get("id") == group_id:
                group.update(changes)
                _write(data)
                return dict(group)
    return None


def delete_group(group_id: str) -> None:
    """Borra el grupo; sus cuentas pasan a «sin grupo», nunca se pierden."""
    with _LOCK:
        data = load()
        data["groups"] = [g for g in data.get("groups", [])
                          if isinstance(g, dict) and g.get("id") != group_id]
        for account in data.get("accounts", []):
            if isinstance(account, dict) and account.get("group") == group_id:
                account["group"] = None
        _write(data)


# --- reordenación (arrastrar y soltar) --------------------------------------


def reorder_groups(order: list) -> None:
    """Reescribe el orden de los grupos. Los ids que no aparezcan en ``order`` se
    conservan al final, para que un envío incompleto nunca borre un grupo."""
    with _LOCK:
        data = load()
        items = [g for g in data.get("groups", []) if isinstance(g, dict)]
        by_id = {g["id"]: g for g in items}
        ordered = [by_id.pop(gid) for gid in order if gid in by_id]
        ordered.extend(by_id.values())
        data["groups"] = ordered
        _write(data)


def reorder_accounts(order: list) -> None:
    """Aplica el nuevo orden y la nueva pertenencia a grupo de las cuentas.

    ``order`` es una lista de ``{"email": ..., "group": ... | None}`` en el orden
    final deseado. Igual que en los grupos, cualquier cuenta ausente se conserva
    al final: la interfaz no puede provocar una pérdida de datos por enviar una
    lista parcial.
    """
    with _LOCK:
        data = load()
        items = [a for a in data.get("accounts", []) if isinstance(a, dict)]
        by_email = {a.get("email", "").lower(): a for a in items}

        ordered = []
        for entry in order or []:
            email = (entry.get("email") if isinstance(entry, dict) else entry) or ""
            account = by_email.pop(email.strip().lower(), None)
            if account is None:
                continue
            if isinstance(entry, dict) and "group" in entry:
                account["group"] = entry["group"] or None
            ordered.append(account)
        ordered.extend(by_email.values())

        data["accounts"] = ordered
        _write(data)


# --- credenciales recordadas (DPAPI) ---------------------------------------


def save_credentials(email: str, password: str) -> bool:
    """Guarda la contraseña en el almacén del sistema. False = no se guardó.

    Sin almacén no hay guardado: preferimos que el usuario tenga que reescribir
    la contraseña a dejarla legible en el disco.
    """
    if not password:
        return False
    raw = json.dumps({"email": email or "", "password": password}).encode("utf-8")
    return vault_mod.vault().set(cred_key(email), raw, _CRED_ENTROPY)


def load_credentials(email: str = "") -> dict | None:
    raw = vault_mod.vault().get(cred_key(email), _CRED_ENTROPY)
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def clear_credentials(email: str = "") -> None:
    try:
        vault_mod.vault().delete(cred_key(email))
    except OSError:
        pass


def has_saved_password(email: str = "") -> bool:
    creds = load_credentials(email)
    return bool(creds and creds.get("password"))
