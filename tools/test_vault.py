"""Comprueba el almacén de secretos de `core/vault.py`.

Aquí no hay ni DPAPI ni un llavero de escritorio, así que se prueban las tres
piezas que sí se pueden aislar: el mapeo clave -> fichero de Windows (que es lo
que mantiene compatible una instalación anterior), el backend de llavero contra
uno falso, y la degradación cuando no hay dónde guardar.

    python tools/test_vault.py
"""
import base64
import importlib
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ok = True


def check(label, cond):
    global ok
    ok = ok and cond
    print(("  OK  " if cond else " FALLA") + " | " + label)


class FakeKeyring:
    """Lo mínimo que `vault` le pide a keyring."""

    def __init__(self, broken=False):
        self.items = {}
        self.broken = broken

    def get_keyring(self):
        return self

    def set_password(self, service, key, value):
        if self.broken:
            raise RuntimeError("sin D-Bus")
        self.items[(service, key)] = value

    def get_password(self, service, key):
        if self.broken:
            raise RuntimeError("sin D-Bus")
        return self.items.get((service, key))

    def delete_password(self, service, key):
        if self.broken:
            raise RuntimeError("sin D-Bus")
        self.items.pop((service, key), None)


with tempfile.TemporaryDirectory() as t:
    os.environ["XDG_DATA_HOME"] = t
    import core.vault as vault
    importlib.reload(vault)
    from core import prefs
    importlib.reload(prefs)

    # --- 1) el almacén de Windows guarda donde siempre --------------------
    # DPAPI no existe aquí, así que se sustituye el cifrado por la identidad:
    # lo que se comprueba es el nombre del fichero, que es lo que hereda una
    # instalación anterior.
    # El sustituto invierte los bytes: no es cifrado, pero basta para
    # comprobar que al fichero va lo que devuelve el protector y no el texto
    # original.
    vault.dpapi_protect = lambda data, entropy=None: b"CIFRADO:" + data[::-1]
    vault.dpapi_unprotect = lambda data, entropy=None: data.removeprefix(b"CIFRADO:")[::-1]
    win = vault.DpapiVault()
    check("guarda la contraseña", win.set("cred-abc123", b"secreto", b"ent"))
    blob = (Path(t) / "TroveAccountsHub" / "cred-abc123.bin")
    check("en el fichero de siempre (cred-<hash>.bin)", blob.exists())
    check("cifrado, no en claro", b"secreto" not in blob.read_bytes())
    check("y se recupera", win.get("cred-abc123", b"ent") == b"secreto")
    check("las claves siguen a los nombres previos",
          prefs.cred_key("a@b.com").startswith("cred-")
          and prefs.auth_key("a@b.com").startswith("auth-")
          and prefs.auth_key() == "auth_cache" and prefs.cred_key() == "credentials")
    win.delete("cred-abc123")
    check("borrar retira el fichero", not blob.exists())

    # --- 2) el llavero del escritorio -------------------------------------
    fake = FakeKeyring()
    ring = vault.KeyringVault(fake)
    check("guarda en el llavero", ring.set("auth-abc123", b"ticket-vivo"))
    stored = fake.items[(vault.KEYRING_SERVICE, "auth-abc123")]
    check("y no en un fichero nuestro",
          not (Path(t) / "TroveAccountsHub" / "auth-abc123.bin").exists())
    check("va en base64, no en claro",
          "ticket-vivo" not in stored and base64.b64decode(stored) == b"ticket-vivo")
    check("se recupera igual", ring.get("auth-abc123") == b"ticket-vivo")
    ring.delete("auth-abc123")
    check("y se puede borrar", ring.get("auth-abc123") is None)

    # --- 3) un llavero que no responde no rompe nada -----------------------
    broken = vault.KeyringVault(FakeKeyring(broken=True))
    check("guardar devuelve False, no revienta", broken.set("cred-x", b"p") is False)
    check("leer devuelve None", broken.get("cred-x") is None)
    broken.delete("cred-x")

    # --- 4) sin almacén: no se guarda NADA ---------------------------------
    none = vault.Vault()
    check("sin almacén no se guarda", none.set("cred-x", b"p") is False)
    check("sin almacén no hay nada que leer", none.get("cred-x") is None)

    # --- 5) se retira el ticket en claro de versiones anteriores -----------
    data_dir = Path(t) / "TroveAccountsHub"
    legacy = data_dir / "auth-deadbeef.bin"
    legacy.write_bytes(b'{"ticket": "<?xml ... en claro"}')
    vault._purge_plaintext_leftovers(log=lambda *a: None)
    check("el ticket en claro heredado se borra", not legacy.exists())

print("\n" + ("TODO OK" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
