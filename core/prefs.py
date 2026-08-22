"""Preferences, accounts, groups and remembered passwords.

Data model
----------

An **account** is an object, not just an email::

    {"email": "...", "name": "XxWolf_AlexX", "color": "#8b5cf6",
     "group": "a1b2c3d4" | None, "region": "NA" | "EU" | "PTS"}

A **group** collects accounts and carries presentation only::

    {"id": "a1b2c3d4", "name": "Goats", "color": "#8b5cf6", "collapsed": false}

The order of the lists IS the order seen on screen: reordering by dragging means
rewriting the lists. Accounts with ``group: None`` fall into the "Ungrouped"
area.

Password safety
---------------

Neither the password nor the ticket is stored here: they go to the system secret
store (see ``vault.py``) - DPAPI on Windows, the desktop keyring on Linux. If
neither exists we NEVER fall back to plaintext: nothing is simply remembered.

Each account keeps its ticket and its password under separate keys, named with a
hash of the email (purely as a discriminator, never as an integrity or
authentication check), so several Glyph accounts can be active at once.
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
from .paths import app_data_dir, note, prefs_path

# App-specific entropy for the DPAPI encryption of credentials. Only the
# Windows store uses it; it is a fixed identifier and not a user-visible name,
# so it does not follow the application when that is renamed: changing it would
# render already-saved passwords unreadable.
_CRED_ENTROPY = b"TroveLauncher.credentials.v1"

_LOCK = threading.RLock()

REGIONS = ("NA", "EU", "PTS")
DEFAULT_REGION = "EU"

# Palette for the group dots and the account avatars.
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
    "game_path": "",          # the Live installation
    "pts_game_path": "",      # the PTS installation (auto-detected when empty)
    "custom_dirs": [],
    "reparent_glyph": False,
    "hide_emails": True,
    # Only used off Windows: which Wine launches the game and in which prefix.
    # Empty = worked out automatically (see core/winehost.py).
    "wine_binary": "",
    "wine_prefix": "",
    # Appearance: accent, the user's saved accents, background particles, font
    # family ("system" | "quicksand" | "comfortaa" | "quantico") and club theme
    # ("" | "mystic-cave" | "arsyn" | "sayro"), which pins the accent while it
    # is on.
    "theme": {"accent": "#22c55e", "customs": [], "stars": True,
              "font": "system", "club": ""},
}


# --- reading and writing the file -----------------------------------------


def _read_json(path: Path) -> dict | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except Exception:
        return None


def load() -> dict:
    """Preferences from disk, with the backup as a safety net.

    If the main file cannot be read (truncated by a power cut, say) the ``.bak``
    is tried BEFORE giving up. Without this, broken JSON would give an empty
    configuration and the first save afterwards would wipe accounts and groups
    for good.
    """
    path = prefs_path()
    data = dict(DEFAULTS)

    raw = _read_json(path)
    if raw is None and path.exists():
        backup = path.with_suffix(".json.bak")
        raw = _read_json(backup)
        if raw is not None:
            note(f"[prefs] {path.name} unreadable; recovered from {backup.name}")

    if raw is not None:
        data.update(raw)
    # Leftover from the table layout, which no longer exists: dropped on load,
    # and the first save removes it from the file.
    data.pop("layout", None)
    return _migrate(data)


def save(**changes) -> dict:
    """Merges ``changes`` (ignoring None values) and persists."""
    with _LOCK:
        data = load()
        data.update({k: v for k, v in changes.items() if v is not None})
        _write(data)
        return data


def _write(data: dict) -> None:
    """Saves atomically: temporary file -> fsync -> replace.

    Writing straight over prefs.json truncates it before refilling it, so a
    power cut at that instant leaves the file empty and every account is lost.
    ``os.replace`` is atomic, so the real file goes from the old contents to the
    new with no state in between. Before replacing it, a ``.bak`` copy is kept
    that ``load()`` knows how to use.
    """
    path = prefs_path()
    tmp = path.with_suffix(".json.tmp")
    try:
        payload = json.dumps(data, indent=2, ensure_ascii=False)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())   # get the bytes onto the disk, not into a cache
        if path.exists():
            try:
                shutil.copy2(path, path.with_suffix(".json.bak"))
            except OSError:
                pass  # no backup, but the save is still atomic
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


# --- migration from the old format ------------------------------------


def _migrate(data: dict) -> dict:
    """Converts the v1 format (accounts as a list of emails plus an alias dict)
    to the object model. It runs on every load; once the data is already v2 it
    touches nothing."""
    accounts = data.get("accounts") or []
    if data.get("schema", 1) >= 2 and all(isinstance(a, dict) for a in accounts):
        return data

    aliases = data.get("aliases", {}) or {}
    # The single global region of v1 becomes each account's starting region.
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


# --- helpers -------------------------------------------------------------


def color_for(email: str) -> str:
    """A stable colour derived from the email, so a new account is born with a
    distinguishable avatar without the user picking anything."""
    digest = hashlib.sha256((email or "").strip().lower().encode("utf-8"),
                            usedforsecurity=False).digest()
    return PALETTE[digest[0] % len(PALETTE)]


def _email_hash(email: str) -> str:
    # Only discriminates filenames; never a security check.
    return hashlib.sha256(
        (email or "").strip().lower().encode("utf-8"), usedforsecurity=False
    ).hexdigest()[:12]


def mask_email(email: str) -> str:
    """Obscures an email but keeps the initial: ``a**********@domain``."""
    email = (email or "").strip()
    if "@" not in email:
        return "****" if email else ""
    local, _, domain = email.partition("@")
    head = local[0] if local else ""
    return f"{head}{'*' * max(len(local) - 1, 3)}@{domain}"


def display_name(email: str) -> str:
    """What we show for an account: its name if it has one, otherwise the
    masked email. Never the full address."""
    account = get_account(email)
    if account and account.get("name"):
        return account["name"]
    return mask_email(email)


# --- per-account keys -------------------------------------------------------


def auth_key(email: str = "") -> str:
    """The ticket's key in the secret store.

    On Windows it doubles as the filename that already existed
    (``auth-<hash>.bin``), so upgrading loses nobody's session.
    """
    return f"auth-{_email_hash(email)}" if email else "auth_cache"


def cred_key(email: str = "") -> str:
    """The password's key in the secret store."""
    return f"cred-{_email_hash(email)}" if email else "credentials"


