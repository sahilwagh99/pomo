"""Is a game in front? The window in front, and the decision whether it's a game.

Gaming mode turns Pomo's timer into a telemetry readout while you play, so it has to know when that is
without being told every time. The decision runs from strongest signal to weakest: the games and
not-games you named, a built-in list of apps that are never games (browsers, players, editors, the
desktop, Pomo itself...), where the program lives (a Steam, Epic, Riot, GOG, Xbox, EA, Ubisoft or
Battle.net library), and last, behaviour: a window that covers its whole monitor and keeps the GPU's 3D
engine busy is almost always a game. Everything here reads what Windows tells any app; no admin rights.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass

GAME_GPU_PCT = 25.0  # a fullscreen app at least this busy on the 3D engine counts as a game

# Apps that are never games, by exe name without ".exe" (lower-case)
NOT_GAMES = frozenset({
    # Pomo itself, the desktop and the shell
    "pomo", "focusorb", "explorer", "shellexperiencehost", "startmenuexperiencehost", "searchhost", "searchapp",
    "searchui", "textinputhost", "lockapp", "logonui", "consent", "applicationframehost", "systemsettings",
    "taskmgr", "dwm", "sihost", "ctfmon", "rundll32", "mmc", "control", "regedit", "snippingtool",
    "screenclippinghost", "widgets", "msedgewebview2", "gamebar", "gamebarftserver", "xboxpcapp", "xboxapp",
    "scrnsave", "ribbons", "mystify", "bubbles", "sstext3d", "photoscreensaver", "magnify", "osk",
    # browsers
    "chrome", "msedge", "firefox", "brave", "opera", "opera_gx", "vivaldi", "arc", "iexplore", "chromium",
    "waterfox", "librewolf", "floorp", "zen", "thorium", "browser", "tor", "duckduckgo",
    # media players and streaming
    "vlc", "mpc-hc", "mpc-hc64", "mpc-be", "mpc-be64", "mpv", "potplayer", "potplayer64", "potplayermini",
    "potplayermini64", "wmplayer", "microsoft.media.player", "video.ui", "music.ui", "spotify", "itunes",
    "applemusic", "appletv", "plex", "plexhtpc", "plex media player", "kodi", "netflix", "foobar2000", "aimp",
    "musicbee", "winamp", "obs64", "obs32", "obs", "streamlabs obs", "streamlabs", "twitch", "photos",
    "wallpaper32", "wallpaper64", "lively", "losslessscaling",
    # office, documents and notes
    "winword", "excel", "powerpnt", "outlook", "olk", "onenote", "onenoteim", "msaccess", "mspub", "visio",
    "winproj", "acrobat", "acrord32", "sumatrapdf", "foxitpdfreader", "foxitphantompdf", "notepad", "wordpad",
    "notepad++", "soffice", "soffice.bin", "swriter", "scalc", "simpress", "obsidian", "notion", "evernote",
    "onecommander", "totalcmd64", "everything",
    # IDEs, editors, terminals and creative tools
    "code", "code - insiders", "cursor", "windsurf", "zed", "devenv", "idea64", "pycharm64", "webstorm64",
    "rider64", "goland64", "clion64", "phpstorm64", "rubymine64", "datagrip64", "studio64", "sublime_text",
    "atom", "vim", "gvim", "emacs", "eclipse", "netbeans64", "unity", "unity hub", "unrealeditor", "ue4editor",
    "blender", "photoshop", "illustrator", "afterfx", "adobe premiere pro", "resolve", "figma", "krita",
    "windowsterminal", "cmd", "powershell", "pwsh", "conhost", "openconsole", "wezterm-gui", "alacritty",
    "mintty", "hyper", "warp", "tabby", "mstsc", "msrdc",
    # video calls and chat
    "zoom", "teams", "ms-teams", "msteams", "slack", "discord", "skype", "webex", "ciscocollabhost", "atmgr",
    "whatsapp", "telegram", "signal",
    # game launchers and stores (the games they start are games; they aren't)
    "steam", "steamwebhelper", "epicgameslauncher", "epicwebhelper", "unrealcefsubprocess",
    "riotclientservices", "riotclientux", "riot client", "battle.net", "battle.net launcher", "agent",
    "blizzarderror", "eadesktop", "eabackgroundservice", "origin", "upc", "ubisoftconnect", "galaxyclient",
    "galaxyclient helper", "amazongames", "amazon games", "amazon games ui", "playnite.desktopapp",
    "playnite.fullscreenapp", "vrmonitor", "vrserver", "vrdashboard",
})

# A program under one of these folders came from a game store (matched anywhere in its lower-case path)
LIBRARY_MARKERS = (
    "\\steamapps\\common\\", "\\epic games\\", "\\riot games\\", "\\gog galaxy\\games\\", "\\gog games\\",
    "\\xboxgames\\", "\\ea games\\", "\\ubisoft game launcher\\games\\", "\\battle.net\\",
    "\\amazon games\\library\\",
)
# ...except the store's own programs and tools that sit beside the games
LIBRARY_EXCLUDE = (
    "\\epic games\\launcher\\", "\\riot games\\riot client\\", "\\steamapps\\common\\steamworks shared\\",
    "\\steamapps\\common\\steamvr\\", "\\steamapps\\common\\steam controller configs\\",
)
# A game folder's helpers (installers, crash reporters, anti-cheat) aren't the game
HELPER_WORDS = ("unins", "crash", "redist", "vc_", "dxsetup", "setup", "install", "updater", "report",
                "cefprocess", "webhelper", "launcherprereq", "easyanticheat", "battleye")

# Where launchers put games, relative to the root of each fixed drive
LAUNCHER_DIRS = (
    r"Program Files\Epic Games", r"Program Files (x86)\Epic Games", "Epic Games",
    "Riot Games",
    r"Program Files (x86)\GOG Galaxy\Games", r"Program Files\GOG Galaxy\Games", "GOG Games",
    "XboxGames",
    r"Program Files\EA Games", r"Program Files (x86)\EA Games", "EA Games",
    r"Program Files (x86)\Ubisoft\Ubisoft Game Launcher\games", r"Program Files\Ubisoft\Ubisoft Game Launcher\games",
    r"Program Files (x86)\Battle.net", r"Program Files\Battle.net",
    r"Amazon Games\Library",
    r"SteamLibrary\steamapps\common", r"Program Files (x86)\Steam\steamapps\common",
)


@dataclass(frozen=True)
class Foreground:
    hwnd: int
    pid: int
    exe: str          # lower-case file name, e.g. "eldenring.exe"
    path: str         # full path, "" if unknown
    title: str
    fullscreen: bool  # covers its whole monitor (exclusive or borderless)
    gpu: float | None  # this process's 3D GPU engine use in %, None if unknown


def exe_key(name: str) -> str:
    """'C:\\Games\\Valorant.exe', 'Valorant.exe' or 'valorant' -> 'valorant' (how lists name apps)."""
    base = name.strip().replace("/", "\\").split("\\")[-1].lower()
    return base[:-4] if base.endswith(".exe") else base


# ------------------------------------------------------------------ the decision
def is_game(fg: Foreground, games: list[str], not_games: list[str], library_dirs: list[str] | None = None) -> bool:
    """True when the app in front is a game. Pure: it only looks at fg and the lists it's given.

    games and not_games are exe names (with or without ".exe", any case): what you said wins over
    everything Pomo would guess.
    """
    if fg.pid == os.getpid():
        return False
    key = exe_key(fg.exe)
    if key and key in {exe_key(g) for g in games}:
        return True
    if key and key in {exe_key(n) for n in not_games}:
        return False
    if key in NOT_GAMES or fg.exe.lower().endswith(".scr"):  # .scr: a screen saver
        return False
    if fg.path and in_library(fg.path, library_dirs or []) and not any(word in key for word in HELPER_WORDS):
        return True
    return fg.fullscreen and fg.gpu is not None and fg.gpu >= GAME_GPU_PCT


def in_library(path: str, library_dirs: list[str]) -> bool:
    """Whether a program's path is inside a game library: a store's usual folder or one of library_dirs."""
    low = os.path.normcase(os.path.normpath(path)).replace("/", "\\").lower()
    if any(marker in low for marker in LIBRARY_EXCLUDE):
        return False
    if any(marker in low for marker in LIBRARY_MARKERS):
        return True
    for folder in library_dirs:
        if folder:
            base = os.path.normcase(os.path.normpath(folder)).replace("/", "\\").lower().rstrip("\\") + "\\"
            if low.startswith(base):
                return True
    return False


# ------------------------------------------------------------------ where games live
def game_library_dirs() -> list[str]:
    """Folders that hold installed games: your Steam libraries, Epic's install folders, and the launchers'
    usual folders on each fixed drive (only the ones that exist)."""
    if sys.platform != "win32":
        return []
    found: list[str] = []
    for source in (_steam_libraries, _epic_installs, _launcher_folders):
        try:
            found += source()
        except Exception:  # one launcher's odd records mustn't hide the others
            pass
    out: dict[str, str] = {}
    for folder in found:
        out.setdefault(os.path.normcase(os.path.normpath(folder)), os.path.normpath(folder))
    return list(out.values())


def _steam_libraries() -> list[str]:
    """steamapps\\common of every Steam library (Steam's own folder plus those in libraryfolders.vdf)."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam = winreg.QueryValueEx(key, "SteamPath")[0]
    except OSError:
        return []
    libraries = [steam]
    try:
        with open(os.path.join(steam, "steamapps", "libraryfolders.vdf"), encoding="utf-8", errors="ignore") as fh:
            libraries += [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', fh.read())]
    except OSError:
        pass
    common = [os.path.join(os.path.normpath(lib), "steamapps", "common") for lib in dict.fromkeys(libraries)]
    return [c for c in common if os.path.isdir(c)]


def _epic_installs() -> list[str]:
    """Each Epic Games game's folder, from the launcher's install manifests (games can live anywhere)."""
    import json

    folder = os.path.expandvars(r"%PROGRAMDATA%\Epic\EpicGamesLauncher\Data\Manifests")
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    out = []
    for name in names:
        if not name.endswith(".item"):
            continue
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                location = json.load(fh).get("InstallLocation", "")
        except (OSError, ValueError, AttributeError):
            continue
        if location and os.path.isdir(location):
            out.append(location)
    return out


def _launcher_folders() -> list[str]:
    return [os.path.join(drive, rel) for drive in _fixed_drives() for rel in LAUNCHER_DIRS
            if os.path.isdir(os.path.join(drive, rel))]


def _fixed_drives() -> list[str]:
    """'C:\\', 'D:\\'...: local disks only (a network or removed drive could stall the check)."""
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetDriveTypeW.restype = ctypes.c_uint
    mask = kernel32.GetLogicalDrives()
    drives = [f"{chr(65 + i)}:\\" for i in range(26) if mask & (1 << i)]
    return [d for d in drives if kernel32.GetDriveTypeW(d) == 3]  # DRIVE_FIXED


# ------------------------------------------------------------------ what's in front
def foreground(gpu_by_pid: dict[int, float] | None = None) -> Foreground | None:
    """The window in front, or None (nothing, or not Windows).

    gpu_by_pid is each process's 3D engine use from the telemetry worker; without it gpu is None. A
    process missing from a map that was given isn't using the 3D engine, so its gpu is 0.
    """
    if sys.platform != "win32":
        return None
    try:
        return _foreground(gpu_by_pid)
    except Exception:  # a window that closed mid-read, or an API that refused: nothing to report
        return None


_api = None


def _win():
    """Private ctypes handles with exact signatures (ctypes.windll is shared with the rest of Pomo)."""
    global _api
    if _api is None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32")
        kernel32 = ctypes.WinDLL("kernel32")
        shell32 = ctypes.WinDLL("shell32")
        H = wintypes.HWND
        user32.GetForegroundWindow.restype = H
        user32.GetAncestor.argtypes = [H, ctypes.c_uint]
        user32.GetAncestor.restype = H
        user32.GetShellWindow.restype = H
        user32.GetDesktopWindow.restype = H
        user32.GetWindowThreadProcessId.argtypes = [H, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowTextLengthW.argtypes = [H]
        user32.GetWindowTextW.argtypes = [H, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.argtypes = [H, wintypes.LPWSTR, ctypes.c_int]
        user32.IsIconic.argtypes = [H]
        user32.IsZoomed.argtypes = [H]
        user32.GetWindowRect.argtypes = [H, ctypes.POINTER(wintypes.RECT)]
        user32.MonitorFromWindow.argtypes = [H, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HMONITOR
        user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
        user32.GetWindowLongW.argtypes = [H, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                        ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        shell32.SHQueryUserNotificationState.argtypes = [ctypes.POINTER(ctypes.c_int)]
        _api = (ctypes, wintypes, user32, kernel32, shell32)
    return _api


def _foreground(gpu_by_pid: dict[int, float] | None) -> Foreground | None:
    ctypes, wintypes, user32, kernel32, _shell32 = _win()
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    hwnd = user32.GetAncestor(hwnd, 2) or hwnd  # GA_ROOT: the top-level window, not a child that has focus
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    length = user32.GetWindowTextLengthW(hwnd)
    title = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, title, length + 1)
    path = process_path(pid.value)
    gpu = None if gpu_by_pid is None else float(gpu_by_pid.get(pid.value, 0.0))
    return Foreground(hwnd=int(hwnd), pid=int(pid.value), exe=os.path.basename(path).lower(), path=path,
                      title=title.value, fullscreen=_is_fullscreen(int(hwnd)), gpu=gpu)


def process_path(pid: int) -> str:
    """A process's full exe path, or "" when Windows won't say (an elevated or protected process)."""
    if not pid:
        return ""
    ctypes, wintypes, _user32, kernel32, _shell32 = _win()
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
    finally:
        kernel32.CloseHandle(handle)
    return ""


def _is_fullscreen(hwnd: int) -> bool:
    """The window covers its whole monitor: exclusive fullscreen, or borderless windowed.

    A maximized window with a title bar also reaches past the edges when the taskbar hides itself, so
    that doesn't count; neither does the desktop.
    """
    ctypes, wintypes, user32, _kernel32, shell32 = _win()
    if user32.IsIconic(hwnd) or hwnd in (user32.GetShellWindow(), user32.GetDesktopWindow()):
        return False
    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, cls, 64)
    if cls.value in ("Progman", "WorkerW"):  # the desktop behind the icons
        return False

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]

    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    monitor = user32.MonitorFromWindow(hwnd, 0)  # MONITOR_DEFAULTTONULL
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        m = info.rcMonitor
        covers = rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom
        style = user32.GetWindowLongW(hwnd, -16) & 0xFFFFFFFF  # GWL_STYLE
        captioned = style & 0x00C00000 == 0x00C00000  # WS_CAPTION
        if covers and not (user32.IsZoomed(hwnd) and captioned):
            return True
    state = ctypes.c_int(0)
    try:  # an exclusive-fullscreen Direct3D app can report odd window sizes; the shell knows it's there
        return shell32.SHQueryUserNotificationState(ctypes.byref(state)) == 0 and state.value == 3
    except OSError:
        return False
