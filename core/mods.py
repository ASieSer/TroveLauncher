"""Reading the mods folder of an installation, and turning mods on and off.

A `.tmod` is a small archive with its own header, and everything shown about a
mod is read straight out of it - nothing here talks to the network or to any
mod site. The whole layout:

    uint64     where the header ends, and where the payload starts
    uint16     format version
    uint16     how many properties follow
    properties key and value, each a LEB128 length followed by that many bytes
    file table until the header ends: a length BYTE and the path, then four
               LEB128 numbers - index, offset, size, checksum
    payload    one zlib stream; a file is its `size` bytes at its `offset`
               inside the decompressed whole

`title`, `author`, `notes`, `tags`, `modVersion` and `previewPath` are
properties, and the preview is simply the file the table lists under that path.

This used to be read by hunting for image signatures instead, because the
offsets were thought to be unguessable. It worked for most mods and quietly
failed for others: a preview whose bytes did not begin near the front of the
archive was never found, and one mod that declares a preview it never packed
showed a slab of texture that happened to start with a PNG signature. Both are
gone now - what is read is what the table says is there.

Two details that are not obvious and that mods in the wild depend on:

  * lengths in the file table are single bytes, but property lengths are
    LEB128: notes run well past 127 bytes and stop the header dead if read as
    one byte;
  * a build of the packing tool wrote property lengths in CHARACTERS while
    writing the bytes as UTF-8, so one accented letter in a title shifts
    everything after it. Such a file is recognised by the file table not
    landing exactly on the end of the header, and read again the other way.

A mod can be off in two different ways, and both have to be read to know what
the game will actually load:

  * its file renamed to `<name>.tmod.disabled`, which the loader skips;
  * its name listed in `DisabledMods` in the game's own `Trove.cfg` - the one
    under `%APPDATA%/Trove`, not the one beside the executable - which is what
    Trove's in-game mod menu writes.

Turning one off from here goes through the cfg, because that is the game's own
mechanism and it survives the mod file being replaced by a newer version. A
file someone renamed by hand is still honoured, and turning it back on undoes
both.
"""

from __future__ import annotations

import base64
import itertools
import zlib
from pathlib import Path

# A first read, big enough that the header of almost every mod arrives whole.
# Its real length is the first eight bytes, so anything longer is read again at
# its true size - one mod here has a 284 KB header, and a listing that stopped
# at a fixed ceiling simply lost the end of it.
HEADER_BYTES = 64 * 1024

# A header longer than this is not a header. Nothing legitimate comes close;
# this only stops a corrupt length from asking for a gigabyte.
MAX_HEADER = 8 * 1024 * 1024

DISABLED = ".disabled"

# The ceiling on one decompressed image. A preview is a screenshot, not a film;
# this is only here so a malformed stream cannot ask for all the memory there is.
MAX_IMAGE = 24 * 1024 * 1024

# What the interface is told about, in the order it is looked for.
_WANTED = ("title", "author", "notes", "tags", "modVersion", "previewPath")


def mods_dir(game_path: str | Path) -> Path | None:
    """The mods folder of an installation, if the installation is there."""
    if not game_path:
        return None
    folder = Path(game_path) / "mods"
    return folder if folder.is_dir() else None


def game_config(trove_appdata: Path) -> Path:
    """The config the game keeps its own settings in."""
    return Path(trove_appdata) / "Trove.cfg"


def _split_disabled(line: str) -> list[str]:
    return [name for name in line.split("|") if name]


def disabled_in_config(config: Path) -> tuple[set[str], bool]:
    """The mods the game has switched off, and whether ALL of them are.

    Returns an empty set when the file is not there: a machine that has never
    opened the mod menu has no list, and that is not an error.
    """
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set(), False

    names: set[str] = set()
    everything = False
    in_mods = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            in_mods = line.lower() == "[mods]"
            continue
        if not in_mods or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip().lower(), value.strip()
        if key == "disabledmods":
            names = set(_split_disabled(value))
        elif key == "disableallmods":
            everything = value.lower() in ("true", "1", "yes")
    return names, everything


