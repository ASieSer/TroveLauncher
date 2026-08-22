# Attribution

This project ports and adapts code from **BetterTroveTools**, by Aallyn Reed,
released under the MIT licence:

- Repository: https://github.com/AallynReed/BetterTroveTools
- Copyright (c) 2026-Present Aallyn Reed
- Full licence text: [`LICENSE-BetterTroveTools`](LICENSE-BetterTroveTools)

## What was ported

Five core modules, taken from `backend/trove_launcher/` and adapted to this
project's `core/` package:

| Local file | Origin | Changes |
| --- | --- | --- |
| `core/cdn.py` | `backend/trove_launcher/cdn.py` | No functional changes. |
| `core/updater.py` | `backend/trove_launcher/updater.py` | No functional changes. |
| `core/trionauth.py` | `backend/trove_launcher/trionauth.py` | The ticket cache now goes to the system secret store (`core/vault.py`) instead of a file of its own. |
| `core/inject.py` | `backend/trove_launcher/inject.py` | The RIFT blob was split out into `core/rift.py` so the Wine helper can mirror it; `resolve_game_pid` was added to find the game behind the anti-cheat loader. |
| `core/launch.py` | `backend/trove_launcher/launch.py` | Only the per-region auth-server strings were kept. |

That code was in turn vendored by BetterTroveTools from the TroveImposter
project, as its own documentation states.

The PE-header executable validation in `core/installs.py` is based on
`utils/executable.py` from the same project.

## What is original to this repository

Everything else, in particular:

- `core/service.py` — the orchestrator: worker thread, 2FA, per-account launch
  queueing and auto-relog.
- `core/paths.py`, `core/prefs.py` — where data lives and how it is saved.
- `core/vault.py` — the system secret store, DPAPI or desktop keyring.
- `core/rift.py` — the ticket blob's format, isolated so both launch paths share it.
- `core/installs.py` — finding installations across registry, Steam and prefixes.
- `core/gamehost.py`, `core/winehost.py`, `native/troveinject.c` — the Linux
  launch path: a Win32 helper inside the Wine prefix, and the Python end that
  drives it.
- `core/winicon.py` — the live window icon.
- `api.py`, `main.py`, and everything under `web/` and `tools/`.
