"""Unit tests for configuration, secret storage, and user limits."""

import os
import tempfile
from pathlib import Path
import pytest

from kerniq.config import (
    DEFAULT_RULES,
    MAX_AUTHORIZED_USERS,
    add_user,
    delete_telegram_token,
    get_base_dir,
    get_rules,
    get_telegram_token,
    get_users,
    load_config,
    remove_user,
    save_config,
    set_telegram_token,
    update_rules,
    validate_config,
)
from kerniq.exceptions import ConfigError

@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """Isolate storage and config directory for every test."""
    monkeypatch.setenv("KERNIQ_HOME", str(tmp_path))
    yield tmp_path

def test_default_config_loading():
    cfg = load_config()
    assert cfg["version"] == 1
    assert cfg["users"] == []
    assert cfg["rules"]["task_completed"] is True
    assert cfg["rules"]["task_input_required"] is True
    assert cfg["rules"]["cpu_threshold"] is False

def test_secret_token_storage_and_retrieval():
    test_token = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ_01234567"
    set_telegram_token(test_token)
    assert get_telegram_token() == test_token

    delete_telegram_token()
    assert get_telegram_token() is None

def test_token_cannot_be_empty():
    with pytest.raises(ConfigError):
        set_telegram_token("   ")

def test_user_limit_enforced():
    # Add up to MAX_AUTHORIZED_USERS (3)
    add_user(1001, "readonly")
    add_user(1002, "control")
    add_user(1003, "notifications")

    users = get_users()
    assert len(users) == 3

    # Attempting to add 4th user must raise ConfigError
    with pytest.raises(ConfigError) as exc_info:
        add_user(1004, "readonly")
    assert f"maximum limit of {MAX_AUTHORIZED_USERS}" in str(exc_info.value)

def test_user_id_validation():
    with pytest.raises(ConfigError):
        add_user(-5, "readonly")

    with pytest.raises(ConfigError):
        add_user("not_a_number", "readonly")

    with pytest.raises(ConfigError):
        add_user(1005, "invalid_permission")

def test_user_removal():
    add_user(2001, "control")
    assert len(get_users()) == 1

    removed = remove_user(2001)
    assert removed is True
    assert len(get_users()) == 0

    assert remove_user(9999) is False

def test_user_permission_update():
    add_user(3001, "readonly")
    assert get_users()[0]["permission"] == "readonly"

    add_user(3001, "control")
    users = get_users()
    assert len(users) == 1
    assert users[0]["permission"] == "control"

def test_invalid_config_preserves_existing():
    cfg = load_config()
    cfg["users"].append({"user_id": 111, "permission": "control"})
    save_config(cfg)

    # Attempt to save invalid config (e.g. duplicate user IDs)
    invalid_cfg = load_config()
    invalid_cfg["users"].append({"user_id": 111, "permission": "readonly"})

    with pytest.raises(ConfigError):
        save_config(invalid_cfg)

    # Valid config should remain intact
    reloaded = load_config()
    assert len(reloaded["users"]) == 1

def test_rule_updates():
    rules = update_rules({"cpu_threshold": True, "cpu_percent": 75.5, "cpu_cooldown_seconds": 60})
    assert rules["cpu_threshold"] is True
    assert rules["cpu_percent"] == 75.5
    assert rules["cpu_cooldown_seconds"] == 60

    active_rules = get_rules()
    assert active_rules["cpu_percent"] == 75.5
