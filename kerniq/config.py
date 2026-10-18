"""Configuration management, secret storage, and path resolution for Kerniq."""

import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from kerniq.exceptions import ConfigError

APP_DIR_NAME = ".kerniq"
SERVICE_NAME = "kerniq"
KEYRING_USERNAME = "telegram_bot_token"
MAX_AUTHORIZED_USERS = 3

VALID_PERMISSIONS = {"notifications", "readonly", "control"}

RULE_DEFINITIONS = [
    ("monitoring_started", "Shell Started"),
    ("monitoring_ended", "Shell Ended"),
    ("monitoring_paused", "Monitoring Paused"),
    ("monitoring_resumed", "Monitoring Resumed"),
    ("task_started", "Task Started"),
    ("task_completed", "Task Completed Successfully"),
    ("task_failed", "Task Failed (Non-zero exit)"),
    ("task_input_required", "Task Requires User Input"),
    ("duration_threshold", "Task Running Since Long Duration"),
    ("cpu_threshold", "Task Consuming High CPU"),
    ("memory_threshold", "Task Consuming High RAM"),
    ("battery_threshold", "Battery Lower Than Threshold"),
]

DEFAULT_RULES = {
    "monitoring_started": True,  # Interactive shell started
    "monitoring_ended": True,    # Interactive shell ended
    "monitoring_paused": True,
    "monitoring_resumed": True,
    "task_started": True,
    "task_completed": True,
    "task_failed": True,
    "task_input_required": True,  # Prompt detection enabled
    "duration_threshold": False,   # Default-disabled
    "duration_seconds": 300,
    "cpu_threshold": False,        # Default-disabled
    "cpu_percent": 80.0,
    "cpu_duration_samples": 3,
    "cpu_cooldown_seconds": 300,
    "battery_threshold": False,    # Default-disabled
    "battery_percent": 20.0,
    "battery_cooldown_seconds": 300,
    "memory_threshold": False,
    "memory_mb": 1024.0,
    "memory_percent": 80.0,
    "memory_duration_samples": 3,
    "memory_cooldown_seconds": 300,
    "sampling_interval_seconds": 2.0,
}

DEFAULT_DEVICE = {
    "name": None,
    "id": None,
    "id_mode": "name",  # "name" or "stable_id"
    "registered_notified": False
}

DEFAULT_ADVANCED = {
    "auto_cleanup_enabled": False,
    "history_retention_days": 30,
    "output_retention_days": 14,
    "max_history_records": 100,
    "export_format": "json"
}

def get_base_dir() -> Path:
    """Return the base storage directory for Kerniq.
    
    Uses KERNIQ_HOME if set, otherwise ~/.kerniq.
    """
    env_path = os.environ.get("KERNIQ_HOME")
    if env_path:
        base = Path(env_path).resolve()
    else:
        base = Path.home() / APP_DIR_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base

def get_db_path() -> Path:
    """Return path to SQLite database."""
    return get_base_dir() / "kerniq.db"

def get_config_path() -> Path:
    """Return path to non-secret configuration file."""
    return get_base_dir() / "config.json"

def get_credentials_path() -> Path:
    """Return path to fallback credential file."""
    return get_base_dir() / "credentials.json"

def get_output_dir() -> Path:
    """Return path to directory storing stdout/stderr captures."""
    out_dir = get_base_dir() / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir

# ---------------------------------------------------------------------------
# Secret Store Management & Encryption
# ---------------------------------------------------------------------------

_fernet_instance = None
_fernet_key_path = None

def _set_file_permissions_secure(path: Path):
    """Set restrictive file permissions (user read/write only)."""
    try:
        if sys.platform != "win32":
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        else:
            # On Windows, user directory is generally user-private, but we can set read/write
            pass
    except Exception:
        pass

