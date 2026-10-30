"""Users, server sessions, conversations, messages and durable generation queue."""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table("login_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_login_sessions_user_id", "login_sessions", ["user_id"])
    op.create_index("ix_login_sessions_expires_at", "login_sessions", ["expires_at"])
    op.create_table("conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_conversations_user_updated", "conversations", ["user_id", sa.text("updated_at DESC"), "id"])
    op.create_table("generations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("answer", sa.Text()),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(40)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('pending','running','succeeded','failed')", name="ck_generation_status"),
        sa.CheckConstraint("mode IN ('ask','plan','summary','decision')", name="ck_generation_mode"),
        sa.CheckConstraint("status != 'succeeded' OR answer IS NOT NULL", name="ck_generation_answer"),
        sa.CheckConstraint("status != 'failed' OR (error_code IS NOT NULL AND error_message IS NOT NULL)", name="ck_generation_error"),
        sa.CheckConstraint("status != 'running' OR (started_at IS NOT NULL AND lease_until IS NOT NULL)", name="ck_generation_lease"),
        sa.CheckConstraint("char_length(question) BETWEEN 3 AND 6000 AND char_length(context) <= 12000", name="ck_generation_input"),
        sa.CheckConstraint("duration_ms >= 0", name="ck_generation_duration"),
    )
    op.create_index("ix_generations_user_id", "generations", ["user_id"])
    op.create_index("ix_generations_queue", "generations", ["status", "created_at"])
    op.create_index("ix_generations_conversation", "generations", ["conversation_id", "created_at"])
    op.create_index("uq_generation_active_conversation", "generations", ["conversation_id"], unique=True,
                    postgresql_where=sa.text("status IN ('pending','running')"))
    op.create_table("messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("generation_id", sa.Uuid(), sa.ForeignKey("generations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('user','assistant')", name="ck_message_role"),
        sa.UniqueConstraint("generation_id", "role", name="uq_message_generation_role"),
    )
    op.create_index("ix_messages_conversation_created", "messages", ["conversation_id", "created_at", "id"])
    op.create_table("rate_buckets",
        sa.Column("key", sa.String(150), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_rate_buckets_expires_at", "rate_buckets", ["expires_at"])
    op.create_table("worker_heartbeats",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    for name in ("worker_heartbeats", "rate_buckets", "messages", "generations",
                 "conversations", "login_sessions", "users"):
        op.drop_table(name)
