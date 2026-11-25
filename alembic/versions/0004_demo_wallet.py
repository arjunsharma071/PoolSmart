from alembic import op
import sqlalchemy as sa


revision = "0004_demo_wallet"
down_revision = "0003_one_payment_per_ride"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "wallet_balance_inr" not in columns:
        op.add_column(
            "users",
            sa.Column("wallet_balance_inr", sa.Float(), nullable=False, server_default="1000.0"),
        )


def downgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "wallet_balance_inr" in columns:
        op.drop_column("users", "wallet_balance_inr")
