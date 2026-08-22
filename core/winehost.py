"""Talking to the Win32 helper that lives inside the Wine prefix.

On Linux the game runs under Wine, and the ticket can only be handed to it from
inside that same prefix: it is a Windows file mapping the game duplicates from
the process that launched it (see ``native/troveinject.c``). This module is the
other end of the wire - it starts ``troveinject.exe`` with ``wine``, sends it
commands and routes the replies.

One helper per session, not one per game: the ticket's handles have to stay open
for as long as a game is open, so the helper plays "launcher process" for every
account, the same way the application itself does on Windows.

The protocol is one line per message (see the .c). The interesting part here is
that replies arrive out of order: ``wait`` can take hours to answer and other
``spawn`` and ``list`` calls happen meanwhile. That is why each request carries a
number and waits on its own event, with a reader thread doing the routing.
"""

from __future__ import annotations

import base64
import collections
import functools
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .paths import base_dir

HELPER_NAME = "troveinject.exe"


class WineError(RuntimeError):
    pass


def helper_path() -> Path:
    """The helper executable, which ships alongside the code."""
    return base_dir() / "native" / HELPER_NAME


# The symbol Trove's anti-cheat loader imports from ws2_32 and that Wine does
# not always export. When it is missing the loader does not even start: a "The
# procedure entry point ... could not be located" box appears and the game never
# shows up. Proton does ship it, which is why it is preferred (see find_wine).
LOADER_SYMBOL = "WSCEnumProtocols32"


def _executable(candidate: str) -> str:
    """The path of an executable binary, searching PATH if need be."""
    if not candidate:
        return ""
    found = shutil.which(candidate)
    if found:
        return str(found)
    path = Path(candidate)
    return str(path) if path.is_file() and os.access(path, os.X_OK) else ""


def _version_key(name: str) -> tuple:
    """For sorting Proton by version: the numbers in the name, highest first."""
    return tuple(int(n) for n in re.findall(r"\d+", name)) or (0,)


# Hunting for runners touches the disk and the interface asks for status often,
# so it is remembered for a while. A freshly installed Proton takes that long to
# appear, which for something installed once a year is a fair price.
_RUNNER_TTL = 60.0
_runner_cache: tuple[float, list[dict]] = (0.0, [])


def find_proton_runners(fresh: bool = False) -> list[dict]:
    """The Protons installed on this machine, newest first.

    Each is ``{"name", "wine"}``. Both places they end up in are checked: the
    official Protons, inside the Steam libraries, and the community ones
    (GE-Proton and friends), in ``compatibilitytools.d``. The binary is at
    ``files/bin/wine`` since Proton 5.13 and at ``dist/bin/wine`` before that.
    """
    global _runner_cache
    from . import installs

    stamp, cached = _runner_cache
    if not fresh and time.monotonic() - stamp < _RUNNER_TTL:
        return cached

    roots: list[Path] = []
    for steam in installs._steam_roots():
        for library in [steam] + installs._steam_library_paths(steam):
            roots.append(library / "steamapps" / "common")
        roots.append(steam / "compatibilitytools.d")
    roots.append(Path.home() / ".steam" / "root" / "compatibilitytools.d")

    found: dict[str, dict] = {}
    for root in roots:
        try:
            children = sorted(root.iterdir())
        except OSError:
            continue
        for child in children:
            if root.name == "common" and not child.name.lower().startswith("proton"):
                continue
            wine = _proton_wine(child)
            if wine and wine not in found:
                found[wine] = {"name": child.name, "wine": wine}
    runners = sorted(found.values(),
                     key=lambda r: _version_key(r["name"]), reverse=True)
    _runner_cache = (time.monotonic(), runners)
    return runners


def _proton_wine(install: Path) -> str:
    """The wine binary inside a Proton folder, if there is one."""
    for relative in ("files/bin/wine", "dist/bin/wine",
                     "files/bin/wine64", "dist/bin/wine64"):
        candidate = install / relative
        if candidate.is_file():
            return str(candidate)
    return ""


def proton_for_prefix(prefix: str | os.PathLike) -> str:
    """The Proton that made that prefix, if the prefix says so.

    A Proton prefix is not just any Wine prefix: a particular runner created it,
    and Steam records which in ``config_info`` (paths inside that Proton) and in
    ``version`` (its name). Using that same runner avoids half the problems -
    starting with "wineserver version mismatch" - and brings back what the
    system Wine does not have.
    """
    compat = Path(prefix)
    if compat.name == "pfx":
        compat = compat.parent
    if compat.parent.name != "compatdata":
        return ""

    try:
        info = (compat / "config_info").read_text(encoding="utf-8", errors="replace")
    except OSError:
        info = ""
    for line in info.splitlines():
        for marker in ("/files/", "/dist/"):
            head, sep, _rest = line.partition(marker)
            if sep and head:
                wine = _proton_wine(Path(head))
                if wine:
                    return wine

    try:
        version = (compat / "version").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    wanted = version.split()[-1].lower() if version.split() else ""
    if not wanted:
        return ""
    for runner in find_proton_runners():
        name = runner["name"].lower()
        if wanted in name or name.replace(" ", "-") in wanted:
            return runner["wine"]
    return ""