def write_disabled(config: Path, names) -> None:
    """Put `names` in the [Mods] section, leaving the rest of the file alone.

    The line is rewritten in place rather than the file rebuilt: this belongs to
    the game, it holds every graphics and key-binding setting the user has, and
    the only thing we have any business touching is one list.
    """
    joined = "|".join(sorted(names))
    text = config.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)

    in_mods = False
    at = None
    section_end = None
    for i, raw in enumerate(lines):
        line = raw.strip()
        if line.startswith("["):
            if in_mods and section_end is None:
                section_end = i
            in_mods = line.lower() == "[mods]"
            continue
        if in_mods and line.lower().startswith("disabledmods"):
            at = i
    ending = "\r\n" if text.count("\r\n") > text.count("\n") // 2 else "\n"

    if at is not None:
        lines[at] = f"DisabledMods = {joined}{ending}"
    elif section_end is not None:
        lines.insert(section_end, f"DisabledMods = {joined}{ending}")
    else:
        lines.append(f"{ending}[Mods]{ending}DisabledMods = {joined}{ending}")

    # Written beside the original and moved into place, so a crash halfway
    # cannot leave the game without a config.
    temporary = config.with_suffix(".cfg.tah-tmp")
    temporary.write_text("".join(lines), encoding="utf-8")
    temporary.replace(config)


def _leb128(data: bytes, i: int) -> tuple[int, int]:
    """One variable-length number, and where it ends."""
    out = shift = 0
    while True:
        byte = data[i]
        out |= (byte & 0x7F) << shift
        i += 1
        if not byte & 0x80:
            return out, i
        shift += 7
        if shift >= 64:
            raise ValueError("varint too long")


def _string(data: bytes, i: int, length: int, by_char: bool) -> tuple[str, int]:
    """`length` bytes of UTF-8, or `length` characters of it - see the module
    docstring for why the second one exists."""
    if not by_char:
        end = i + length
    else:
        end = i
        for _ in range(length):
            lead = data[end]
            end += 1 if lead < 0x80 else 2 if lead < 0xE0 else 3 if lead < 0xF0 else 4
    if end > len(data):
        raise ValueError("string runs past the end of the header")
    return data[i:end].decode("utf-8", "replace"), end


def _walk(data: bytes, by_char: bool) -> tuple[dict, list, int]:
    """The header, or an exception. Nothing here guesses: the file table has to
    land exactly on the end of the header, which is what tells a header read the
    right way from one read the wrong way."""
    end = int.from_bytes(data[:8], "little")
    if not 12 < end <= min(len(data), MAX_HEADER):
        raise ValueError(f"header says it ends at {end}")
    i = 12                                   # past the length, version and count
    count = int.from_bytes(data[10:12], "little")
    properties = {}
    for _ in range(count):
        size, i = _leb128(data, i)
        key, i = _string(data, i, size, by_char)
        size, i = _leb128(data, i)
        value, i = _string(data, i, size, by_char)
        properties[key] = value
    files = []
    while i < end:
        size = data[i]
        i += 1
        name, i = _string(data, i, size, by_char)
        _index, i = _leb128(data, i)
        offset, i = _leb128(data, i)
        length, i = _leb128(data, i)
        _checksum, i = _leb128(data, i)
        files.append((name, offset, length))
    if i != end:
        raise ValueError(f"the file table ends at {i}, not at {end}")
    return properties, files, end


def parse(data: bytes) -> tuple[dict, list, int]:
    """`(properties, [(path, offset, size)], where the payload starts)`.

    Anything unreadable comes back empty rather than raising: one malformed mod
    in a folder of seventy must not take the list down with it.
    """
    for by_char in (False, True):
        try:
            return _walk(data, by_char)
        except Exception:
            continue
    return {}, [], 0


def whole_header(handle, first: bytes) -> bytes:
    """`first` if it already holds the whole header, otherwise the whole of it.

    The length is the first eight bytes, so this costs one extra read and only
    for the few mods whose header does not fit in the first HEADER_BYTES.
    """
    end = int.from_bytes(first[:8], "little")
    if end <= len(first) or not 12 < end <= MAX_HEADER:
        return first
    handle.seek(0)
    return handle.read(end)


def read_header(data: bytes) -> dict:
    """Just the properties of a .tmod."""
    return parse(data)[0]


