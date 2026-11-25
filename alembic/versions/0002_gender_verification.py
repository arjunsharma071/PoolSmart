from alembic import op
import sqlalchemy as sa


revision = "0002_gender_verification"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "gender_verified" not in columns:
        op.add_column(
            "users",
            sa.Column("gender_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "gender_verified" in columns:
        op.drop_column("users", "gender_verified")
