"""pm_core —— 账号密码管理程序的核心层（纯 Python、无 UI 依赖，双端共用）。"""

from .crypto import (  # noqa: F401
    CryptoError,
    KeyfileMismatchError,
    KeyfileRequiredError,
    TamperError,
    UnlockThrottle,
    WrongPasswordError,
    decode_keyfile_qr,
    default_kdf_params,
    derive_kek,
    encode_keyfile_qr,
    keyfile_fingerprint,
    load_keyfile,
    new_keyfile,
    new_recovery_key,
    normalize_recovery_key,
    parse_keyfile_bytes,
    password_strength,
    save_keyfile,
    subkey,
    validate_master_password,
)
from .generator import DEFAULT_OPTIONS, build_alphabet, generate_password  # noqa: F401
from .models import Credential, FORMAT_NAME, FORMAT_VERSION, LEGACY_FORMAT_VERSION, VaultHeader  # noqa: F401
from .registry import VaultRecord, VaultRegistry, default_keyfile_dir  # noqa: F401
from . import qr  # noqa: F401
from . import settings  # noqa: F401
from . import sorting  # noqa: F401
from . import sync  # noqa: F401
from . import totp  # noqa: F401
from .vault import Vault, VaultError, VaultFormatError, VaultLockedError, VaultResetRequiredError  # noqa: F401

__all__ = [
    "CryptoError",
    "KeyfileMismatchError",
    "KeyfileRequiredError",
    "TamperError",
    "UnlockThrottle",
    "WrongPasswordError",
    "Credential",
    "FORMAT_NAME",
    "FORMAT_VERSION",
    "LEGACY_FORMAT_VERSION",
    "VaultHeader",
    "Vault",
    "VaultError",
    "VaultFormatError",
    "VaultLockedError",
    "VaultResetRequiredError",
    "VaultRecord",
    "VaultRegistry",
    "default_keyfile_dir",
    "qr",
    "settings",
    "sorting",
    "sync",
    "totp",
    "generate_password",
    "build_alphabet",
    "DEFAULT_OPTIONS",
    "password_strength",
    "validate_master_password",
    "new_recovery_key",
    "normalize_recovery_key",
    "new_keyfile",
    "save_keyfile",
    "load_keyfile",
    "keyfile_fingerprint",
    "encode_keyfile_qr",
    "decode_keyfile_qr",
]
