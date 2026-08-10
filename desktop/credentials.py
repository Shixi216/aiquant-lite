from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass
from typing import Protocol


CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
TARGET_PREFIX = "HermesOPC/Provider/"


class CredentialStore(Protocol):
    def write(self, key: str, value: str) -> None: ...

    def read(self, key: str) -> str | None: ...

    def delete(self, key: str) -> bool: ...


class _CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", wintypes.LPVOID),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class WindowsCredentialStore:
    def __init__(self, *, prefix: str = TARGET_PREFIX) -> None:
        if os.name != "nt":
            raise RuntimeError("Windows Credential Manager is not supported")
        self.prefix = prefix
        self._advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self._advapi32.CredWriteW.argtypes = [
            ctypes.POINTER(_CREDENTIAL),
            wintypes.DWORD,
        ]
        self._advapi32.CredWriteW.restype = wintypes.BOOL
        self._advapi32.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(_CREDENTIAL)),
        ]
        self._advapi32.CredReadW.restype = wintypes.BOOL
        self._advapi32.CredDeleteW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self._advapi32.CredDeleteW.restype = wintypes.BOOL
        self._advapi32.CredFree.argtypes = [wintypes.LPVOID]

    def _target(self, key: str) -> str:
        cleaned = key.strip()
        if not cleaned or any(character in cleaned for character in "\r\n"):
            raise ValueError("invalid credential key")
        return self.prefix + cleaned

    def write(self, key: str, value: str) -> None:
        if not value:
            raise ValueError("credential value must not be empty")
        blob = value.encode("utf-16-le")
        buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        credential = _CREDENTIAL(
            Type=CRED_TYPE_GENERIC,
            TargetName=self._target(key),
            CredentialBlobSize=len(blob),
            CredentialBlob=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
            Persist=CRED_PERSIST_LOCAL_MACHINE,
            UserName="HermesOPC",
        )
        if not self._advapi32.CredWriteW(ctypes.byref(credential), 0):
            raise OSError(ctypes.get_last_error(), "credential write failed")

    def read(self, key: str) -> str | None:
        pointer = ctypes.POINTER(_CREDENTIAL)()
        if not self._advapi32.CredReadW(
            self._target(key), CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)
        ):
            error = ctypes.get_last_error()
            if error == 1168:
                return None
            raise OSError(error, "credential read failed")
        try:
            size = int(pointer.contents.CredentialBlobSize)
            blob = ctypes.string_at(pointer.contents.CredentialBlob, size)
            return blob.decode("utf-16-le")
        finally:
            self._advapi32.CredFree(pointer)

    def delete(self, key: str) -> bool:
        if self._advapi32.CredDeleteW(
            self._target(key), CRED_TYPE_GENERIC, 0
        ):
            return True
        error = ctypes.get_last_error()
        if error == 1168:
            return False
        raise OSError(error, "credential deletion failed")


@dataclass(slots=True)
class MemoryCredentialStore:
    _values: dict[str, str]

    def __init__(self) -> None:
        self._values = {}

    def write(self, key: str, value: str) -> None:
        if not value:
            raise ValueError("credential value must not be empty")
        self._values[key] = value

    def read(self, key: str) -> str | None:
        return self._values.get(key)

    def delete(self, key: str) -> bool:
        return self._values.pop(key, None) is not None


PROVIDER_ENVIRONMENT_KEYS = {
    "tushare": "TUSHARE_TOKEN",
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
    "longcat": "LONGCAT_API_KEY",
    "mimo": "XIAOMI_API_KEY",
    "wecom": "WECOM_SECRET",
}


def child_process_environment(
    store: CredentialStore,
    configured_providers: list[str],
    *,
    base: dict[str, str] | None = None,
) -> dict[str, str]:
    environment = dict(os.environ if base is None else base)
    for provider in configured_providers:
        variable = PROVIDER_ENVIRONMENT_KEYS.get(provider)
        if not variable:
            continue
        value = store.read(provider)
        if value:
            environment[variable] = value
    return environment
