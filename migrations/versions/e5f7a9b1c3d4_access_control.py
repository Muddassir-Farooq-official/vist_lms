"""access control: custom roles, per-person access with end dates, access history, four permissions

Production impact: additive only. Adds two columns to roles (is_builtin, default true for every existing role, and an
empty description) and three new, empty tables: role_permission_removals (a right an administrator took away from a
built-in role, so later deploys do not put it back), user_access_overrides (a right given to or taken from one person,
optionally until a date) and access_changes (the history of access changes, for undo). Adds permissions held as before:
Administrator only: backup.manage (the Backups page, already Administrator-only) and system.health (server health,
data check, morning summary, semester rollover tools, already Administrator-only), and student.lookup (see any student's
record) for the five offices that could already do so by role (Administrator, Accounts, Principal, HOD, Exam
Controller), and fee.office (every student's fee account) for Administrator and Accounts, who could already do so by
role. Nobody gains or loses any access on the day of the update. Downgrade drops the new tables, columns and the four permissions.

Revision ID: e5f7a9b1c3d4
Revises: d4e6f8a0b2c3
Create Date: 2026-11-27 20:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = 'e5f7a9b1c3d4'
down_revision = 'd4e6f8a0b2c3'
branch_labels = None
depends_on = None

PERMS = (
    ("backup.manage", "Backups: back up now, download a copy of the database or files", ["ADMIN"]),
    ("system.health", "Server health, data check, morning summary and semester rollover", ["ADMIN"]),
    # the offices that could already open any student's record (by role) now do so by this right, so it can be given
    ("student.lookup", "See any student's record (read-only)", ["ADMIN", "ACCOUNTS_OFFICER", "PRINCIPAL", "HOD", "EXAM_CONTROLLER"]),
    # the fee office's institute-wide lists and accounts (Administrator and Accounts, as before by role)
    ("fee.office", "Every student's fee account and the fee office's lists", ["ADMIN", "ACCOUNTS_OFFICER"]),
)


def upgrade() -> None:
    op.add_column('roles', sa.Column('is_builtin', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('roles', sa.Column('description', sa.String(300), nullable=True))

    op.create_table(
        'role_permission_removals',
        sa.Column('role_id', sa.Uuid(), sa.ForeignKey('roles.id', ondelete='CASCADE', name='fk_role_permission_removals_role_id_roles'), nullable=False),
        sa.Column('permission_id', sa.Uuid(), sa.ForeignKey('permissions.id', ondelete='CASCADE', name='fk_role_permission_removals_permission_id_permissions'), nullable=False),
        sa.Column('removed_by', sa.Uuid(), sa.ForeignKey('users.id', ondelete='SET NULL', name='fk_role_permission_removals_removed_by_users'), nullable=True),
        sa.Column('removed_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('role_id', 'permission_id', name='pk_role_permission_removals'),
    )
    op.create_table(
        'user_access_overrides',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='CASCADE', name='fk_user_access_overrides_user_id_users'), nullable=False),
        sa.Column('permission_code', sa.String(64), nullable=False),
        sa.Column('effect', sa.String(8), nullable=False),
        sa.Column('until', sa.Date(), nullable=True),
        sa.Column('reason', sa.String(300), nullable=True),
        sa.Column('reminded_on', sa.Date(), nullable=True),
        sa.Column('created_by', sa.Uuid(), sa.ForeignKey('users.id', ondelete='SET NULL', name='fk_user_access_overrides_created_by_users'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_user_access_overrides'),
        sa.UniqueConstraint('user_id', 'permission_code', name='uq_user_access_overrides_user_permission'),
        sa.CheckConstraint("effect IN ('GRANT', 'DENY')", name='ck_user_access_overrides_effect'),
    )
    op.create_table(
        'access_changes',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('kind', sa.String(40), nullable=False),
        sa.Column('target_type', sa.String(10), nullable=False),
        sa.Column('target_id', sa.Uuid(), nullable=True),
        sa.Column('target_label', sa.String(200), nullable=False),
        sa.Column('summary', sa.String(500), nullable=False),
        sa.Column('before', sa.JSON(), nullable=True),
        sa.Column('after', sa.JSON(), nullable=True),
        sa.Column('actor_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='SET NULL', name='fk_access_changes_actor_id_users'), nullable=True),
        sa.Column('approval_request_id', sa.Uuid(), nullable=True),
        sa.Column('undo_of', sa.Uuid(), nullable=True),
        sa.Column('undone_by', sa.Uuid(), sa.ForeignKey('users.id', ondelete='SET NULL', name='fk_access_changes_undone_by_users'), nullable=True),
        sa.Column('undone_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_access_changes'),
    )
    op.create_index('ix_access_changes_created_at', 'access_changes', ['created_at'])
    op.create_index('ix_access_changes_target', 'access_changes', ['target_type', 'target_id'])

    for code, description, roles in PERMS:
        op.execute(sa.text("INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), :c, :d, now(), now() "
                           "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :c)").bindparams(c=code, d=description))
        for role in roles:
            op.execute(sa.text("INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p "
                               "WHERE r.code = :r AND p.code = :c AND NOT EXISTS "
                               "(SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)").bindparams(r=role, c=code))


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('backup.manage', 'system.health', 'student.lookup', 'fee.office'))")
    op.execute("DELETE FROM permissions WHERE code IN ('backup.manage', 'system.health', 'student.lookup', 'fee.office')")
    op.drop_index('ix_access_changes_target', table_name='access_changes')
    op.drop_index('ix_access_changes_created_at', table_name='access_changes')
    op.drop_table('access_changes')
    op.drop_table('user_access_overrides')
    op.drop_table('role_permission_removals')
    op.drop_column('roles', 'description')
    op.drop_column('roles', 'is_builtin')