def file_list(data: bytes) -> set[str]:
    """The paths of the files a mod carries.

    Two mods that write the same path fight over it: whichever the game loads
    second wins, and the other quietly does nothing. That is what the conflict
    check compares.
    """
    return {name.replace("\\", "/").lower() for name, _offset, _size in parse(data)[1]}


# The smallest thing the last resort below will accept as a preview. Only that
# path is measured: by then nothing says the image is a preview except that it
# looks like one, and a screenshot is not 31 pixels wide. Exactly the size of
# the cursor bitmap and the scrap of a Flash file that used to be shown as two
# mods' previews.
_LOOKS_LIKE_A_PREVIEW = (120, 80)


def _dimensions(kind: str, image: bytes) -> tuple[int, int]:
    """How big a PNG or JPEG is, read from its own header."""
    if kind == "png":
        return (int.from_bytes(image[16:20], "big"),
                int.from_bytes(image[20:24], "big"))
    i = 2                                   # past the start-of-image marker
    while i + 9 <= len(image):
        if image[i] != 0xFF:
            return 0, 0
        marker = image[i + 1]
        # The frame headers, which is where the size is written. C4, C8 and CC
        # share the range and are not frames.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            return (int.from_bytes(image[i + 7:i + 9], "big"),
                    int.from_bytes(image[i + 5:i + 7], "big"))
        if 0xD0 <= marker <= 0xD9:
            i += 2
            continue
        i += 2 + int.from_bytes(image[i + 2:i + 4], "big")
    return 0, 0