class _FallbackCipher:
    """Pure-Python authenticated cipher fallback when cryptography package is not installed."""
    def __init__(self, key: bytes):
        import hashlib
        self._key = hashlib.sha256(key).digest()

    def encrypt(self, data: bytes) -> bytes:
        import base64
        import hashlib
        import hmac
        import secrets
        nonce = secrets.token_bytes(16)
        keystream = bytearray()
        counter = 0
        while len(keystream) < len(data):
            block = hashlib.sha256(self._key + nonce + counter.to_bytes(4, "big")).digest()
            keystream.extend(block)
            counter += 1
        ciphertext = bytes(b ^ k for b, k in zip(data, keystream[:len(data)]))
        tag = hmac.new(self._key, nonce + ciphertext, hashlib.sha256).digest()
        payload = b"FB1:" + nonce + tag + ciphertext
        return base64.urlsafe_b64encode(payload)

    def decrypt(self, token: bytes) -> bytes:
        import base64
        import hashlib
        import hmac
        try:
            raw = base64.urlsafe_b64decode(token)
            if not raw.startswith(b"FB1:"):
                raise ValueError("Invalid token header")
            payload = raw[4:]
            if len(payload) < 48:
                raise ValueError("Truncated token")
            nonce = payload[:16]
            tag = payload[16:48]
            ciphertext = payload[48:]
            expected_tag = hmac.new(self._key, nonce + ciphertext, hashlib.sha256).digest()
            if not hmac.compare_digest(tag, expected_tag):
                raise ValueError("Authentication tag mismatch")
            keystream = bytearray()
            counter = 0
            while len(keystream) < len(ciphertext):
                block = hashlib.sha256(self._key + nonce + counter.to_bytes(4, "big")).digest()
                keystream.extend(block)
                counter += 1
            return bytes(b ^ k for b, k in zip(ciphertext, keystream[:len(ciphertext)]))
        except Exception as e:
            raise ValueError(f"Decryption failed: {e}")

def _get_fernet() -> Any:
    """Retrieve or initialize the machine-bound Fernet cipher with fallback."""
    global _fernet_instance, _fernet_key_path
    key_file = get_base_dir() / ".key"
    if _fernet_instance is not None and _fernet_key_path == key_file and key_file.exists():
        return _fernet_instance

    FernetClass = None
    try:
        import importlib
        fernet_mod = importlib.import_module("cryptography.fernet")
        FernetClass = getattr(fernet_mod, "Fernet", None)
    except Exception:
        FernetClass = None

    if key_file.exists():
        try:
            key = key_file.read_bytes().strip()
            if FernetClass is not None:
                _fernet_instance = FernetClass(key)
            else:
                _fernet_instance = _FallbackCipher(key)
            _fernet_key_path = key_file
            return _fernet_instance
        except Exception:
            pass

    import secrets
    if FernetClass is not None:
        key = FernetClass.generate_key()
    else:
        import base64
        key = base64.urlsafe_b64encode(secrets.token_bytes(32))

    try:
        key_file.write_bytes(key)
        _set_file_permissions_secure(key_file)
    except Exception:
        pass

    if FernetClass is not None:
        _fernet_instance = FernetClass(key)
    else:
        _fernet_instance = _FallbackCipher(key)
    _fernet_key_path = key_file
    return _fernet_instance

def encrypt_str(plain_text: str) -> str:
    """Encrypt a string using the machine-bound Fernet or fallback key."""
    if not plain_text:
        return ""
    f = _get_fernet()
    return f.encrypt(plain_text.encode("utf-8")).decode("utf-8")

def decrypt_str(cipher_text: str) -> str:
    """Decrypt a ciphertext string using the machine-bound Fernet or fallback key."""
    if not cipher_text:
        return ""
    f = _get_fernet()
    try:
        return f.decrypt(cipher_text.encode("utf-8")).decode("utf-8")
    except Exception:
        try:
            key_file = get_base_dir() / ".key"
            if key_file.exists():
                fallback = _FallbackCipher(key_file.read_bytes().strip())
                return fallback.decrypt(cipher_text.encode("utf-8")).decode("utf-8")
        except Exception:
            pass
        raise