def update_db_path(branch: str, game_dir: Path) -> Path:
    """The "what is on disk" state per (branch, folder): two installations can
    be on different versions, so they cannot share a database."""
    key = hashlib.sha256(
        str(Path(game_dir).resolve()).lower().encode("utf-8"), usedforsecurity=False
    ).hexdigest()[:12]
    return app_data_dir() / f"update-{branch}-{key}.sqlite"


# --- accounts ----------------------------------------------------------------


def accounts() -> list[dict]:
    return [dict(a) for a in load().get("accounts", []) if isinstance(a, dict)]


def get_account(email: str) -> dict | None:
    email = (email or "").strip().lower()
    for account in load().get("accounts", []):
        if isinstance(account, dict) and account.get("email", "").lower() == email:
            return dict(account)
    return None


def upsert_account(email: str, **fields) -> dict | None:
    """Creates the account if it does not exist and applies the given fields.

    Only the model's keys are accepted, so a call from the interface cannot slip
    arbitrary fields into the preferences file.
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
                # Manual mark (a banned account, say): affects painting only.
                "flagged": bool(changes.get("flagged", False)),
            }
            items.append(account)
            result = dict(account)
        data["accounts"] = items
        _write(data)
        return result


def remove_account(email: str) -> None:
    """Forgets the account entirely: entry, cached ticket and password."""
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


# --- groups -----------------------------------------------------------------


def groups() -> list[dict]:
    return [dict(g) for g in load().get("groups", []) if isinstance(g, dict)]


def create_group(name: str = "", color: str = "") -> dict:
    with _LOCK:
        data = load()
        items = [g for g in data.get("groups", []) if isinstance(g, dict)]
        group = {
            "id": uuid.uuid4().hex[:8],
            "name": (name or "").strip() or f"Group {len(items) + 1}",
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
    """Deletes the group; its accounts move to "Ungrouped", never lost."""
    with _LOCK:
        data = load()
        data["groups"] = [g for g in data.get("groups", [])
                          if isinstance(g, dict) and g.get("id") != group_id]
        for account in data.get("accounts", []):
            if isinstance(account, dict) and account.get("group") == group_id:
                account["group"] = None
        _write(data)


# --- reordering (drag and drop) --------------------------------------


def reorder_groups(order: list) -> None:
    """Rewrites the group order. Ids missing from ``order`` are kept at the end,
    so an incomplete submission can never delete a group."""
    with _LOCK:
        data = load()
        items = [g for g in data.get("groups", []) if isinstance(g, dict)]
        by_id = {g["id"]: g for g in items}
        ordered = [by_id.pop(gid) for gid in order if gid in by_id]
        ordered.extend(by_id.values())
        data["groups"] = ordered
        _write(data)


def reorder_accounts(order: list) -> None:
    """Applies the accounts' new order and new group membership.

    ``order`` is a list of ``{"email": ..., "group": ... | None}`` in the wanted
    final order. As with groups, any absent account is kept at the end: the
    interface cannot cause data loss by sending a partial list.
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


# --- remembered credentials ---------------------------------------


def save_credentials(email: str, password: str) -> bool:
    """Stores the password in the system store. False = it was not stored.

    No store means no storing: we would rather the user had to retype the
    password than leave it readable on disk.
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