def _whole_png(data: bytes) -> bytes | None:
    """The PNG in `data` if its chunks run all the way to IEND, else None."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    i = 8
    while i + 8 <= len(data):
        size = int.from_bytes(data[i:i + 4], "big")
        kind = data[i + 4:i + 8]
        if not kind.isalpha():
            return None
        if kind == b"IEND":
            return data[:i + 12]
        i += 12 + size
    return None


def _whole_jpeg(data: bytes) -> bytes | None:
    """The JPEG starting at `data[0]`, if there is a whole one there.

    Walked marker by marker rather than cut at an end marker found by search.
    Searching gets it wrong both ways: the first end marker can belong to the
    thumbnail a photograph carries inside its own header, and the last one can
    belong to something else entirely further down the archive. Two of the
    mods here have three bytes that look like the start of a JPEG and are not.
    """
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xD9:                       # end of image
            return data[:i + 2]
        if marker == 0xDA:                       # the scan: entropy-coded data
            i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
            # Skip to the next real marker; inside the scan every 0xFF is
            # followed by 0x00 (byte stuffing) or by a restart marker.
            while i + 1 < len(data):
                if data[i] == 0xFF and data[i + 1] not in (0x00,) and not (
                        0xD0 <= data[i + 1] <= 0xD7):
                    break
                i += 1
            continue
        if 0xD0 <= marker <= 0xD8:               # standalone markers
            i += 2
            continue
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return None


def _first_image(blob: bytes) -> tuple[str, bytes] | tuple[None, None]:
    """The first whole PNG or JPEG anywhere in `blob`."""
    for signature, kind, whole in (
            (b"\x89PNG\r\n\x1a\n", "png", _whole_png),
            (b"\xff\xd8\xff", "jpeg", _whole_jpeg)):
        at = blob.find(signature)
        while at != -1:
            found = whole(blob[at:])
            if found:
                return kind, found
            at = blob.find(signature, at + 1)
    return None, None


def _unpacked(data: bytes):
    """Every zlib stream in the file, decompressed.

    This is how a .tmod holds what is inside it, and missing it was a real bug.
    The streams are written with UNCOMPRESSED deflate blocks, so a picture's
    bytes do appear verbatim in the file - with a five-byte block header wedged
    in every 32 KB. Read raw, the image decoded as far as the first of those and
    then fell apart, which on screen is a preview that loads halfway and stops.
    """
    at = 0
    while at < len(data):
        at = data.find(b"\x78", at)
        if at == -1 or at + 2 > len(data):
            return
        # The two bytes of a zlib header are a multiple of 31 together. Cheap
        # to check and wrong often enough that the decompress below has to be
        # allowed to fail quietly.
        if (data[at] << 8 | data[at + 1]) % 31 == 0:
            try:
                out = zlib.decompressobj().decompress(data[at:], MAX_IMAGE)
            except zlib.error:
                out = b""
            if len(out) > 64:
                yield out
        at += 1


def _inflate(data: bytes, start: int, wanted: int) -> bytes:
    """The payload, decompressed as far as `wanted` bytes and no further.

    Capped because the payload of a mod carrying textures runs to megabytes and
    the preview is usually the first thing in it - there is no reason to unpack
    a 4 MB block sheet to show a screenshot.
    """
    machine = zlib.decompressobj()
    out = machine.decompress(data[start:], wanted)
    while len(out) < wanted and not machine.eof:
        more = machine.decompress(machine.unconsumed_tail, wanted - len(out))
        if not more:
            break
        out += more
    return out


def _pick(properties: dict, files: list) -> tuple[int, int] | None:
    """Which of the packed files is the preview.

    Normally the one `previewPath` names. Two things go wrong in the wild and
    both are answered here: a mod that names a file it never packed (there is
    no preview then, whatever the property says), and a mod that packs a
    picture without naming it at all. For the second one the file itself is
    good enough evidence - and one called "preview" more so than any other.
    """
    table = {name.replace("\\", "/").lower(): (offset, size)
             for name, offset, size in files}
    named = (properties.get("previewPath") or "").replace("\\", "/").lower()
    if named in table:
        return table[named]
    pictures = [(path, where) for path, where in table.items()
                if path.endswith((".png", ".jpg", ".jpeg"))]
    if not pictures:
        return None
    for path, where in pictures:
        if "preview" in path:
            return where
    return pictures[0][1]


def preview(data: bytes) -> tuple[str, bytes] | tuple[None, None]:
    """The embedded preview image: its type and its bytes.

    Not every mod ships one, and those come back as (None, None).
    """
    properties, files, payload = parse(data)
    where = _pick(properties, files) if files else None
    if where is not None:
        offset, size = where
        if 0 < size <= MAX_IMAGE:
            try:
                image = _inflate(data, payload, offset + size)[offset:offset + size]
            except zlib.error:
                image = b""
            if len(image) == size:
                if image[:8] == b"\x89PNG\r\n\x1a\n":
                    return "png", image
                if image[:2] == b"\xff\xd8":
                    return "jpeg", image

    # Last resort: hunt for the picture by its signature, which is how this was
    # done for everything before the table was understood. One kind of mod
    # still needs it - the one whose payload does not decompress at all, its
    # own packing being broken, so the table cannot be used to reach anything.
    # It is a guess, so what it finds has to look like a preview to be
    # believed: without that the cursor bitmap inside a UI mod and a scrap of a
    # Flash file both came back as previews, which is how a mod ended up
    # showing 31 pixels of nothing.
    for blob in itertools.chain(_unpacked(data), (data,)):
        for kind, whole in (("png", _whole_png), ("jpeg", _whole_jpeg)):
            signature = b"\x89PNG\r\n\x1a\n" if kind == "png" else b"\xff\xd8\xff"
            at = blob.find(signature)
            while at != -1:
                found = whole(blob[at:])
                if found:
                    width, height = _dimensions(kind, found)
                    if (width >= _LOOKS_LIKE_A_PREVIEW[0]
                            and height >= _LOOKS_LIKE_A_PREVIEW[1]):
                        return kind, found
                at = blob.find(signature, at + 1)
    return None, None


def preview_uri(path: Path) -> str:
    """One mod's preview as a data URI, or "" when it has none."""
    try:
        kind, image = preview(path.read_bytes())
    except OSError:
        return ""
    if not image:
        return ""
    return f"data:image/{kind};base64," + base64.b64encode(image).decode("ascii")


def config_key(title: str, author: str) -> str:
    """How the game names a mod in its own list: Trove-<title>-<author>.

    Ambiguous to take apart - most titles have dashes of their own - but we only
    ever build it and compare, never parse it.
    """
    return f"Trove-{title}-{author}"