def get_telegram_token() -> Optional[str]:
    """Retrieve the Telegram bot token securely.
    
    Tries system keyring first; falls back to encrypted credentials.json.
    """
    # 1. Try keyring
    try:
        import keyring
        token = keyring.get_password(SERVICE_NAME, KEYRING_USERNAME)
        if token and token.strip():
            return token.strip()
    except Exception:
        pass

    # 2. Try encrypted credentials.json fallback
    cred_file = get_credentials_path()
    if cred_file.exists():
        try:
            with open(cred_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "telegram_bot_token_enc" in data:
                    dec = decrypt_str(data["telegram_bot_token_enc"])
                    if dec and dec.strip():
                        return dec.strip()
                elif "telegram_bot_token" in data:
                    token = data.get("telegram_bot_token")
                    if token and str(token).strip():
                        cleaned = str(token).strip()
                        # Migrate to encrypted format
                        try:
                            set_telegram_token(cleaned)
                        except Exception:
                            pass
                        return cleaned
        except Exception:
            pass

    return None

def set_telegram_token(token: str) -> None:
    """Store the Telegram bot token securely.
    
    Token is never stored unencrypted on disk. Stored in system keyring
    and encrypted credentials.json fallback.
    """
    cleaned = token.strip()
    if not cleaned:
        raise ConfigError("Bot token cannot be empty.")

    keyring_success = False
    try:
        import keyring
        keyring.set_password(SERVICE_NAME, KEYRING_USERNAME, cleaned)
        keyring_success = True
    except Exception:
        keyring_success = False

    # Store encrypted in credentials.json
    cred_file = get_credentials_path()
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(dir=get_base_dir(), prefix="cred_", suffix=".tmp")
        with open(tmp_fd, "w", encoding="utf-8") as f:
            json.dump({"telegram_bot_token_enc": encrypt_str(cleaned)}, f)
        _set_file_permissions_secure(Path(tmp_path))
        shutil.move(tmp_path, cred_file)
        _set_file_permissions_secure(cred_file)
    except Exception as e:
        if not keyring_success:
            raise ConfigError(f"Failed to persist bot token to secure credential store: {e}")

def delete_telegram_token() -> None:
    """Remove stored Telegram bot token from secret stores."""
    try:
        import keyring
        keyring.delete_password(SERVICE_NAME, KEYRING_USERNAME)
    except Exception:
        pass

    cred_file = get_credentials_path()
    if cred_file.exists():
        try:
            cred_file.unlink()
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Non-secret Configuration Management
# ---------------------------------------------------------------------------

def load_config() -> Dict[str, Any]:
    """Load configuration from config.json, returning defaults if missing."""
    import socket
    import uuid
    cfg_file = get_config_path()
    default_dev_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, socket.gethostname()))
    default_dev = {
        "name": socket.gethostname(),
        "id": default_dev_id,
        "id_mode": "name",
        "registered_notified": False
    }

    if not cfg_file.exists():
        return {
            "version": 1,
            "telegram": {
                "bot_id": None,
                "bot_username": None,
                "verified_at": None,
                "status": "not_configured"
            },
            "users": [],
            "rules": dict(DEFAULT_RULES),
            "device": default_dev,
            "advanced": dict(DEFAULT_ADVANCED)
        }

    try:
        with open(cfg_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise ConfigError(f"Failed to read configuration file: {e}")

    # Ensure all required structures and default rules are present
    if "telegram" not in data:
        data["telegram"] = {"status": "not_configured"}
    if "users" not in data or not isinstance(data["users"], list):
        data["users"] = []
    if "rules" not in data or not isinstance(data["rules"], dict):
        data["rules"] = {}

    # Transparently decrypt user IDs from user_id_enc
    for u in data["users"]:
        if isinstance(u, dict):
            if "user_id_enc" in u and "user_id" not in u:
                try:
                    u["user_id"] = int(decrypt_str(u["user_id_enc"]))
                except Exception:
                    pass
            elif "user_id" in u:
                try:
                    u["user_id"] = int(u["user_id"])
                except Exception:
                    pass
            if "nickname" not in u:
                u["nickname"] = None

    merged_rules = dict(DEFAULT_RULES)
    merged_rules.update(data.get("rules", {}))
    data["rules"] = merged_rules

    # Merge device info
    dev = dict(default_dev)
    dev.update(data.get("device", {}))
    if not dev.get("name"):
        dev["name"] = socket.gethostname()
    if not dev.get("id"):
        dev["id"] = default_dev_id
    data["device"] = dev

    # Merge advanced settings
    adv = dict(DEFAULT_ADVANCED)
    adv.update(data.get("advanced", {}))
    data["advanced"] = adv

    return data

def save_config(config_data: Dict[str, Any]) -> None:
    """Atomically save configuration to config.json after validation.
    
    Telegram user IDs are never stored unencrypted on disk.
    """
    validate_config(config_data)

    import copy
    to_save = copy.deepcopy(config_data)
    if "users" in to_save and isinstance(to_save["users"], list):
        for u in to_save["users"]:
            if isinstance(u, dict) and "user_id" in u:
                u["user_id_enc"] = encrypt_str(str(u["user_id"]))
                del u["user_id"]

    cfg_file = get_config_path()
    base_dir = get_base_dir()

    tmp_fd, tmp_path = tempfile.mkstemp(dir=base_dir, prefix="cfg_", suffix=".tmp")
    try:
        with open(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(to_save, f, indent=2)
        shutil.move(tmp_path, cfg_file)
    except Exception as e:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        raise ConfigError(f"Failed to save configuration: {e}")

def validate_config(config_data: Dict[str, Any]) -> None:
    """Validate entire configuration schema. Raises ConfigError if invalid."""
    if not isinstance(config_data, dict):
        raise ConfigError("Configuration root must be a dictionary.")

    # 1. Validate users
    users = config_data.get("users", [])
    if not isinstance(users, list):
        raise ConfigError("'users' must be a list.")
    if len(users) > MAX_AUTHORIZED_USERS:
        raise ConfigError(f"At most {MAX_AUTHORIZED_USERS} authorized users can be configured (found {len(users)}).")

    seen_ids = set()
    for user in users:
        if not isinstance(user, dict):
            raise ConfigError("User entry must be an object.")
        user_id = user.get("user_id")
        if user_id is None and "user_id_enc" in user:
            try:
                user_id = int(decrypt_str(user["user_id_enc"]))
                user["user_id"] = user_id
            except Exception:
                raise ConfigError("Failed to decrypt user_id in configuration.")
        if user_id is None:
            raise ConfigError("User entry missing required 'user_id'.")
        try:
            numeric_id = int(user_id)
            if numeric_id <= 0:
                raise ValueError()
        except ValueError:
            raise ConfigError(f"User ID must be a positive integer, got: {user_id}")

        if numeric_id in seen_ids:
            raise ConfigError(f"Duplicate user ID detected: {numeric_id}")
        seen_ids.add(numeric_id)

        perm = user.get("permission")
        if perm not in VALID_PERMISSIONS:
            raise ConfigError(f"Invalid permission '{perm}'. Must be one of {sorted(VALID_PERMISSIONS)}.")

        nickname = user.get("nickname")
        if nickname is not None and not isinstance(nickname, str):
            raise ConfigError("User nickname must be a string or null.")

    # 2. Validate rules
    rules = config_data.get("rules", {})
    if not isinstance(rules, dict):
        raise ConfigError("'rules' must be a dictionary.")

    for num_key in ("cpu_percent", "memory_percent", "battery_percent"):
        val = rules.get(num_key)
        if val is not None:
            try:
                f_val = float(val)
                if not (0.0 <= f_val <= 100.0):
                    raise ValueError()
            except ValueError:
                raise ConfigError(f"Rule '{num_key}' must be a percentage between 0 and 100.")

    for pos_key in ("cpu_duration_samples", "memory_duration_samples", "cpu_cooldown_seconds", "memory_cooldown_seconds", "battery_cooldown_seconds", "duration_seconds"):
        val = rules.get(pos_key)
        if val is not None:
            try:
                i_val = int(val)
                if i_val < 1:
                    raise ValueError()
            except ValueError:
                raise ConfigError(f"Rule '{pos_key}' must be a positive integer.")

    sampling = rules.get("sampling_interval_seconds")
    if sampling is not None:
        try:
            f_val = float(sampling)
            if f_val <= 0.1:
                raise ValueError()
        except ValueError:
            raise ConfigError("sampling_interval_seconds must be a positive number >= 0.1.")

# ---------------------------------------------------------------------------
# User Management Helpers
# ---------------------------------------------------------------------------

def add_user(user_id: int, permission: str, nickname: Optional[str] = None) -> bool:
    """Add or update an authorized user."""
    try:
        numeric_id = int(user_id)
        if numeric_id <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        raise ConfigError(f"Invalid user ID: {user_id}. Must be a positive integer.")

    if permission not in VALID_PERMISSIONS:
        raise ConfigError(f"Invalid permission '{permission}'. Must be one of: {', '.join(sorted(VALID_PERMISSIONS))}")

    clean_nick = nickname.strip() if (nickname and isinstance(nickname, str) and nickname.strip()) else None

    cfg = load_config()
    users = cfg.get("users", [])

    # Check if user already exists
    existing_idx = None
    for i, u in enumerate(users):
        if int(u["user_id"]) == numeric_id:
            existing_idx = i
            break

    is_new = False
    if existing_idx is not None:
        users[existing_idx]["permission"] = permission
        if clean_nick is not None:
            users[existing_idx]["nickname"] = clean_nick
    else:
        if len(users) >= MAX_AUTHORIZED_USERS:
            raise ConfigError(f"Cannot add user: maximum limit of {MAX_AUTHORIZED_USERS} users reached.")
        users.append({"user_id": numeric_id, "nickname": clean_nick, "permission": permission})
        is_new = True

    cfg["users"] = users
    save_config(cfg)
    return is_new

def remove_user(user_id: int) -> bool:
    """Remove an authorized user by ID. Returns True if found and removed."""
    try:
        numeric_id = int(user_id)
    except (ValueError, TypeError):
        raise ConfigError(f"Invalid user ID: {user_id}. Must be an integer.")

    cfg = load_config()
    users = cfg.get("users", [])
    initial_len = len(users)
    users = [u for u in users if int(u["user_id"]) != numeric_id]

    if len(users) == initial_len:
        return False

    cfg["users"] = users
    save_config(cfg)
    return True

def get_users() -> List[Dict[str, Any]]:
    """Return the list of configured authorized users."""
    cfg = load_config()
    return cfg.get("users", [])

def get_rules() -> Dict[str, Any]:
    """Return the active notification and monitoring rules."""
    cfg = load_config()
    return cfg.get("rules", dict(DEFAULT_RULES))

def update_rules(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Update notification and monitoring rules with validation."""
    cfg = load_config()
    rules = cfg.get("rules", dict(DEFAULT_RULES))
    rules.update(updates)
    cfg["rules"] = rules
    save_config(cfg)
    return rules

# ---------------------------------------------------------------------------
# Device & Advanced Configuration Helpers
# ---------------------------------------------------------------------------

def get_device_info() -> Dict[str, Any]:
    """Return configured device identity information."""
    cfg = load_config()
    return cfg.get("device", dict(DEFAULT_DEVICE))

def set_device_info(name: Optional[str] = None, id_mode: Optional[str] = None) -> None:
    """Update device human-readable name or ID mode."""
    cfg = load_config()
    dev = cfg.get("device", dict(DEFAULT_DEVICE))
    if name is not None:
        cleaned = name.strip()
        if not cleaned:
            raise ConfigError("Device name cannot be blank.")
        dev["name"] = cleaned
    if id_mode is not None:
        if id_mode not in ("name", "stable_id"):
            raise ConfigError("Invalid device identity mode. Must be 'name' or 'stable_id'.")
        dev["id_mode"] = id_mode
    cfg["device"] = dev
    save_config(cfg)

def get_effective_device_id() -> str:
    """Return device ID string based on the active identification mode."""
    dev = get_device_info()
    if dev.get("id_mode") == "stable_id":
        return str(dev.get("id", "unknown"))
    return str(dev.get("name", "unknown"))

def is_device_registered_notified() -> bool:
    """Check if the initial device registration notification has been sent."""
    return bool(get_device_info().get("registered_notified", False))

def mark_device_registered_notified() -> None:
    """Mark the device registration notification as sent."""
    cfg = load_config()
    dev = cfg.get("device", dict(DEFAULT_DEVICE))
    dev["registered_notified"] = True
    cfg["device"] = dev
    save_config(cfg)

def get_advanced_config() -> Dict[str, Any]:
    """Return advanced configuration parameters."""
    cfg = load_config()
    adv = dict(DEFAULT_ADVANCED)
    adv.update(cfg.get("advanced", {}))
    return adv

def update_advanced_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Update advanced configuration parameters."""
    cfg = load_config()
    adv = dict(DEFAULT_ADVANCED)
    adv.update(cfg.get("advanced", {}))
    adv.update(updates)
    cfg["advanced"] = adv
    save_config(cfg)
    return adv
