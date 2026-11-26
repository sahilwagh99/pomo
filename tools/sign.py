"""Code-signs Pomo's build so Windows Smart App Control lets it run.

Smart App Control runs an app only if Microsoft's cloud already knows it, or if every binary it loads (the
.exe and each .dll and .pyd) is signed with a certificate from a CA in Microsoft's Trusted Root Program.
Each Pomo release is new files the cloud hasn't seen yet, so they have to be signed. Qt, Python and
Microsoft ship their files signed already; build.bat signs the rest of dist\\Pomo (Pomo.exe and a few
Python extensions), and Inno Setup calls this again for the uninstaller and the installer.

Set one of these to sign (with neither, the build stays unsigned and says so):
  POMO_SIGN_CERT_SHA1   thumbprint of a code signing certificate in your Windows certificate store, used
                        with signtool (a USB token, Certum SimplySign, SSL.com eSigner CKA...)
  POMO_SIGN_COMMAND     any command that signs one file in place, with {file} where its quoted path goes
                        (for example signtool with Azure Artifact Signing's /dlib and /dmdf). Used instead
                        of POMO_SIGN_CERT_SHA1 when both are set.
Optional:
  POMO_SIGN_TIMESTAMP   RFC 3161 timestamp server for POMO_SIGN_CERT_SHA1 (default DigiCert's)
  POMO_SIGNTOOL         the signtool.exe to use (default: on PATH, else the newest Windows SDK's)
  POMO_SIGN_REQUIRED=1  fail the build instead of building unsigned

Usage:
  python tools/sign.py --configured   exit 0 if signing is set up, 2 if not, 1 if not but required
  python tools/sign.py --list PATH... print the binaries under PATH without a valid signature
  python tools/sign.py PATH...        sign the files named, and the unsigned binaries inside folders
"""
from __future__ import annotations

import os
import shutil
import string
import subprocess
import sys
from pathlib import Path

BINARY_SUFFIXES = {".exe", ".dll", ".pyd"}
DEFAULT_TIMESTAMP = "http://timestamp.digicert.com"
BATCH = 25     # files per signtool call
ATTEMPTS = 3   # timestamp servers fail now and then


def is_configured() -> bool:
    return bool(_thumbprint() or os.environ.get("POMO_SIGN_COMMAND", "").strip())


def _thumbprint() -> str:
    # Copied from the certificate dialog it can carry spaces and an invisible mark: keep the hex only
    return "".join(ch for ch in os.environ.get("POMO_SIGN_CERT_SHA1", "") if ch in string.hexdigits)


def warn(message: str) -> None:
    print(f"::warning::{message}" if os.environ.get("GITHUB_ACTIONS") else f"WARNING: {message}")


def binaries(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.suffix.lower() in BINARY_SUFFIXES and p.is_file())


def unsigned(paths: list[Path]) -> list[Path]:
    """The files among paths without a valid, trusted Authenticode signature."""
    if not paths:
        return []
    script = "$input | ForEach-Object { (Get-AuthenticodeSignature -LiteralPath $_).Status.ToString() }"
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                            input="\n".join(str(p) for p in paths), capture_output=True, text=True)
    statuses = result.stdout.split()
    if result.returncode != 0 or len(statuses) != len(paths):
        raise SystemExit(f"Could not read the files' signatures: {result.stderr.strip()}")
    return [p for p, status in zip(paths, statuses) if status != "Valid"]


def _version(name: str) -> tuple[int, ...]:
    return tuple(int(part) if part.isdigit() else 0 for part in name.split("."))


def find_sdk_tool(exe: str, env_var: str) -> str | None:
    """A Windows SDK tool (signtool.exe, makeappx.exe): from env_var, on PATH, or the newest SDK's."""
    if os.environ.get(env_var):
        return os.environ[env_var]
    if found := shutil.which(exe):
        return found
    kits = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Windows Kits" / "10"
    sdks = sorted(kits.glob(f"bin/10.*/x64/{exe}"), key=lambda p: _version(p.parent.parent.name))
    for candidate in [*reversed(sdks), kits / "App Certification Kit" / exe]:
        if candidate.is_file():
            return str(candidate)
    return None


def find_signtool() -> str:
    found = find_sdk_tool("signtool.exe", "POMO_SIGNTOOL")
    if not found:
        raise SystemExit("signtool.exe was not found: install the Windows SDK, or set POMO_SIGNTOOL to it.")
    return found


def _run(command: list[str] | str) -> None:
    for attempt in range(1, ATTEMPTS + 1):
        if subprocess.run(command, shell=isinstance(command, str)).returncode == 0:
            return
        if attempt < ATTEMPTS:
            print(f"Signing failed, trying again ({attempt + 1}/{ATTEMPTS})...")
    raise SystemExit("Signing failed.")


def sign(files: list[Path]) -> None:
    command = os.environ.get("POMO_SIGN_COMMAND", "").strip()
    if command:
        for path in files:
            _run(command.replace("{file}", f'"{path}"'))
        return
    signtool = find_signtool()
    timestamp = os.environ.get("POMO_SIGN_TIMESTAMP", "").strip() or DEFAULT_TIMESTAMP
    for start in range(0, len(files), BATCH):
        _run([signtool, "sign", "/sha1", _thumbprint(), "/fd", "sha256", "/tr", timestamp, "/td", "sha256",
              *(str(p) for p in files[start:start + BATCH])])


def main(argv: list[str]) -> int:
    if argv == ["--configured"]:
        if is_configured():
            return 0
        if os.environ.get("POMO_SIGN_REQUIRED") == "1":
            print("ERROR: POMO_SIGN_REQUIRED is set, but neither POMO_SIGN_CERT_SHA1 nor POMO_SIGN_COMMAND is.")
            return 1
        warn("Code signing is not set up, so this build is unsigned and Windows Smart App Control "
             "will block it. See tools/sign.py.")
        return 2

    listing = argv[:1] == ["--list"]
    targets = [Path(arg) for arg in (argv[1:] if listing else argv)]
    if not targets:
        print(__doc__)
        return 1
    scanned: list[Path] = []
    named: list[Path] = []
    for target in targets:
        if target.is_dir():
            scanned += binaries(target)
        elif target.is_file():
            named.append(target)
        else:
            print(f"ERROR: {target} does not exist.")
            return 1

    if listing:
        for path in unsigned(scanned + named):
            print(path)
        return 0
    if not is_configured():
        print("ERROR: code signing is not set up (see tools/sign.py).")
        return 1

    # Files named outright are always signed: Inno Setup passes its uninstaller as a temporary file
    to_sign = unsigned(scanned) + named
    sign(to_sign)
    print(f"Signed {len(to_sign)} file(s).")
    # A certificate Windows doesn't trust (self-signed, expired, wrong one) signs fine but still gets blocked
    if untrusted := unsigned(scanned):
        print("ERROR: these still have no trusted signature, so Smart App Control would block them:")
        for path in untrusted:
            print(f"  {path}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