def find_wine(preferred: str = "", prefix: str = "") -> str:
    """The Wine binary to use for that prefix. Empty when there is none.

    What the user says wins. Then the prefix's own runner: if the game lives
    inside a Proton prefix, the Wine that belongs to it is that Proton and not
    the system's - which, besides not having made the prefix, is the one that
    ends up without ``WSCEnumProtocols32`` and leaves the anti-cheat unable to
    start. Failing both, the system's; and failing that, any Proton lying around,
    which beats nothing.
    """
    for candidate in (preferred, os.environ.get("WINE", "")):
        found = _executable(candidate)
        if found:
            return found
    if prefix:
        proton = proton_for_prefix(prefix)
        if proton:
            return proton
    for candidate in ("wine", "wine64"):
        found = _executable(candidate)
        if found:
            return found
    runners = find_proton_runners()
    return runners[0]["wine"] if runners else ""


@functools.lru_cache(maxsize=8)
def missing_loader_symbol(wine: str) -> bool:
    """Is this Wine missing what Trove's anti-cheat is going to ask it for?

    ``ws2_32.dll`` is a Wine DLL, not a prefix one, so the answer lies in the
    runner itself. The symbol's name is searched for inside the file: in a PE the
    exported names sit there in plain text, and for what is needed here - warning
    before the user runs into an error dialog - that is enough. In doubt (we
    cannot find the DLL), it stays quiet.
    """
    dll = _ws2_32_of(wine)
    if not dll:
        return False
    try:
        return LOADER_SYMBOL.encode("ascii") not in dll.read_bytes()
    except OSError:
        return False


def _ws2_32_of(wine: str) -> Path | None:
    """The ws2_32.dll that wine binary would use."""
    if not wine:
        return None
    root = Path(wine).resolve().parent.parent      # …/bin/wine -> …
    for pattern in ("lib/wine/x86_64-windows/ws2_32.dll",
                    "lib64/wine/x86_64-windows/ws2_32.dll",
                    "lib/*/wine/x86_64-windows/ws2_32.dll"):
        for candidate in root.glob(pattern):
            if candidate.is_file():
                return candidate
    # System Wine: the binary is in /usr/bin and the DLLs hang off the
    # architecture folder, which varies by distribution.
    for candidate in root.glob("lib*/*/wine/x86_64-windows/ws2_32.dll"):
        if candidate.is_file():
            return candidate
    return None


def prefix_for(game_path: str | os.PathLike | None, preferred: str = "") -> str:
    """The Wine prefix that game lives in.

    What the user says is preferred. Otherwise it is worked out from the game's
    own path: a Proton installation hangs off ``.../compatdata/<appid>/pfx/``,
    and any installation under Wine hangs off ``<prefix>/drive_c/``. Working it
    out matters because the game only exists INSIDE a prefix: launching from a
    different one would not even find the drive it is on.
    """
    if preferred:
        return str(preferred)
    if game_path:
        parts = Path(game_path).resolve().parts
        for i in range(len(parts) - 1, -1, -1):
            if parts[i] == "drive_c":
                return str(Path(*parts[:i]))
    env = os.environ.get("WINEPREFIX", "")
    if env:
        return env
    return str(Path.home() / ".wine")


