"""Role and permission definitions.

Roles are stored on the user row as a plain string; permissions are derived
from the role at request time. Keeping the mapping in code (rather than a
join table) means permission changes ship with a deploy and are reviewable
in git, which is what an audited school system wants.
"""

from enum import StrEnum


class Role(StrEnum):
    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"
    PRINCIPAL = "PRINCIPAL"
    REGISTRAR = "REGISTRAR"
    TEACHER = "TEACHER"
    STUDENT = "STUDENT"
    PARENT = "PARENT"
    IT_ADMIN = "IT_ADMIN"


class Permission(StrEnum):
    AI_CHAT = "ai.chat"
    AI_IMAGE = "ai.image"
    AI_KNOWLEDGE = "ai.knowledge"
    KNOWLEDGE_UPLOAD = "knowledge.upload"
    USERS_READ = "users.read"
    USERS_WRITE = "users.write"
    REPORTS_READ = "reports.read"
    CREDITS_MANAGE = "credits.manage"
    SYSTEM_SETTINGS = "system.settings"
    AUDIT_READ = "audit.read"


_ALL = set(Permission)

ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.SUPER_ADMIN: _ALL,
    Role.IT_ADMIN: _ALL - {Permission.CREDITS_MANAGE},
    Role.ADMIN: {
        Permission.AI_CHAT,
        Permission.AI_IMAGE,
        Permission.AI_KNOWLEDGE,
        Permission.KNOWLEDGE_UPLOAD,
        Permission.USERS_READ,
        Permission.USERS_WRITE,
        Permission.REPORTS_READ,
        Permission.CREDITS_MANAGE,
        Permission.AUDIT_READ,
    },
    Role.PRINCIPAL: {
        Permission.AI_CHAT,
        Permission.AI_IMAGE,
        Permission.AI_KNOWLEDGE,
        Permission.KNOWLEDGE_UPLOAD,
        Permission.USERS_READ,
        Permission.REPORTS_READ,
    },
    Role.REGISTRAR: {
        Permission.AI_CHAT,
        Permission.AI_KNOWLEDGE,
        Permission.KNOWLEDGE_UPLOAD,
        Permission.USERS_READ,
        Permission.REPORTS_READ,
    },
    Role.TEACHER: {
        Permission.AI_CHAT,
        Permission.AI_IMAGE,
        Permission.AI_KNOWLEDGE,
        Permission.KNOWLEDGE_UPLOAD,
    },
    Role.STUDENT: {
        Permission.AI_CHAT,
        Permission.AI_KNOWLEDGE,
    },
    Role.PARENT: {
        Permission.AI_CHAT,
    },
}


def permissions_for(role: str) -> set[Permission]:
    try:
        return ROLE_PERMISSIONS.get(Role(role), set())
    except ValueError:
        return set()


def has_permission(role: str, permission: Permission) -> bool:
    return permission in permissions_for(role)
