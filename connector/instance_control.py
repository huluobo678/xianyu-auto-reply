from __future__ import annotations

import ctypes
import os


_EVENT_NAME = "Local\\XianyuConnectorBindRequested"
_EVENT_MODIFY_STATE = 0x0002
_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0x00000000


class BindSignal:
    def __init__(self, handle: int | None = None):
        self.handle = handle

    @classmethod
    def listen(cls) -> "BindSignal":
        if os.name != "nt":
            return cls()
        handle = ctypes.windll.kernel32.CreateEventW(None, True, False, _EVENT_NAME)
        return cls(handle or None)

    @staticmethod
    def signal_existing() -> bool:
        if os.name != "nt":
            return False
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenEventW(
            _EVENT_MODIFY_STATE | _SYNCHRONIZE, False, _EVENT_NAME
        )
        if not handle:
            return False
        try:
            return bool(kernel32.SetEvent(handle))
        finally:
            kernel32.CloseHandle(handle)

    def consume(self) -> bool:
        if not self.handle or os.name != "nt":
            return False
        kernel32 = ctypes.windll.kernel32
        if kernel32.WaitForSingleObject(self.handle, 0) != _WAIT_OBJECT_0:
            return False
        kernel32.ResetEvent(self.handle)
        return True

    def close(self) -> None:
        if self.handle and os.name == "nt":
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None