class WineHelper:
    """The helper, seen from Linux."""

    def __init__(self, *, wine: str, prefix: str, helper: Path | None = None,
                 log=print):
        self.wine = wine
        self.prefix = prefix
        self.helper = Path(helper) if helper else helper_path()
        self._log = log
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._next_id = 1
        self._pending: dict[int, dict] = {}
        self._reader: threading.Thread | None = None
        self._path_cache: dict[str, str] = {}
        # The last thing Wine said on stderr. When the helper dies, the reason
        # is THERE and nowhere else: "wineserver: version mismatch", "wine: could
        # not load...", a prefix from another runner. Without this all that was
        # left was a bare "the helper died", which helps nobody.
        self._stderr: collections.deque[str] = collections.deque(maxlen=25)
        self._errthread: threading.Thread | None = None

    # --- lifecycle ----------------------------------------------------

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def env(self) -> dict:
        env = dict(os.environ)
        env["WINEPREFIX"] = self.prefix
        # The helper is a console program and paints nothing; without this Wine
        # complains on headless machines and clutters the log with noise.
        env.setdefault("WINEDEBUG", "-all")
        return env

    def start(self) -> None:
        with self._lock:
            if self.alive:
                return
            if not self.wine:
                raise WineError("Wine was not found. Install it, or point at it "
                                "in Settings.")
            if not self.helper.is_file():
                raise WineError(f"the helper {self.helper} is missing. Build it with "
                                f"tools/build_helper.sh.")
            self._stderr.clear()
            try:
                self._proc = subprocess.Popen(
                    [self.wine, str(self.helper)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, env=self.env(), bufsize=1,
                    text=True, encoding="utf-8", errors="replace")
            except OSError as exc:
                raise WineError(f"could not start Wine: {exc}") from exc
            self._reader = threading.Thread(target=self._read_loop, daemon=True,
                                            name="wine-helper")
            self._reader.start()
            self._errthread = threading.Thread(target=self._drain_stderr,
                                               daemon=True, name="wine-helper-err")
            self._errthread.start()
        # Make it answer before calling the start-up good: a broken prefix fails
        # here rather than halfway through a launch.
        try:
            self.call("ping", timeout=60)
        except WineError as exc:
            raise WineError(self._explain(str(exc))) from exc

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if not proc:
            return
        try:
            if proc.poll() is None and proc.stdin:
                proc.stdin.write("0 quit\n")
                proc.stdin.flush()
                proc.wait(timeout=5)
        except Exception:
            pass
        if proc.poll() is None:
            proc.kill()
        # Nobody is going to answer now: wake whoever was waiting.
        with self._lock:
            for slot in self._pending.values():
                slot["error"] = "the Wine helper was shut down"
                slot["event"].set()
            self._pending.clear()

    # --- transport -------------------------------------------------------

    def _drain_stderr(self) -> None:
        """Keeps what Wine spits out on stderr, without dumping it to the log.

        Wine is chatty even when all is well, so this is not shown unless
        something fails; then it is the only thing that explains the failure.
        """
        proc = self._proc
        if not proc or not proc.stderr:
            return
        for raw in proc.stderr:
            text = raw.rstrip()
            if text:
                self._stderr.append(text)

    def _explain(self, problem: str) -> str:
        """The error, with what Wine said and a hint where one can be given.

        Idempotent: the reader explains as soon as the process dies, and start-up
        comes back through here with that same text. Without this guard the user
        read Wine's complaint twice in a row.
        """
        if "wine said:" in problem or "wine exited with code" in problem:
            return problem
        # Wait for the stderr reader: without this the failure was explained
        # BEFORE Wine finished saying it, giving a bare "it died".
        if self._errthread and self._errthread.is_alive():
            self._errthread.join(timeout=5)
        parts = [problem]
        proc = self._proc
        code = proc.poll() if proc else None
        if code is not None:
            parts.append(f"wine exited with code {code}")
        tail = [t for t in self._stderr][-6:]
        if tail:
            parts.append("wine said:\n  " + "\n  ".join(tail))
        hint = self._hint(" ".join(self._stderr).lower())
        if hint:
            parts.append(hint)
        return " — ".join(parts[:2]) + ("\n" + "\n".join(parts[2:]) if parts[2:] else "")

    def _hint(self, said: str) -> str:
        """Hints for the failures that have a known name."""
        if "version mismatch" in said or "wineserver" in said:
            return ("Hint: a wineserver from a DIFFERENT Wine build is already "
                    "running for this prefix. Close the app that opened it "
                    "(Bottles, Lutris, Steam) or run "
                    f"`WINEPREFIX={self.prefix} wineserver -k`, then try again.")
        if "/bottles/" in str(self.prefix).lower():
            return ("Hint: this prefix was made by Bottles, which uses its own "
                    "Wine build. Point Settings → Wine at the same runner "
                    "Bottles uses for this bottle, or launch it from Bottles "
                    "once so its runner updates the prefix.")
        if "compatdata" in str(self.prefix).lower():
            return ("Hint: this is a Proton prefix. Point Settings → Wine at "
                    "that Proton's own binary (…/dist/bin/wine or "
                    "…/files/bin/wine).")
        return ""

    def _read_loop(self) -> None:
        proc = self._proc
        assert proc and proc.stdout
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            head, _, rest = line.partition(" ")
            if head == "log":
                self._log(_decode(rest) or rest)
                continue
            try:
                msg_id = int(head)
            except ValueError:
                self._log(f"[wine] unexpected line: {line[:120]}")
                continue
            status, _, payload = rest.partition(" ")
            with self._lock:
                slot = self._pending.pop(msg_id, None)
            if not slot:
                continue
            if status == "ok":
                slot["result"] = payload.split(" ") if payload else []
            else:
                slot["error"] = _decode(payload) or "unknown error"
            slot["event"].set()
        # stdout closed: the helper died. Wine is given a moment to finish
        # writing its complaint before we report it.
        if self._proc:
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        message = self._explain("the Wine helper died")
        with self._lock:
            for slot in self._pending.values():
                slot["error"] = message
                slot["event"].set()
            self._pending.clear()

    def call(self, cmd: str, *args, timeout: float | None = 120) -> list[str]:
        """Sends a command and waits for ITS reply.

        ``timeout=None`` for the ones that take as long as they take (``wait``).
        Blocking here blocks nobody else: the helper serves each wait on its own
        thread and the reader routes by request number.
        """
        proc = self._proc
        if not proc or proc.poll() is not None:
            raise WineError("the Wine helper is not running")
        slot = {"event": threading.Event(), "result": None, "error": None}
        with self._lock:
            msg_id = self._next_id
            self._next_id += 1
            self._pending[msg_id] = slot
        line = " ".join([str(msg_id), cmd, *(str(a) for a in args)])
        try:
            assert proc.stdin
            proc.stdin.write(line + "\n")
            proc.stdin.flush()
        except OSError as exc:
            with self._lock:
                self._pending.pop(msg_id, None)
            raise WineError(f"could not talk to the helper: {exc}") from exc
        if not slot["event"].wait(timeout):
            with self._lock:
                self._pending.pop(msg_id, None)
            raise WineError(f"the helper did not answer {cmd!r}")
        if slot["error"]:
            raise WineError(slot["error"])
        return slot["result"] or []

    # --- rutas ------------------------------------------------------------

    def to_windows_path(self, path: str | os.PathLike) -> str:
        """A Linux path -> a path the helper can open inside the prefix."""
        key = str(path)
        cached = self._path_cache.get(key)
        if cached:
            return cached
        try:
            out = subprocess.run([self.wine, "winepath", "-w", key],
                                 capture_output=True, text=True, env=self.env(),
                                 timeout=60)
            win = out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            win = ""
        if not win:
            # Fallback: Wine maps the system root at Z:. It keeps a winepath
            # failure from blocking a launch.
            win = "Z:" + key.replace("/", "\\")
        self._path_cache[key] = win
        return win

    # --- commands ----------------------------------------------------------

    def spawn(self, exe: str | os.PathLike, ticket: str, auth_server: str, *,
              parent_process_name: str = "", wait_ms: int = 30000,
              exclude: set[int] | None = None) -> dict:
        """``exclude``: sessions we already watch, so the helper does not
        confuse them with the one it has just opened (see resolve_game_pid in
        the .c)."""
        res = self.call("spawn",
                        _encode(self.to_windows_path(exe)),
                        _encode(ticket),
                        _encode(auth_server),
                        _encode(parent_process_name or ""),
                        wait_ms,
                        ",".join(str(int(p)) for p in sorted(exclude or [])) or "0",
                        timeout=max(120.0, wait_ms / 1000 + 90))
        if not res:
            raise WineError("the helper did not return the game pid")
        return {"pid": int(res[0]),
                "consumed": len(res) > 1 and res[1] == "1",
                "via_loader": len(res) > 2 and res[2] == "1"}

    def wait_for_exit(self, pid: int) -> int | None:
        res = self.call("wait", int(pid), timeout=None)
        if not res:
            return None
        code = int(res[0])
        return None if code == -1 else code

    def wait_until_ready(self, pid: int, timeout: float = 120.0) -> int:
        """Waits for that session to finish starting, INSIDE the prefix.

        Returns whatever ``WaitForInputIdle`` returns there: 0 if it is already
        up, 0x102 on timeout, and -1 if the process could not even be opened.
        Like ``wait``, it is served on a helper thread, so waiting here does not
        leave everyone else without a line.
        """
        res = self.call("ready", int(pid), int(timeout * 1000),
                        timeout=timeout + 30)
        return int(res[0]) if res else -1

    def terminate(self, pid: int) -> bool:
        try:
            self.call("kill", int(pid), timeout=30)
            return True
        except WineError:
            return False

    def list_processes(self) -> list[tuple[int, int, str]]:
        out = []
        for item in self.call("list", timeout=60):
            pid, _, rest = item.partition(",")
            ppid, _, name64 = rest.partition(",")
            try:
                out.append((int(pid), int(ppid), _decode(name64) or ""))
            except ValueError:
                continue
        return out


def _encode(text: str) -> str:
    return base64.b64encode((text or "").encode("utf-8")).decode("ascii") or "="


def _decode(text: str) -> str:
    try:
        return base64.b64decode(text).decode("utf-8", "replace")
    except Exception:
        return ""
