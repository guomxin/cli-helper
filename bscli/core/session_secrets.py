from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Protocol
from uuid import uuid4


class SessionSecretError(RuntimeError):
    pass


class SessionStateAccessDenied(SessionSecretError):
    """The encrypted state is not usable by the current security context."""


class SessionStateProtector(Protocol):
    def protect(self, plaintext: bytes, *, context: bytes) -> bytes: ...

    def unprotect(self, ciphertext: bytes, *, context: bytes) -> bytes: ...


class SessionStateStore:
    def __init__(
        self,
        root: Path | str,
        *,
        protector: SessionStateProtector | None = None,
    ) -> None:
        self.root = Path(root)
        self.protector = protector or _default_protector()

    def save(self, session_id: str, state: dict) -> None:
        _validate_state(state)
        plaintext = json.dumps(
            state,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        ciphertext = self.protector.protect(plaintext, context=session_id.encode("utf-8"))
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path_for(session_id)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(ciphertext)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            raise SessionSecretError("could not persist encrypted session state") from exc
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def load(self, session_id: str) -> dict | None:
        path = self.path_for(session_id)
        if not path.exists():
            return None
        try:
            ciphertext = path.read_bytes()
            plaintext = self.protector.unprotect(
                ciphertext,
                context=session_id.encode("utf-8"),
            )
            state = json.loads(plaintext.decode("utf-8"))
        except SessionSecretError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SessionSecretError("encrypted session state could not be loaded") from exc
        _validate_state(state)
        return state

    def delete(self, session_id: str) -> None:
        try:
            self.path_for(session_id).unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise SessionSecretError("encrypted session state could not be deleted") from exc

    def path_for(self, session_id: str) -> Path:
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        return self.root / f"{digest}.bin"


class WindowsDpapiProtector:
    def protect(self, plaintext: bytes, *, context: bytes) -> bytes:
        return _windows_dpapi(plaintext, context=context, protect=True)

    def unprotect(self, ciphertext: bytes, *, context: bytes) -> bytes:
        return _windows_dpapi(ciphertext, context=context, protect=False)


def _default_protector() -> SessionStateProtector:
    if os.name == "nt":
        return WindowsDpapiProtector()
    raise SessionSecretError(
        "no session-state protector is configured for this operating system"
    )


def _validate_state(state: object) -> None:
    if not isinstance(state, dict):
        raise SessionSecretError("session state must be an object")
    cookies = state.get("cookies")
    if not isinstance(cookies, list) or not all(isinstance(cookie, dict) for cookie in cookies):
        raise SessionSecretError("session state must contain a cookie list")


def _windows_dpapi(data: bytes, *, context: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise SessionSecretError("Windows DPAPI is unavailable on this operating system")

    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD),
            ("data", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    def blob(value: bytes) -> tuple[DataBlob, object]:
        buffer = (ctypes.c_ubyte * max(len(value), 1))()
        if value:
            ctypes.memmove(buffer, value, len(value))
        return DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    input_blob, input_buffer = blob(data)
    entropy_blob, entropy_buffer = blob(context)
    output_blob = DataBlob()
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN

    if protect:
        function = crypt32.CryptProtectData
        function.argtypes = [
            ctypes.POINTER(DataBlob),
            wintypes.LPCWSTR,
            ctypes.POINTER(DataBlob),
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(DataBlob),
        ]
        function.restype = wintypes.BOOL
        ok = function(
            ctypes.byref(input_blob),
            "AgentBridge session state",
            ctypes.byref(entropy_blob),
            None,
            None,
            flags,
            ctypes.byref(output_blob),
        )
    else:
        function = crypt32.CryptUnprotectData
        function.argtypes = [
            ctypes.POINTER(DataBlob),
            wintypes.LPVOID,
            ctypes.POINTER(DataBlob),
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(DataBlob),
        ]
        function.restype = wintypes.BOOL
        ok = function(
            ctypes.byref(input_blob),
            None,
            ctypes.byref(entropy_blob),
            None,
            None,
            flags,
            ctypes.byref(output_blob),
        )

    _ = input_buffer, entropy_buffer
    if not ok:
        error = ctypes.get_last_error()
        if not protect and error in {13, -2146893813, 2148073483}:
            raise SessionStateAccessDenied(
                "encrypted session state is not accessible to the current "
                "Windows security principal"
            )
        raise SessionSecretError(f"Windows DPAPI operation failed with code {error}")
    try:
        return ctypes.string_at(output_blob.data, output_blob.size)
    finally:
        kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        kernel32.LocalFree.restype = wintypes.HLOCAL
        kernel32.LocalFree(output_blob.data)
