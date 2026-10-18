"""Telegram authorization and permission verification."""

from enum import Enum
from typing import Optional
from kerniq.config import get_users

class PermissionLevel(str, Enum):
    NOTIFICATIONS = "notifications"
    READONLY = "readonly"
    CONTROL = "control"

def get_user_permission(user_id: int) -> Optional[PermissionLevel]:
    """Retrieve the effective permission level for a Telegram numeric user ID.
    
    Returns None if the user is not authorized.
    """
    try:
        numeric_id = int(user_id)
    except (ValueError, TypeError):
        return None

    users = get_users()
    for u in users:
        if int(u.get("user_id", -1)) == numeric_id:
            perm_str = u.get("permission")
            try:
                return PermissionLevel(perm_str)
            except ValueError:
                return None
    return None

def is_authorized(user_id: int) -> bool:
    """Return True if user is configured as an authorized account."""
    return get_user_permission(user_id) is not None

def can_receive_notifications(user_id: int) -> bool:
    """Check if user can receive broadcast notifications."""
    perm = get_user_permission(user_id)
    return perm in (PermissionLevel.NOTIFICATIONS, PermissionLevel.READONLY, PermissionLevel.CONTROL)

def can_read(user_id: int) -> bool:
    """Check if user has permission to inspect tasks, history, status, or output."""
    perm = get_user_permission(user_id)
    return perm in (PermissionLevel.READONLY, PermissionLevel.CONTROL)

def can_control(user_id: int) -> bool:
    """Check if user has permission to terminate tasks."""
    perm = get_user_permission(user_id)
    return perm == PermissionLevel.CONTROL
