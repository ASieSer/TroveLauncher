# Trove Accounts Hub

A launcher for Trove that keeps the game up to date against Trion's CDN, signs
in with your Glyph credentials and starts the game - without the Glyph client.
Built to make switching between your own accounts less tedious - it remembers
them, so you are not retyping an email and password every time.

**Windows and Linux.** On Linux the game launches inside its Wine or Proton
prefix (see [Linux](#linux)).

> Ports code from [BetterTroveTools](https://github.com/AallynReed/BetterTroveTools)
> (MIT). See [`NOTICE.md`](NOTICE.md).

---

## Getting it

### Windows

Download `TroveAccountsHub.exe` and run it. That is the whole installation:
one file, no Python, no dependencies, nothing written to Program Files.

The interface is drawn by the **Microsoft Edge WebView2 runtime**. Windows 11
includes it and Windows 10 gets it with Edge, so it is almost certainly already
on your machine. If it is not, the app says so on start-up and links to the
[Evergreen Runtime](https://developer.microsoft.com/microsoft-edge/webview2/).
Before v0.2.2 it did not: without the runtime the window opened empty and
dark, with no message anywhere.

**If the window opens empty and dark**, the engine did not start. From v0.2.2
the app checks on start-up that the runtime is not only registered but actually
on disk - a half-finished WebView2 update leaves the registry saying it is
installed while the files are gone, and that is enough to produce an empty
window. Every
start-up writes `%APPDATA%\TroveAccountsHub\startup.log`, and its first
lines say which engine was used and what went wrong with it; from v0.2.2 the
app also says so itself if the interface has not loaded after 25 seconds.
The usual causes are no room left on the disk, a `TEMP` folder it cannot
write to, or an antivirus stopping WebView2.

> **Windows will warn you the first time.** The executable is not code-signed -
> a certificate costs a few hundred euros a year and this is a free tool - so
> SmartScreen shows *"Windows protected your PC"*. The button that runs it
> anyway is behind **More info** → **Run anyway**.
>
> If you would rather not take that on trust, don't: the whole thing is here,
> `build.bat` produces the same executable on your own machine, and every
> release lists the SHA-256 of the file it ships so you can check what you
> downloaded is what was built.

### From source

```bash
pip install -r requirements.txt
python main.py
```

Python 3.10 or newer. `--debug` opens WebView2's DevTools with F12.

To build the executable yourself rather than download it, see
[Building the executable](#building-the-executable).

---

## Where your data lives

**There is no server. This project does not have one.** No account of yours is
registered anywhere, nothing is uploaded, and there is no telemetry, analytics
or crash reporting. Nobody involved in this project can see your accounts,
because there is nowhere for them to be seen.

Everything the application knows lives in one folder on your own machine:

| | |
| --- | --- |
| **Windows** | `%APPDATA%\TroveAccountsHub` |
| **Linux** | `$XDG_DATA_HOME/TroveAccountsHub`, or `~/.local/share/TroveAccountsHub` |

What is in it:

| File | What it holds |
| --- | --- |
| `prefs.json` | Your accounts, groups, display names and settings. Plain JSON - open it and read it. |
| `prefs.json.bak` | The previous copy, so a power cut cannot lose your accounts. |
| `cred-<hash>.bin` | One saved password, encrypted. |
| `auth-<hash>.bin` | One Trion session ticket, encrypted. |
| `update-<branch>-<hash>.sqlite` | Which game files are on disk, so updates only download what changed. |
| `macaddr.txt` | A device id - see below. |
| `mod-backups/` | The mod file each update replaced, kept so an update can be put back. |
| `logo.<ext>` | Your own mark for the top bar, if you set one. A copy, like the background below. |
| `wallpaper.<ext>` | Your background image, if you chose one. A copy of the file you picked, so it keeps working when the original moves. Delete it and the background goes. |
| `recent/` | The last few logos and backgrounds you picked, so you can go back to one without finding the file again. Six of each at most, and no more than 72 MB of each kind - the oldest go first. Delete the folder and you only lose the shortcut. |
| `startup.log` | What happened the last time the window opened. Overwritten on every start. |

The `<hash>` in those filenames is just a short digest of the email, used to
keep several accounts apart. It is a filename, not a security measure.

### Passwords and tickets are never stored in the clear

A password, and a Trion ticket (a live session credential, good for about 48
hours), go to the operating system's own secret store - never into a readable
file:

| | Where | What protects it |
| --- | --- | --- |
| Windows | `cred-*.bin`, `auth-*.bin` in the folder above | **DPAPI**, user scope: only your Windows user, on this machine, can decrypt them |
| Linux | Your desktop keyring (GNOME Keyring, KWallet…) | Your login session - the files never touch our folder at all |
| Neither available | Nowhere | - |

**If there is nowhere safe to put them, they are not saved.** With no DPAPI and
no keyring, the password is simply not remembered and the ticket stays in
memory until you close the app. That is more annoying and it is the right
behaviour: an earlier version fell back to plaintext, and that is exactly what
must not happen to a live credential.

Saving the password is optional - turn *Remember passwords* off in Settings and
nothing is written at all. Your accounts stay in the list either way; the
setting only covers the password.

### `macaddr.txt` is not your MAC address

Trion's sign-in expects a device identifier field. Rather than read your real
network hardware address, the app **generates six random bytes on first run**
and reuses them so the value stays stable between sessions
(`secrets.token_bytes(6)` in `core/trionauth.py`). Delete the file and you get
a new random one. Nothing about your hardware is read or sent.

### The only three places anything is sent

| Host | What for | When |
| --- | --- | --- |
| `auth.trionworlds.com` | Signing in. Your Glyph email and password go here, over HTTPS, and come back as a ticket. This is Trion's own server - the same one the official Glyph client uses. There is no other way to log into Trove. | Launching or testing an account |
| `trove-update.dyn.triongames.com` | Trion's update CDN: the game's own files. No credentials are involved. | Updating or repairing an installation |
| `api.aallyn.net` | Mods Hub. Asked whether your installed mods have newer versions - what is sent is a list of SHA-256 hashes of the `.tmod` files in your mods folder, not their names, not their paths, and nothing about you. Also where an updated mod is downloaded from, and every download is checked against the checksum published beside it before it replaces anything. | Only when you press **Check for updates** or **Update** on a mod |

That is the complete list. The interface makes no network requests of its own -
fonts, icons and styles all ship inside the app - and nothing is contacted on
start-up. The mod check in particular never runs by itself: there is no timer
and nothing happens when the Mods pane opens.

**Check it yourself.** Every URL in the source, in one command:

```bash
grep -rnoE "https?://[^\"' )]+" --include=*.py --include=*.js --include=*.html .
```

You will find those three, plus `w3.org` - the SVG XML namespace, which is an
identifier and not an address - the project's own GitHub address, which the
update check sends as its user agent, the Microsoft link that gets printed in
the WebView2 error message, and `127.0.0.1` in a comment - the local address the
window serves its own interface from, which never leaves the machine. Nothing
else.

### Removing everything

Delete the folder listed above and nothing of yours remains - the executable
keeps nothing elsewhere and touches no registry keys of its own. On Linux, also
remove the keyring entries under the service name *Trove Accounts Hub*.

---

## Using it

Add an account with its Glyph email, give it a display name, drop it into a
group. Each card carries its own buttons:

| | |
| --- | --- |
| **Delete** | Removes the account, its saved password and its cached ticket. |
| **Edit** | Display name and its colour, server, group, password, and a flag for accounts you want struck through. |
| **Test login** | Signs in against Trion without launching, so you know a password works before you need it. |
| **Launch** | Starts the game. Turns into **Stop** while it runs. |

Each account carries its own region (NA / EU / PTS), so a PTS account and a
live one can sit side by side. NA and EU share the Live install; PTS needs its
own folder.

Each group's header wears the colour you gave it, so the board tells you at a
glance where one group ends and the next starts. The cards themselves stay
unframed: boxing those as well just buries what you actually read.

Account status lives on the card and nowhere else - a group header does not
summarise it, because "2 running" there says nothing about *which* two and
gives you nothing to press.

### What it does not do

It launches accounts **one at a time** and does nothing once the game is
running: no input automation, no macros, no bots, and nothing that keeps an
account signed in on its own. It is a wrapper around signing in and pressing
play - the same steps you would take in Glyph by hand - not a way to run
accounts unattended or play several at once.

### Appearance

One accent colour tints every surface at a low percentage - the whole window
shares a colour temperature rather than wearing a colour. Pick from the palette
or save your own; the tint slider controls how much of it comes through.

**Club themes** (Mystic Cave, Arsyn, Sayro) swap the top-bar mark for the club's
logo and pin the accent to the club's colour. The tint stays yours - that is
intensity, not colour.

Status colours never follow the accent: green running, cyan verified, amber
working, red failed, grey not known yet. A state cannot depend on what colour
you picked today.

On Windows the icon in the title bar and on the taskbar follows the accent too.
On Linux it stays the default green cube: Wayland has no protocol for a window
to set its own icon, so the icon comes from the `.desktop` entry.

Settings also carries a **Log** of everything the launcher does - the same lines
it would print to a console, which the packaged build does not have. It keeps
the last 500 and has a Clear button.

---

## Linux

The game is a Windows one, so it lives inside a Wine or Proton prefix and is
launched from there. Two things are needed from the system, because neither
comes from pip:

```bash
sudo apt install python3-gi gir1.2-webkit2-4.1   # the window
sudo apt install wine64                          # launching the game
```

Then:

```bash
tools/install_linux.sh
```

That checks what is missing, sets up an environment, and puts the app in your
applications menu. It creates its virtualenv with `--system-site-packages`
because `gi` is a system package that a plain venv cannot see - the single most
common reason the app starts and then cannot open a window.

`python tools/check_linux.py` reports what this machine has: window engine,
Wine, the helper, the keyring and the installs it can find. Paste its output if
something will not start.

> **Proton is better than system Wine.** If the game is inside a Proton prefix,
> the app launches it with that prefix's own Proton without being told. System
> Wine often lacks `WSCEnumProtocols32`, which Trove's anti-cheat loader
> imports: without it the loader dies with a "procedure entry point" dialog and
> the game never opens. Settings → Wine lists the Protons it found.

### How the ticket gets in

On Windows the app hands the game its ticket directly. On Linux it cannot: the
game collects the ticket from a Windows file mapping by calling
`OpenProcess(launcher pid)` + `DuplicateHandle`, and those are Windows kernel
objects that a native Linux process can neither create nor share.

So a small Win32 helper (`native/troveinject.c`, shipped pre-built) runs inside
the prefix and plays launcher for every account. It stays alive while any game
is open, because the game keeps duplicating those handles from it.

---

## How it works

### Core (`core/`)

| Module | Responsibility |
| --- | --- |
| `cdn.py` | Update CDN client: pointer (current version) → manifest (`path:hash:size`) → files. |
| `updater.py` | Incremental sync with its state in SQLite. Never deletes files. |
| `trionauth.py` | Sign-in against Trion, 2FA by email, keep-alive, cached ticket. |
| `inject.py` | Hands the ticket to the game the way Glyph does: an RC4-encrypted RIFT blob in an inheritable file mapping. |
| `rift.py` | That blob's format. Pure code, shared with the Wine helper. |
| `launch.py` | The per-region auth-server string. |
| `gamehost.py` | Launch, wait, close: Windows directly, or through Wine. |
| `winehost.py` | The Linux end - picks the runner and drives the helper. |
| `winicon.py` | The live window icon (Windows). |
| `vault.py` | Where secrets go: DPAPI, or the desktop keyring. |
| `installs.py` | Finding installs: registry, Steam, prefixes, custom folders. |
| `prefs.py` | Preferences, accounts and groups, saved atomically. |
| `paths.py` | Where everything on disk lives. |
| `service.py` | Orchestrator: worker thread, 2FA, progress. |

### Interface (`web/`)

Plain scripts on a shared `window.App` namespace - not ES modules, because the
page loads over `file://` and Chromium refuses module scripts from that origin.
`core.js` creates the namespace, `app.js` starts everything.

| | |
| --- | --- |
| `core.js` | Shared state, the Python bridge, DOM helpers, the status line. |
| `icons.js` | Inline SVG icons, so they inherit `currentColor`. |
| `theme.js` | Accent, club themes, fonts, contrast, starfield, window icon. |
| `board.js` | The board: groups, account cards, region menu. |
| `dragdrop.js` | Dragging accounts and groups. |
| `modals.js` | Dialogs. |
| `actions.js` | Launch, test login, stop. |
| `settings.js` | The settings drawer and the install chips. |
| `app.js` | Backend events, wiring, start-up. |

Everything the launcher logs also appears in the log box under Settings, which
matters because the packaged build has no console.

### Things worth not breaking

- **The anti-cheat loader takes the game's name as `argv[1]`**, not `argv[0]`.
  Get that wrong and it aborts with exit code 1038 without launching anything.
- **The ticket's handles are never closed.** The game duplicates them from the
  launcher for the whole session; it is the same deliberate two-handle leak per
  launch that Glyph makes.
- **`prefs.json` is written atomically** - temp file, fsync, replace, with a
  `.bak` kept. Writing over it directly truncates it first, and a power cut in
  that instant loses every account.
- **The DPAPI entropy still reads `TroveLauncher.credentials.v1`.** It is an
  identifier, not a visible name: changing it would make already-saved passwords
  unreadable.

### The previous data folder is adopted, not abandoned

The app used to be called Trove Launcher and stored in
`%APPDATA%\TroveLauncher`. On first run under the new name, if the new folder
has no `prefs.json` and the old one does, the contents are **copied** - not
moved, so going back to an older version still finds its data - and
`adopted-from.txt` records that it happened. Nothing already present is ever
overwritten.

---

## Building the executable

Double-click [`build.bat`](build.bat), or run it from a terminal:

```
build.bat
```

It checks for Python, installs PyInstaller and the app's dependencies if they
are not there, and leaves `dist\TroveAccountsHub.exe` - one self-contained file
of about 18 MB that starts in under two seconds and needs nothing installed on
the machine it runs on.

If you would rather drive it yourself:

```bash
pip install -r requirements.txt pyinstaller
pyinstaller TroveAccountsHub.spec
```

The recipe and the reasoning behind it are in
[`TroveAccountsHub.spec`](TroveAccountsHub.spec).

On Linux the same spec works, but read the note at the top of
[`tools/install_linux.sh`](tools/install_linux.sh) first - a frozen build there
means pulling in Qt WebEngine and a ~400 MB binary, which is why the supported
route is that script instead.

### Regenerating what ships pre-built

None of these is needed to use or build the app; the outputs are versioned.

- `tools/make_icon.py` - the executable's icon, `web/img/app.ico` and
  `app.png`, drawn from the brand cube (`pip install pillow`).
- `tools/make_logo.py` - the brand SVG, with the wordmark traced to curves so it
  depends on no font at paint time (`pip install fonttools brotli`).
- `tools/build_helper.sh` - the Win32 helper the Linux launch path drives inside
  the Wine prefix (needs mingw-w64). Only if you change the C.

---

## Status

Complete and in use. The interface still moves - colours, density, labels - but
what is underneath does not.
