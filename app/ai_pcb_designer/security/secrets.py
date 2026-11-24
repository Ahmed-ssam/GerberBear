"""API key storage in the OS keychain (keyring). If no keychain backend is available the
key is kept in memory for this session only, and `backend_status()` says so - it is
never written to settings files or logs."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)
SERVICE = "AIPCBDesigner"
_memory: dict[str, str] = {}


def _keyring():
    try:
        import keyring
        from keyring.backends import fail
        kr = keyring.get_keyring()
        if isinstance(kr, fail.Keyring) or "fail" in type(kr).__module__ or "null" in type(kr).__module__.lower():
            return None
        return keyring
    except Exception:
        return None


def backend_status() -> tuple[bool, str]:
    kr = _keyring()
    if kr is None:
        return False, "No OS keychain available: keys are kept in memory for this session only."
    return True, f"Stored in the OS keychain ({type(kr.get_keyring()).__name__})."


def set_key(provider: str, value: str | None):
    kr = _keyring()
    name = f"{provider}_api_key"
    if not value:
        _memory.pop(name, None)
        if kr:
            try:
                kr.delete_password(SERVICE, name)
            except Exception:
                pass
        return
    if kr:
        kr.set_password(SERVICE, name, value)
    else:
        _memory[name] = value


def get_key(provider: str) -> str | None:
    name = f"{provider}_api_key"
    kr = _keyring()
    if kr:
        try:
            v = kr.get_password(SERVICE, name)
            if v:
                return v
        except Exception as e:
            log.warning("keychain read failed: %s", type(e).__name__)
    return _memory.get(name)
