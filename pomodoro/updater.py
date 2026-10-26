"""Updates from GitHub Releases, one click away.

Every few hours Pomo asks GitHub for its latest release. When there's a newer one, it downloads
Pomo-Setup.exe in the background and checks it against the SHA-256 GitHub publishes for it (a file
that doesn't match is never run), then offers it: "Pomo 1.5.1 is ready. Update now?" One click starts
the installer silently and quits; the installer waits for Pomo to close, updates it in place (same
folder, same startup and desktop-icon choices) and starts Pomo again, even if something went wrong.

That needs the installed version in a folder you can write to. A portable copy, or an install for all
users, gets a "new version available" card with a download link instead. A copy running from source
never checks by itself.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal

REPO = "sahilwagh99/pomo"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
ASSET = "Pomo-Setup.exe"
CHECK_EVERY_MS = 6 * 3600 * 1000
FIRST_CHECK_MS = 3 * 60 * 1000
MAX_BYTES = 300_000_000
MAX_TRIES = 2          # installs of one version that didn't take, before Pomo offers the download page instead
DETACHED_PROCESS, CREATE_NEW_PROCESS_GROUP = 0x8, 0x200


def parse_version(text) -> tuple[int, ...] | None:
    """'v1.5.0' -> (1, 5, 0); None for anything else (say, a pre-release tag)."""
    match = re.fullmatch(r"v?(\d+(?:\.\d+){0,3})", str(text or "").strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def is_newer(candidate, current) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    if new is None or old is None:
        return False
    width = max(len(new), len(old))
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


@dataclass(frozen=True)
class Release:
    version: str          # "1.5.1"
    page: str             # the release's web page (what's new)
    notes: str            # a few lines of the release notes
    installer_url: str    # "" if the release has no installer
    size: int = 0
    sha256: str = ""      # from GitHub's asset digest, if it gives one
    checksum_url: str = ""  # else a Pomo-Setup.exe.sha256 asset next to it


def short_notes(body: str, lines: int = 3) -> str:
    """The first few bullets of the release notes, without markdown marks."""
    out = []
    for raw in str(body or "").splitlines():
        line = raw.strip().lstrip("-*•").strip()
        if not line or line.startswith("#") or line.lower().startswith("full changelog"):
            continue
        out.append(re.sub(r"[*_`]", "", line))
        if len(out) >= lines:
            break
    return "\n".join(out)


def release_of(data) -> Release | None:
    """A Release from GitHub's /releases/latest answer, or None if it isn't a usable one."""
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = str(data.get("tag_name") or "")
    version = parse_version(tag)
    if version is None:
        return None
    installer, checksum = {}, ""
    for asset in data.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        if asset.get("name") == ASSET:
            installer = asset
        elif asset.get("name") == ASSET + ".sha256":
            checksum = str(asset.get("browser_download_url") or "")
    digest = str(installer.get("digest") or "")
    sha = digest.split(":", 1)[1].lower() if digest.lower().startswith("sha256:") else ""
    return Release(".".join(map(str, version)), str(data.get("html_url") or RELEASES_PAGE),
                   short_notes(str(data.get("body") or "")), str(installer.get("browser_download_url") or ""),
                   int(installer.get("size") or 0), sha, checksum)


# ------------------------------------------------------------------ this copy
def app_dir() -> Path:
    return Path(sys.executable).resolve().parent


def dir_writable(folder: Path) -> bool:
    probe = folder / ".pomo-write-test"
    try:
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def install_mode(frozen: bool | None = None, folder: Path | None = None) -> str:
    """'installer' (updates itself), 'manual' (portable or all-users: tell the user) or 'source' (a dev run)."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if not frozen:
        return "source"
    folder = folder or app_dir()
    if (folder / "unins000.exe").exists() and dir_writable(folder):
        return "installer"
    return "manual"


