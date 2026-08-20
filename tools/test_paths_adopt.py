"""Comprueba la adopción de la carpeta de datos con el nombre anterior.

La aplicación se llamaba Trove Launcher y guardaba en `%APPDATA%/TroveLauncher`;
ahora guarda en `%APPDATA%/TroveAccountsHub` y adopta lo que hubiera en la vieja
(ver `core/paths.py`). Eso corre una sola vez en la máquina de cada usuario y
sobre sus cuentas, así que conviene poder repetirlo a voluntad:

    python tools/test_paths_adopt.py

Sin dependencias: usa carpetas temporales y `XDG_DATA_HOME`, de modo que vale
igual en Linux para probar la lógica.
"""
import importlib, os, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

def fresh(root):
    os.environ["XDG_DATA_HOME"] = str(root)
    import core.paths as paths
    importlib.reload(paths)
    return paths

def seed_old(root, files):
    old = root / "TroveLauncher"
    old.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (old / name).write_text(body, encoding="utf-8")
    return old

ok = True
def check(label, cond):
    global ok
    ok = ok and cond
    print(("  OK  " if cond else " FALLA") + " | " + label)

# 1) instalación limpia
with tempfile.TemporaryDirectory() as t:
    root = Path(t); p = fresh(root)
    d = p.app_data_dir()
    check("crea la carpeta nueva", d == root / "TroveAccountsHub" and d.is_dir())
    check("sin marca de adopción", not (d / "adopted-from.txt").exists())

# 2) carpeta vieja con datos
with tempfile.TemporaryDirectory() as t:
    root = Path(t); old = seed_old(root, {
        "prefs.json": '{"accounts": [1]}',
        "cred-abc.bin": "secreto",
        "update-live-us-x.sqlite": "db",
        "macaddr.txt": "aa:bb",
    })
    (old / "logs").mkdir(); (old / "logs" / "a.log").write_text("hola")
    p = fresh(root)
    d = p.app_data_dir()
    check("copia prefs.json", (d / "prefs.json").read_text() == '{"accounts": [1]}')
    check("copia los blobs DPAPI", (d / "cred-abc.bin").read_text() == "secreto")
    check("copia la base de datos", (d / "update-live-us-x.sqlite").exists())
    check("copia subcarpetas", (d / "logs" / "a.log").read_text() == "hola")
    check("deja intacta la vieja", (old / "prefs.json").exists() and (old / "cred-abc.bin").exists())
    check("escribe la marca", (d / "adopted-from.txt").exists())

    # 3) segunda vuelta: no repite ni pisa
    (d / "prefs.json").write_text('{"accounts": [1,2]}')
    (old / "prefs.json").write_text('{"accounts": [9]}')
    p2 = fresh(root)
    p2.app_data_dir()
    check("no vuelve a copiar", (d / "prefs.json").read_text() == '{"accounts": [1,2]}')

    # 4) sin marca pero con prefs.json ya en uso: tampoco
    (d / "adopted-from.txt").unlink()
    p3 = fresh(root)
    p3.app_data_dir()
    check("respeta una carpeta ya en uso", (d / "prefs.json").read_text() == '{"accounts": [1,2]}')

# 5) nunca pisa un fichero que ya existe
with tempfile.TemporaryDirectory() as t:
    root = Path(t); seed_old(root, {"prefs.json": "viejo", "cred-abc.bin": "viejo"})
    new = root / "TroveAccountsHub"; new.mkdir()
    (new / "cred-abc.bin").write_text("nuevo")
    p = fresh(root)
    d = p.app_data_dir()
    check("adopta lo que falta", (d / "prefs.json").read_text() == "viejo")
    check("no pisa lo que ya había", (d / "cred-abc.bin").read_text() == "nuevo")

# 6) carpeta vieja sin prefs.json: no es una instalación, no se adopta
with tempfile.TemporaryDirectory() as t:
    root = Path(t); seed_old(root, {"macaddr.txt": "aa"})
    p = fresh(root)
    d = p.app_data_dir()
    check("ignora una carpeta vieja sin prefs", not (d / "macaddr.txt").exists())

# 7) todas las rutas derivadas cuelgan de la nueva
with tempfile.TemporaryDirectory() as t:
    root = Path(t); p = fresh(root)
    check("prefs_path bajo la nueva", p.prefs_path().parent.name == "TroveAccountsHub")
    check("macaddr_path bajo la nueva", p.macaddr_path().parent.name == "TroveAccountsHub")

print("\n" + ("TODO OK" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
