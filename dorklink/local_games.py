from __future__ import annotations

import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


MAX_DEPTH = 16
MAX_ENTRIES = 150_000
MAX_CANDIDATES = 500
MAX_MANIFEST_BYTES = 256 * 1024
UNKNOWN_GAME_DATA_MIN_BYTES = 64 * 1024 * 1024
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

TITLE_ID_RE = re.compile(r"\b(?:CUSA|PPSA|PCAS|PLAS)\d{5}\b", re.IGNORECASE)
VERSION_RE = re.compile(r"\b(?:update|patch|version|ver|v)[ _.-]*\d+(?:[._-]\d+){0,3}\b", re.IGNORECASE)
SPLIT_PACKAGE_RE = re.compile(r"\.(?:fpkg|fpk|pkg|iso|nsp|xci)\.\d+$", re.IGNORECASE)
SPLIT_ARCHIVE_RE = re.compile(
    r"(?:\.part\d+\.(?:rar|7z|zip)|\.(?:rar|7z|zip)\.part\d+|\.(?:rar|7z|zip|pkg|fpkg|fpk|iso)\.\d+|\.r\d{2}|\.z\d{2}|\.\d{3,4})$",
    re.IGNORECASE,
)
ARCHIVE_SUFFIXES = (
    ".tar.gz", ".tar.xz", ".tar.bz2", ".tar.zst", ".tbz2", ".tgz", ".txz",
    ".7z", ".rar", ".zip", ".tar", ".gz", ".bz2", ".xz", ".zst", ".lz4",
    ".cab", ".ace", ".arj", ".lzh", ".lha", ".iso", ".chd", ".cso", ".zso",
    ".pkg", ".fpkg", ".fpk", ".nsp", ".xci", ".cia", ".3ds", ".nds", ".gba",
    ".gbc", ".gb", ".nes", ".n64", ".z64", ".v64", ".wbfs", ".wud", ".wua",
    ".gcm", ".gcz", ".rvz", ".pbp", ".vpk", ".wad", ".xex", ".xvc", ".nro",
    ".nso", ".nca", ".apk", ".xapk", ".apks", ".appx", ".msix", ".msi",
    ".appxbundle", ".msixbundle", ".appimage", ".deb", ".rpm",
    ".dmg", ".exe", ".bin", ".cue", ".elf", ".dol", ".rom", ".md", ".sfc",
    ".smc", ".pce", ".cdi", ".gdi", ".a26", ".a78", ".lnx", ".psarc", ".pak",
    ".pck", ".ucas", ".utoc", ".bundle", ".assetbundle", ".unity3d", ".assets", ".resource",
    ".resources", ".obb", ".bsa", ".ba2", ".cpk", ".big", ".arc",
)
GAME_FILE_SUFFIXES = tuple(sorted(ARCHIVE_SUFFIXES, key=len, reverse=True))
GENERIC_GAME_DATA_STEM_RE = re.compile(
    r"^(?:pakchunk\d*|globalgamemanagers|sharedassets\d*|track\s*\d+|data\s*\d*|"
    r"assetbundle|resources?|content|boot|main|system|archive|chunk\s*\d+|part\s*\d+|payload|unknown)(?:\s+.*)?$",
    re.IGNORECASE,
)
GENERIC_NAMES = {
    "app", "apps", "bin", "binaries", "common", "content", "data", "download",
    "downloads", "game", "games", "gog games", "epic games", "install", "installed",
    "game archives", "game folder", "game library", "game data", "installer", "launch", "launcher", "library", "my games",
    "playstation library", "program files", "program files (x86)", "selected games", "steam library",
    "steamlibrary", "ps4 packages", "ps5 packages", "package files",
    "redist", "runtime", "steamapps", "system", "update", "updates", "windows",
}
GAME_CONTAINER_NAMES = {
    "games", "game", "gog games", "epic games", "playstation games", "ps4", "ps5",
    "xboxgames", "playnite", "installed games", "installations", "common", "my games", "applications",
    "selected games", "steam library", "steamlibrary", "playstation library", "game library",
}
SKIP_DIRECTORIES = {
    "$recycle.bin", "system volume information", "windows", "windowsapps", "appdata",
    "programdata", "node_modules", ".git", "__pycache__", "shadercache", "downloading",
}
NON_GAME_EXECUTABLES = {
    "unins000", "uninstall", "uninstaller", "setup", "installer", "install", "patch",
    "crashreporter", "unitycrashhandler32", "unitycrashhandler64", "vc_redist.x64",
    "vc_redist.x86", "vcredist_x64", "vcredist_x86", "dxsetup", "eosbootstrapper",
    "easyanticheat_setup", "steam", "epicgameslauncher", "galaxyclient",
}


