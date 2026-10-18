"""Unit tests for Telegram authorization and permission verification."""

import pytest
from kerniq.authorization import (
    PermissionLevel,
    can_control,
    can_read,
    can_receive_notifications,
    get_user_permission,
    is_authorized,
)
from kerniq.config import add_user

@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    monkeypatch.setenv("KERNIQ_HOME", str(tmp_path))
    yield tmp_path

def test_unauthorized_user_denied():
    assert is_authorized(999999) is False
    assert get_user_permission(999999) is None
    assert can_receive_notifications(999999) is False
    assert can_read(999999) is False
    assert can_control(999999) is False

def test_notifications_only_tier():
    add_user(1001, "notifications")

    assert is_authorized(1001) is True
    assert get_user_permission(1001) == PermissionLevel.NOTIFICATIONS
    assert can_receive_notifications(1001) is True
    assert can_read(1001) is False
    assert can_control(1001) is False

def test_readonly_tier():
    add_user(1002, "readonly")

    assert is_authorized(1002) is True
    assert get_user_permission(1002) == PermissionLevel.READONLY
    assert can_receive_notifications(1002) is True
    assert can_read(1002) is True
    assert can_control(1002) is False

def test_control_tier():
    add_user(1003, "control")

    assert is_authorized(1003) is True
    assert get_user_permission(1003) == PermissionLevel.CONTROL
    assert can_receive_notifications(1003) is True
    assert can_read(1003) is True
    assert can_control(1003) is True
