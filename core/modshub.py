"""Asking Mods Hub whether the installed mods have newer versions.

This is the only part of the launcher that talks to a mod site, and it only
does so when the user presses the button - nothing here runs on its own.

What leaves the machine is a list of SHA-256 hashes of `.tmod` files, which is
how the API identifies a mod: no account of yours, no file names, no paths.
What comes back is the mod's page and its published releases.

    POST https://api.aallyn.net/v1/mods/lookup
    {"hashes": [...], "releases": "latest"}
    -> {"results": {<hash>: {"mod": {...}, "release": {...}}}, "unknown": [...]}

No token is needed for this; the API allows it anonymously.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

LOOKUP_URL = "https://api.aallyn.net/v1/mods/lookup"

# The API's own ceiling per request. Seventy mods fit in one trip.
MAX_HASHES = 200

TIMEOUT = 20

USER_AGENT = "TroveAccountsHub (+https://github.com/ASieSer/TroveLauncher)"


def file_hash(path: Path) -> str:
    """The SHA-256 of a file, which is what the API looks a mod up by."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _latest_on_branch(mod: dict, branch: str) -> dict | None:
    """The current build of the branch the installed copy came from.

    Branch matters: a mod can publish a beta alongside its stable line, and
    someone on stable is not behind because a beta exists.
    """
    for release in mod.get("releases") or []:
        if release.get("branch") == branch:
            return release
    return None


def compare(found: dict) -> dict:
    """What one lookup result says about the copy on disk.

    The comparison is by release TAG and not by hash. A release that gets
    repacked - to carry a config, say - answers to every hash it ever had, so
    an older file can legitimately resolve to the current release with a
    different hash. Comparing hashes reported one of the mods here as out of
    date against itself.
    """
    mod = found.get("mod") or {}
    installed = found.get("release") or {}
    latest = _latest_on_branch(mod, installed.get("branch"))
    behind = bool(latest and latest.get("tag") != installed.get("tag"))
    return {
        "known": True,
        "title": mod.get("title", ""),
        "author": mod.get("author", ""),
        "page_url": mod.get("page_url", ""),
        "branch": installed.get("branch", ""),
        "installed_tag": installed.get("tag", ""),
        "latest_tag": (latest or {}).get("tag", ""),
        "published_at": (latest or {}).get("published_at", ""),
        "changelog": (latest or {}).get("changelog", ""),
        "download_url": (latest or {}).get("download_url", ""),
        "sha256": (latest or {}).get("sha256", ""),
        "size": (latest or {}).get("size", 0),
        "behind": behind,
    }


class NotWhatItSaid(Exception):
    """The bytes that arrived are not the release that was promised."""


def verify(data: bytes, expected_sha: str, expected_size=None) -> None:
    """Check a download against what the API said it would be.

    The hash is the whole point of downloading over the API rather than off a
    link: it is published beside the file, so the bytes can be proved to be the
    ones the mod's author released before anything is written to disk.
    """
    if expected_size and len(data) != int(expected_size):
        raise NotWhatItSaid(
            f"the file is {len(data)} bytes and {expected_size} were expected")
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha and digest != expected_sha:
        raise NotWhatItSaid(
            f"the checksum does not match: {digest[:16]}… instead of "
            f"{expected_sha[:16]}…")


def check(paths, post) -> dict:
    """Look every file up. Returns {file name: what we learned}.

    `post` is passed in rather than imported so that this can be exercised
    without going near the network.
    """
    by_hash: dict[str, str] = {}
    for path in paths:
        try:
            by_hash[file_hash(path)] = path.name
        except OSError:
            continue

    out = {name: {"known": False} for name in by_hash.values()}
    hashes = list(by_hash)
    for start in range(0, len(hashes), MAX_HASHES):
        batch = hashes[start:start + MAX_HASHES]
        answer = post(LOOKUP_URL, {"hashes": batch, "releases": "latest"})
        for digest, found in (answer.get("results") or {}).items():
            if digest in by_hash:
                out[by_hash[digest]] = compare(found)
    return out