@dataclass(frozen=True)
class GameNameCandidate:
    name: str
    evidence: str
    relative_path: str


@dataclass(frozen=True)
class LocalGameScanResult:
    root: str
    candidates: tuple[GameNameCandidate, ...]
    entries_seen: int
    errors: int
    limited: bool
    cancelled: bool
    filesystem: str = ""


def normalize_game_name(value: str) -> str | None:
    """Turn a filename or directory label into a cautious search-name candidate."""
    value = unicodedata.normalize("NFKC", value or "")
    value = value.replace("\u200c", " ").replace("\u200d", " ").replace("\ufeff", "")
    value = SPLIT_PACKAGE_RE.sub("", value)
    value = SPLIT_ARCHIVE_RE.sub("", value)
    value = re.sub(r"\.(?:f?pkg)(?:\.\d+)?$", "", value, flags=re.IGNORECASE)
    for suffix in ARCHIVE_SUFFIXES:
        if value.casefold().endswith(suffix):
            value = value[:-len(suffix)]
            break
    value = re.sub(r"\b[A-Z]{2}\d{4}-CUSA\d{5}[^\s]*", " ", value, flags=re.IGNORECASE)
    value = TITLE_ID_RE.sub(" ", value)
    value = value.replace("_", " ")
    value = VERSION_RE.sub(" ", value)
    value = re.sub(r"\[[^\]]*(?:CUSA|PPSA|USA|EUR|JPN|REGION|UPDATE|DLC)[^\]]*\]", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\([^)]*(?:CUSA|PPSA|USA|EUR|JPN|UPDATE|DLC|REPACK|FITGIRL)[^)]*\)", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"[.\-]+", " ", value)
    value = re.sub(r"[^\w\s'’:&+!(),\u0600-\u06ff]", " ", value, flags=re.UNICODE)
    value = " ".join(value.split()).strip(" _.-")
    if len(value) < 3 or value.casefold() in GENERIC_NAMES or TITLE_ID_RE.search(value):
        return None
    if not any(unicodedata.category(char).startswith("L") for char in value):
        return None
    if len(value) > 120:
        return None
    return value


def _has_game_file_suffix(name: str) -> bool:
    lowered = name.casefold()
    return any(lowered.endswith(suffix) for suffix in GAME_FILE_SUFFIXES) or bool(
        SPLIT_PACKAGE_RE.search(name) or SPLIT_ARCHIVE_RE.search(name)
    )


def _add_file_or_parent_name(
    add, name: str, path: Path, root: Path, relative: str,
    file_evidence: str = "package/archive filename",
) -> None:
    title = normalize_game_name(name)
    if title and not GENERIC_GAME_DATA_STEM_RE.fullmatch(title):
        add(name, file_evidence, relative)
        return
    folder_candidate = _game_folder_for_executable(path, root)
    if folder_candidate != root:
        add(folder_candidate.name, "game data folder", folder_candidate.relative_to(root).as_posix())
    elif root.name.casefold() not in GENERIC_NAMES | GAME_CONTAINER_NAMES:
        add(root.name, "game data folder", ".")


