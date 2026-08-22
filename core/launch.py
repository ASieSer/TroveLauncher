"""The per-region auth-server `-C` string handed to the game.

Trimmed from BetterTroveTools' TroveImposter/utils/trove_launch.py — the mod /
Trove.cfg merging that lived alongside it is deliberately left out, since this
launcher does not manage mods. The ticket -> running-game glue lives in
``service.py``, which mints via ``trionauth`` and spawns via ``gamehost``.

Nothing here touches other processes' windows: the launcher never moves,
restores or foregrounds the game.
"""

from __future__ import annotations

# Verified EU string from GlyphClient.3.log; NA/PTS from the Trion auth guide.
AUTH_SERVERS = {
    "EU": ("[AuthServer] Address = "
           "ams-c12-b01.ams.triongames.com:6560|ams-c12-b02.ams.triongames.com:6560|"
           "ams-c12-b03.ams.triongames.com:6560|ams-c12-b04.ams.triongames.com:6560|"
           "ams-c12-b05.ams.triongames.com:6560"),
    "NA": ("[AuthServer] Address = "
           "dal-c35-b05.dal.triongames.com:6560|dal-c35-b06.dal.triongames.com:6560|"
           "dal-c35-b07.dal.triongames.com:6560|dal-c35-b08.dal.triongames.com:6560|"
           "dal-c35-b09.dal.triongames.com:6560"),
    "PTS": ("[AuthServer] Address = "
            "auth-pcpts01.trovegame.com:6560|auth-pcpts02.trovegame.com:6560"),
}


def get_auth_server(region: str) -> str:
    try:
        return AUTH_SERVERS[region.upper()]
    except KeyError:
        raise ValueError(f"unknown region {region!r}; choose {list(AUTH_SERVERS)}")

