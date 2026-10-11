"""pm_core —— 账号密码管理程序的核心层（无 UI 依赖，可被桌面/移动端共用）。"""

from .crypto import (  # noqa: F401
    CryptoError,
    TamperError,
    WrongPasswordError,
    default_kdf_params,
    derive_kek,
    new_recovery_key,
    normalize_recovery_key,
    password_strength,
    subkey,
)
from .models import Credential, VaultHeader, FORMAT_NAME, FORMAT_VERSION  # noqa: F401
from .generator import DEFAULT_OPTIONS, build_alphabet, generate_password  # noqa: F401
from .vault import Vault, VaultError, VaultFormatError, VaultLockedError  # noqa: F401
from . import sync  # noqa: F401

__all__ = [
    "CryptoError",
    "TamperError",
    "WrongPasswordError",
    "Credential",
    "VaultHeader",
    "Vault",
    "VaultError",
    "VaultFormatError",
    "VaultLockedError",
    "sync",
    "generate_password",
    "build_alphabet",
    "DEFAULT_OPTIONS",
    "password_strength",
    "new_recovery_key",
    "normalize_recovery_key",
]
