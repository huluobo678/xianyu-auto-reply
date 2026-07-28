from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(
        len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))
    ), buffer


def _crypt(data: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise OSError("DPAPI is only available on Windows")
    source, source_buffer = _blob(data)
    result = _DataBlob()
    function = (
        ctypes.windll.crypt32.CryptUnprotectData
        if decrypt
        else ctypes.windll.crypt32.CryptProtectData
    )
    if not function(
        ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(result)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)
        del source_buffer


class SecureCredentialStore:
    def __init__(self, path: Path | None = None):
        base = (
            Path(os.environ.get("LOCALAPPDATA", Path.home()))
            / "XianyuConnector"
            / "data"
        )
        self.path = path or base / "credentials.bin"

    def save(self, values: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        payload = json.dumps(
            {"version": 1, "values": values}, ensure_ascii=False
        ).encode("utf-8")
        temporary.write_bytes(_crypt(payload, decrypt=False))
        os.replace(temporary, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def load(self) -> dict:
        if not self.path.exists():
            return {}
        payload = json.loads(
            _crypt(self.path.read_bytes(), decrypt=True).decode("utf-8")
        )
        if "values" in payload:
            return payload["values"]
        return payload

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