def _entry(path: Path, off_in_config=()) -> dict:
    renamed = path.suffix.lower() == DISABLED
    # The name without the .disabled, so a mod keeps its identity across being
    # turned off and on again.
    stem = path.name[:-len(DISABLED)] if renamed else path.name
    try:
        with path.open("rb") as handle:
            head = whole_header(handle, handle.read(HEADER_BYTES))
        header, packed, _payload = parse(head)
        files = {name.replace("\\", "/").lower() for name, _o, _s in packed}
        size = path.stat().st_size
    except OSError:
        header, files, size = {}, set(), 0
    title = header.get("title", "")
    author = header.get("author", "")
    key = config_key(title, author)
    preview_path = (header.get("previewPath") or "").replace("\\", "/").lower()
    return {
        "file": path.name,
        "id": stem,
        "key": key,
        # Off either way counts as off. Which way matters when turning it back
        # on, and only then.
        "renamed": renamed,
        "in_config": key in off_in_config,
        # The title inside the file when it has one, the file name when it does
        # not: a mod with no name in the list is a mod nobody can find again.
        "name": title or (stem[:-5] if stem.endswith(".tmod") else stem),
        "author": author,
        "notes": header.get("notes", ""),
        "tags": header.get("tags", ""),
        "version": header.get("modVersion", ""),
        "enabled": not renamed and key not in off_in_config,
        # Consumed by mark_conflicts and then dropped: the interface has no use
        # for a thousand file names.
        "_files": {f for f in files if f != preview_path},
        "size": size,
    }


def listing(folder: Path, off_in_config=()) -> list[dict]:
    """Every mod in the folder, on or off, sorted the way a person reads."""
    found = []
    for path in folder.iterdir():
        name = path.name.lower()
        if not path.is_file():
            continue
        if name.endswith(".tmod") or name.endswith(".tmod" + DISABLED):
            found.append(_entry(path, off_in_config))
    found.sort(key=lambda m: m["name"].lower())
    mark_conflicts(folder, found)
    return found


def mark_conflicts(folder: Path, entries: list[dict]) -> None:
    """Note, on each entry, which other mods write the same files it does.

    Only among the ones that are ON: two mods cannot fight over a file if one
    of them is not being loaded, and listing a clash you have already settled
    by switching one off would be noise.

    The preview image is left out. It travels inside the mod like everything
    else and two mods can easily both carry `ui/preview.png`, but nothing reads
    it while the game runs, so it is not a fight over anything.
    """
    owners: dict[str, list[dict]] = {}
    for entry in entries:
        entry["conflicts"] = []
        if not entry["enabled"]:
            continue
        for name in entry.pop("_files", ()):  # filled in by _entry
            owners.setdefault(name, []).append(entry)

    for name, sharing in owners.items():
        if len(sharing) < 2:
            continue
        for entry in sharing:
            entry["conflicts"].append({
                "file": name,
                "with": [other["name"] for other in sharing if other is not entry],
            })
    for entry in entries:
        entry.pop("_files", None)


def set_enabled(folder: Path, filename: str, enabled: bool,
                config: Path | None = None) -> dict:
    """Turn a mod on or off the way the game does, and report its new state.

    Off goes in the config: that is what the in-game menu writes, and it keeps
    working when the mod file is replaced by a newer version. On has to undo
    both ways of being off, because either could be why it was.

    The name is checked against the folder rather than trusted: it arrives from
    the interface, and a name with a path in it is not one we listed.
    """
    if filename != Path(filename).name:
        raise ValueError("That is not a file in the mods folder.")
    path = folder / filename
    if not path.is_file():
        raise FileNotFoundError(f"{filename} is not there any more.")

    off, _ = disabled_in_config(config) if config else (set(), False)
    with path.open("rb") as handle:
        header = read_header(whole_header(handle, handle.read(HEADER_BYTES)))
    key = config_key(header.get("title", ""), header.get("author", ""))

    if enabled:
        if path.suffix.lower() == DISABLED:
            target = folder / path.name[:-len(DISABLED)]
            if target.exists():
                raise FileExistsError(f"{target.name} already exists in the folder.")
            path.rename(target)
            path = target
        if config and key in off:
            write_disabled(config, off - {key})
            off = off - {key}
    else:
        if not config:
            raise RuntimeError("The game's Trove.cfg was not found, so there is "
                               "nowhere to write the list it reads.")
        if key not in off:
            write_disabled(config, off | {key})
            off = off | {key}

    entry = _entry(path, off)
    # The file set is for mark_conflicts, which only runs over a whole folder.
    # It is also a set, which does not survive the trip to the interface.
    entry.pop("_files", None)
    entry["conflicts"] = []
    return entry
