from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path


class ConnectorUpdateError(RuntimeError):
    pass


def version_is_newer(current: str, remote: str) -> bool:
    def parts(value: str) -> tuple[int, ...]:
        return tuple(int(part) for part in value.split(".") if part.isdigit())

    return parts(remote) > parts(current)


def download_installer(download_url: str, expected_sha256: str) -> Path:
    parsed = urllib.parse.urlparse(download_url)
    is_local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and is_local):
        raise ConnectorUpdateError("????????? HTTPS ??")
    expected = expected_sha256.strip().lower()
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        raise ConnectorUpdateError("???????? SHA-256 ??")
    target_dir = Path(tempfile.gettempdir()) / "XianyuConnector" / "updates"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "XianyuConnectorSetup.exe"
    digest = hashlib.sha256()
    request = urllib.request.Request(
        download_url,
        headers={"User-Agent": "XianyuLocalConnector-Updater"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response, target.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
    except OSError as exc:
        target.unlink(missing_ok=True)
        raise ConnectorUpdateError(f"????????{exc}") from exc
    if digest.hexdigest().lower() != expected:
        target.unlink(missing_ok=True)
        raise ConnectorUpdateError("??? SHA-256 ??????????")
    return target


def launch_installer(installer: Path) -> None:
    if os.name != "nt":
        raise ConnectorUpdateError("??????? Windows")
    subprocess.Popen(
        [
            str(installer),
            "/SP-",
            "/SILENT",
            "/CLOSEAPPLICATIONS",
            "/RESTARTAPPLICATIONS",
        ],
        close_fds=True,
    )