def _filesystem_name(root: Path) -> str:
    """Return the selected Windows volume format (for example NTFS or exFAT)."""
    if os.name != "nt":
        return ""
    try:
        import ctypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        volume_path = ctypes.create_unicode_buffer(32768)
        get_volume_path = kernel.GetVolumePathNameW
        get_volume_path.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint)
        get_volume_path.restype = ctypes.c_int
        if not get_volume_path(str(root), volume_path, len(volume_path)):
            return ""
        file_system = ctypes.create_unicode_buffer(64)
        get_volume_info = kernel.GetVolumeInformationW
        get_volume_info.argtypes = (
            ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint,
            ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint), ctypes.c_wchar_p, ctypes.c_uint,
        )
        get_volume_info.restype = ctypes.c_int
        if not get_volume_info(volume_path.value, None, 0, None, None, None, file_system, len(file_system)):
            return ""
        return file_system.value
    except Exception:
        return ""


def _normal_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = normalized.translate(str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه"}))
    normalized = normalized.replace("\u200c", "").replace("\u200d", "")
    return " ".join(normalized.split())


def _is_within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((os.path.normcase(str(path)), os.path.normcase(str(root)))) == os.path.normcase(str(root))
    except (ValueError, OSError):
        return False


def _steam_manifest_names(path: Path) -> list[str]:
    try:
        with path.open("rb") as manifest:
            raw = manifest.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            return []
        text = raw.decode("utf-8", errors="replace")
    except OSError:
        return []
    values = []
    for key in ("name", "installdir"):
        match = re.search(rf'"{key}"\s*"((?:[^"\\]|\\.)*)"', text, re.IGNORECASE)
        if match:
            value = match.group(1).replace(r'\"', '"').strip()
            if value:
                values.append(value)
    return values


def _game_folder_for_executable(path: Path, root: Path) -> Path:
    generic = GENERIC_NAMES | {"win64", "win32", "x64", "x86", "bin32", "bin64", "binaries"}
    candidate = path.parent
    while candidate != root and (
        candidate.name.casefold() in generic or candidate.name.casefold().endswith(("_data", "_content", "_files"))
    ) and candidate.parent != candidate:
        candidate = candidate.parent
    return candidate


def scan_local_game_names(
    root_path: str | Path,
    cancelled: Callable[[], bool] = lambda: False,
    progress: Callable[[int, int], None] | None = None,
) -> LocalGameScanResult:
    """Read filesystem names and small Steam manifests under one explicitly chosen root.

    The scan never executes, unpacks, hashes, or modifies files. It does not follow
    symlinks or Windows reparse points and is bounded by depth and entry limits.
    """
    selected = Path(root_path).expanduser().absolute()
    try:
        selected_info = selected.stat(follow_symlinks=False)
    except OSError as exc:
        raise ValueError(f"Could not inspect the selected folder: {exc}") from exc
    if selected.is_symlink() or getattr(selected_info, "st_file_attributes", 0) & REPARSE_POINT:
        raise ValueError("The selected folder is a link or reparse point. Choose its real folder path instead.")
    root = selected.resolve(strict=True)
    if os.path.normcase(str(root)) != os.path.normcase(str(selected)):
        raise ValueError("The selected path resolves through a link. Choose its real folder path instead.")
    if not root.is_dir():
        raise ValueError("Choose a folder, not a file.")

    candidates: dict[str, GameNameCandidate] = {}
    source_rank = {
        "Steam manifest": 0, "game folder": 1, "package/archive filename": 2,
        "game data filename": 3, "game data folder": 4,
        "game executable folder": 5, "game executable name": 6,
    }
    entries_seen = 0
    errors = 0
    limited = False
    was_cancelled = False

    def add(raw_name: str, evidence: str, relative_path: str) -> None:
        nonlocal limited
        name = normalize_game_name(raw_name)
        if not name:
            return
        key = _normal_key(name)
        current = candidates.get(key)
        if current is None and len(candidates) >= MAX_CANDIDATES:
            limited = True
            return
        item = GameNameCandidate(name=name, evidence=evidence, relative_path=relative_path[:500])
        if current is None or source_rank.get(evidence, 99) < source_rank.get(current.evidence, 99):
            candidates[key] = item

    stack: list[tuple[Path, int]] = [(root, 0)]

    while stack:
        if cancelled():
            was_cancelled = True
            break
        folder, depth = stack.pop()
        if depth > MAX_DEPTH:
            errors += 1
            continue
        try:
            resolved = folder.resolve(strict=True)
            if os.path.normcase(str(resolved)) != os.path.normcase(str(folder.absolute())) or not _is_within(resolved, root):
                errors += 1
                continue
            with os.scandir(folder) as entries:
                for entry in entries:
                    if limited:
                        break
                    if cancelled():
                        was_cancelled = True
                        break
                    entries_seen += 1
                    if entries_seen > MAX_ENTRIES:
                        limited = True
                        break
                    if progress and entries_seen % 1000 == 0:
                        progress(entries_seen, len(candidates))
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        errors += 1
                        continue
                    if getattr(info, "st_file_attributes", 0) & REPARSE_POINT or entry.is_symlink():
                        continue
                    path = Path(entry.path)
                    if not _is_within(path.absolute(), root):
                        errors += 1
                        continue
                    if stat.S_ISDIR(info.st_mode):
                        if entry.name.casefold() in SKIP_DIRECTORIES:
                            continue
                        parent_name = folder.name.casefold()
                        if entry.name.casefold().endswith(".app"):
                            add(entry.name[:-4], "package/archive filename", path.relative_to(root).as_posix())
                            continue
                        if parent_name in GAME_CONTAINER_NAMES:
                            relative = path.relative_to(root).as_posix()
                            add(entry.name, "game folder", relative)
                        if depth < MAX_DEPTH:
                            stack.append((path, depth + 1))
                        else:
                            errors += 1
                        continue
                    if not stat.S_ISREG(info.st_mode):
                        continue

                    extension = Path(entry.name).suffix.casefold()
                    relative = path.relative_to(root).as_posix()
                    if extension == ".acf" and entry.name.casefold().startswith("appmanifest_"):
                        for manifest_name in _steam_manifest_names(path):
                            add(manifest_name, "Steam manifest", relative)
                    if extension == ".exe":
                        executable_stem = Path(entry.name).stem
                        if executable_stem.casefold() not in NON_GAME_EXECUTABLES:
                            folder_candidate = _game_folder_for_executable(path, root)
                            if folder_candidate != root:
                                add(folder_candidate.name, "game executable folder", folder_candidate.relative_to(root).as_posix())
                            elif root.name.casefold() not in GENERIC_NAMES | GAME_CONTAINER_NAMES:
                                add(root.name or executable_stem, "game executable folder", "." if root.name else relative)
                            else:
                                add(executable_stem, "game executable name", relative)
                    elif _has_game_file_suffix(entry.name):
                        _add_file_or_parent_name(add, entry.name, path, root, relative)
                    elif info.st_size >= UNKNOWN_GAME_DATA_MIN_BYTES:
                        folder_candidate = _game_folder_for_executable(path, root)
                        if folder_candidate != root:
                            add(folder_candidate.name, "game data folder", folder_candidate.relative_to(root).as_posix())
                        elif root.name.casefold() not in GENERIC_NAMES | GAME_CONTAINER_NAMES:
                            add(root.name, "game data folder", ".")
                        else:
                            _add_file_or_parent_name(
                                add, Path(entry.name).stem, path, root, relative, "game data filename"
                            )
                if was_cancelled or limited:
                    break
        except (OSError, PermissionError):
            errors += 1

    if progress:
        progress(entries_seen, len(candidates))
    ordered = tuple(sorted(candidates.values(), key=lambda item: _normal_key(item.name)))
    return LocalGameScanResult(str(root), ordered, entries_seen, errors, limited, was_cancelled, _filesystem_name(root))
