"""Access control: modules, permissions, roles, users and per-user exceptions.

Single company (Transvalor): there is no tenant column anywhere.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    func,
    select,
)
from sqlalchemy.orm import Mapped, column_property, deferred, mapped_column, relationship

from app.models.base import UUID_TYPE, Base, IdMixin, TimestampMixin


class Module(IdMixin, TimestampMixin, Base):
    """Governs the side menu. Seed-only: the API can configure it, never create it."""

    __tablename__ = "module"

    code: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    route: Mapped[str | None] = mapped_column(String(120))
    icon: Mapped[str | None] = mapped_column(String(40))
    # A permission code, not a foreign key: derived permissions come and go.
    menu_permission: Mapped[str | None] = mapped_column(String(120))
    show_in_menu: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Permission(IdMixin, TimestampMixin, Base):
    """Closed catalog. Seeded; the only runtime additions are the permissions derived
    from a ticket type, created and removed by the backend itself."""

    __tablename__ = "permission"

    code: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    module_id: Mapped[UUID] = mapped_column(
        UUID_TYPE, ForeignKey("module.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    module: Mapped[Module] = relationship(Module, lazy="selectin")

    @property
    def module_code(self) -> str:
        return self.module.code


Module.permissions_count = column_property(  # type: ignore[attr-defined]
    select(func.count(Permission.id))
    .where(Permission.module_id == Module.id)
    .correlate_except(Permission)
    .scalar_subquery()
)

role_permission = Table(
    "role_permission",
    Base.metadata,
    Column("role_id", UUID_TYPE, ForeignKey("role.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "permission_id",
        UUID_TYPE,
        ForeignKey("permission.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Role(IdMixin, TimestampMixin, Base):
    __tablename__ = "role"

    code: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # The superuser role: its permission links are kept in sync with the whole catalog.
    has_all_permissions: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    permissions: Mapped[list[Permission]] = relationship(
        Permission,
        secondary=role_permission,
        lazy="selectin",
        order_by=Permission.code,
    )

    __table_args__ = (Index("uq_role_name_lower", func.lower(name), unique=True),)

    @property
    def permission_codes(self) -> list[str]:
        return [p.code for p in self.permissions if p.is_active]


class AppUser(IdMixin, TimestampMixin, Base):
    __tablename__ = "app_user"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(40))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role_id: Mapped[UUID] = mapped_column(
        UUID_TYPE, ForeignKey("role.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # Code of an item of the `aircrafts` / `locations` catalogs.
    aircraft_id: Mapped[str | None] = mapped_column(String(60))
    location: Mapped[str | None] = mapped_column(String(60))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    # Data URL of the signature image. Deferred: it is only read by the signature endpoints.
    signature_url: Mapped[str | None] = deferred(mapped_column(Text))
    signature_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Tokens issued before this instant are revoked (logout, password change, deactivation).
    tokens_valid_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    role: Mapped[Role] = relationship(Role, lazy="selectin")
    permission_overrides: Mapped[list["UserPermissionOverride"]] = relationship(
        "UserPermissionOverride",
        lazy="selectin",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def role_name(self) -> str:
        return self.role.name

    @property
    def role_code(self) -> str:
        return self.role.code

    @property
    def has_signature(self) -> bool:
        return self.signature_updated_at is not None


class UserPermissionOverride(Base):
    """A permission granted to, or revoked from, one user on top of their role."""

    __tablename__ = "user_permission_override"

    user_id: Mapped[UUID] = mapped_column(
        UUID_TYPE, ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[UUID] = mapped_column(
        UUID_TYPE, ForeignKey("permission.id", ondelete="CASCADE"), primary_key=True
    )
    effect: Mapped[str] = mapped_column(String(10), nullable=False)

    permission: Mapped[Permission] = relationship(Permission, lazy="selectin")

    __table_args__ = (CheckConstraint("effect IN ('grant', 'revoke')", name="effect_valid"),)


Role.users_count = column_property(  # type: ignore[attr-defined]
    select(func.count(AppUser.id))
    .where(AppUser.role_id == Role.id)
    .correlate_except(AppUser)
    .scalar_subquery()
)