def installer_args(installer: Path, folder: Path, log: Path, relaunch: Path | None) -> list[str]:
    """The silent update: same folder, startup and desktop-icon choices left as they are, Pomo started again."""
    args = [str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/UPDATE=1",
            f"/DIR={folder}", "/MERGETASKS=!autostart,!desktopicon", f"/LOG={log}"]
    if relaunch is not None:
        args.append(f"/RELAUNCH={relaunch}")
    return args


def launch_installer(args: list[str]) -> bool:
    import subprocess

    try:
        subprocess.Popen(args, creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, close_fds=True)
        return True
    except OSError:
        return False


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ------------------------------------------------------------------ the worker
class UpdateWorker(QThread):
    """Checks GitHub (and, if asked, downloads and verifies the installer) on a background thread."""

    checked = Signal(object)        # the newer Release, or None when Pomo is up to date
    ready = Signal(object, str)     # Release, path of the verified installer
    failed = Signal(str, bool)      # why, in a few friendly words; False when it's just being offline

    def __init__(self, current: str, folder: Path, download: bool, parent=None, transport=None):
        super().__init__(parent)
        self.current = current
        self.folder = folder
        self.download = download
        self.transport = transport

    def run(self) -> None:
        import httpx2

        headers = {"Accept": "application/vnd.github+json", "User-Agent": f"Pomo/{self.current}"}
        try:
            with httpx2.Client(timeout=httpx2.Timeout(30.0, connect=15.0), follow_redirects=True,
                               transport=self.transport) as client:
                resp = client.get(LATEST_URL, headers=headers)
                if resp.status_code == 404:  # no releases yet
                    self.checked.emit(None)
                    return
                if resp.status_code >= 400:
                    self.failed.emit(f"GitHub answered with an error ({resp.status_code}).", False)
                    return
                release = release_of(resp.json())
                if release is None or not is_newer(release.version, self.current):
                    self.checked.emit(None)
                    return
                self.checked.emit(release)
                if not self.download or not release.installer_url:
                    return
                path = self._fetch(client, release, headers)
        except (httpx2.TransportError, OSError):
            self.failed.emit("Pomo couldn't reach GitHub.", False)
            return
        except ValueError as exc:  # a bad answer, no checksum, or a file that didn't match
            self.failed.emit(str(exc), True)
            return
        if path is not None:
            self.ready.emit(release, str(path))

    def _fetch(self, client, release: Release, headers: dict) -> Path | None:
        sha = release.sha256
        if not sha and release.checksum_url:
            text = client.get(release.checksum_url, headers={"User-Agent": headers["User-Agent"]}).text
            sha = (text.split() or [""])[0].lower()
        if not re.fullmatch(r"[0-9a-f]{64}", sha or ""):
            raise ValueError("This release has no checksum, so Pomo won't install it by itself.")
        self.folder.mkdir(parents=True, exist_ok=True)
        target = self.folder / f"Pomo-Setup-{release.version}.exe"
        for old in self.folder.glob("Pomo-Setup-*.exe"):  # older downloads aren't needed any more
            if old != target:
                try:
                    old.unlink()
                except OSError:
                    pass
        if target.exists() and file_sha256(target) == sha:
            return target
        part = target.with_suffix(".part")
        digest, size = hashlib.sha256(), 0
        try:
            with client.stream("GET", release.installer_url, headers={"User-Agent": headers["User-Agent"]}) as resp:
                if resp.status_code >= 400:
                    raise ValueError(f"The download failed ({resp.status_code}).")
                with open(part, "wb") as fh:
                    for chunk in resp.iter_bytes():
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise ValueError("The download was far bigger than an installer should be.")
                        digest.update(chunk)
                        fh.write(chunk)
            if digest.hexdigest() != sha:
                raise ValueError("The download didn't match GitHub's checksum, so Pomo threw it away.")
            os.replace(part, target)
        finally:
            part.unlink(missing_ok=True)  # gone either way: renamed, or not worth keeping
        return target
