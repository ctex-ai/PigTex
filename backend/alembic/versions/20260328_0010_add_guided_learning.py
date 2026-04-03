"""add guided learning schema

Revision ID: 20260328_0010
Revises: 20260314_0009
Create Date: 2026-03-28 16:30:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260328_0010"
down_revision: Union[str, None] = "20260314_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not _table_exists("learning_programs"):
        op.create_table(
            "learning_programs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("workspace_id", sa.String(length=36), sa.ForeignKey("workspaces.id"), nullable=True),
            sa.Column("title", sa.String(length=191), nullable=False),
            sa.Column("topic", sa.String(length=191), nullable=False),
            sa.Column("domain", sa.String(length=64), nullable=False, server_default=sa.text("'general'")),
            sa.Column("goal", sa.Text(), nullable=False),
            sa.Column("outcome_target", sa.Text(), nullable=True),
            sa.Column("current_level", sa.String(length=32), nullable=False, server_default=sa.text("'beginner'")),
            sa.Column("learning_style", sa.String(length=32), nullable=False, server_default=sa.text("'guided'")),
            sa.Column("language", sa.String(length=16), nullable=False, server_default=sa.text("'vi'")),
            sa.Column("weekly_minutes", sa.Integer(), nullable=False, server_default=sa.text("180")),
            sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'active'")),
            sa.Column("metadata_json", sa.Text(), nullable=True),
            sa.Column("target_date", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_learning_programs_user_id", "learning_programs", ["user_id"])
        op.create_index("ix_learning_programs_workspace_id", "learning_programs", ["workspace_id"])

    if not _table_exists("learning_program_nodes"):
        op.create_table(
            "learning_program_nodes",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("program_id", sa.String(length=36), sa.ForeignKey("learning_programs.id"), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("node_key", sa.String(length=64), nullable=False),
            sa.Column("stage", sa.String(length=64), nullable=False),
            sa.Column("title", sa.String(length=191), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("explanation", sa.Text(), nullable=False),
            sa.Column("worked_example", sa.Text(), nullable=False),
            sa.Column("practice_task", sa.Text(), nullable=False),
            sa.Column("reflection_prompt", sa.Text(), nullable=False),
            sa.Column("estimated_minutes", sa.Integer(), nullable=False, server_default=sa.text("30")),
            sa.Column("difficulty", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column("prerequisites_json", sa.Text(), nullable=True),
            sa.Column("common_pitfalls_json", sa.Text(), nullable=True),
            sa.Column("expected_keywords_json", sa.Text(), nullable=True),
            sa.Column("success_criteria_json", sa.Text(), nullable=True),
            sa.Column("resources_json", sa.Text(), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=True),
            sa.Column("mastery_status", sa.String(length=32), nullable=False, server_default=sa.text("'locked'")),
            sa.Column("mastery_score", sa.Float(), nullable=False, server_default=sa.text("0")),
            sa.Column("evidence_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("last_practiced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("review_due_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("program_id", "position", name="uq_learning_program_nodes_program_position"),
        )
        op.create_index("ix_learning_program_nodes_program_id", "learning_program_nodes", ["program_id"])

    if not _table_exists("learning_sessions"):
        op.create_table(
            "learning_sessions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("program_id", sa.String(length=36), sa.ForeignKey("learning_programs.id"), nullable=False),
            sa.Column("node_id", sa.String(length=36), sa.ForeignKey("learning_program_nodes.id"), nullable=False),
            sa.Column("conversation_id", sa.String(length=36), sa.ForeignKey("conversations.id"), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'active'")),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("lesson_snapshot_json", sa.Text(), nullable=True),
            sa.Column("feedback_json", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_learning_sessions_user_id", "learning_sessions", ["user_id"])
        op.create_index("ix_learning_sessions_program_id", "learning_sessions", ["program_id"])
        op.create_index("ix_learning_sessions_node_id", "learning_sessions", ["node_id"])
        op.create_index("ix_learning_sessions_conversation_id", "learning_sessions", ["conversation_id"])

    if not _table_exists("learning_assessment_attempts"):
        op.create_table(
            "learning_assessment_attempts",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("program_id", sa.String(length=36), sa.ForeignKey("learning_programs.id"), nullable=False),
            sa.Column("node_id", sa.String(length=36), sa.ForeignKey("learning_program_nodes.id"), nullable=False),
            sa.Column("session_id", sa.String(length=36), sa.ForeignKey("learning_sessions.id"), nullable=False),
            sa.Column("answer_text", sa.Text(), nullable=False),
            sa.Column("score", sa.Float(), nullable=False, server_default=sa.text("0")),
            sa.Column("passed", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("strengths_json", sa.Text(), nullable=True),
            sa.Column("misconceptions_json", sa.Text(), nullable=True),
            sa.Column("feedback", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_learning_assessment_attempts_user_id", "learning_assessment_attempts", ["user_id"])
        op.create_index("ix_learning_assessment_attempts_program_id", "learning_assessment_attempts", ["program_id"])
        op.create_index("ix_learning_assessment_attempts_node_id", "learning_assessment_attempts", ["node_id"])
        op.create_index("ix_learning_assessment_attempts_session_id", "learning_assessment_attempts", ["session_id"])


def downgrade() -> None:
    if _table_exists("learning_assessment_attempts"):
        op.drop_index("ix_learning_assessment_attempts_session_id", table_name="learning_assessment_attempts")
        op.drop_index("ix_learning_assessment_attempts_node_id", table_name="learning_assessment_attempts")
        op.drop_index("ix_learning_assessment_attempts_program_id", table_name="learning_assessment_attempts")
        op.drop_index("ix_learning_assessment_attempts_user_id", table_name="learning_assessment_attempts")
        op.drop_table("learning_assessment_attempts")

    if _table_exists("learning_sessions"):
        op.drop_index("ix_learning_sessions_conversation_id", table_name="learning_sessions")
        op.drop_index("ix_learning_sessions_node_id", table_name="learning_sessions")
        op.drop_index("ix_learning_sessions_program_id", table_name="learning_sessions")
        op.drop_index("ix_learning_sessions_user_id", table_name="learning_sessions")
        op.drop_table("learning_sessions")

    if _table_exists("learning_program_nodes"):
        op.drop_index("ix_learning_program_nodes_program_id", table_name="learning_program_nodes")
        op.drop_table("learning_program_nodes")

    if _table_exists("learning_programs"):
        op.drop_index("ix_learning_programs_workspace_id", table_name="learning_programs")
        op.drop_index("ix_learning_programs_user_id", table_name="learning_programs")
        op.drop_table("learning_programs")
