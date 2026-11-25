from alembic import op
import sqlalchemy as sa


revision = "0003_one_payment_per_ride"
down_revision = "0002_gender_verification"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if any(
        constraint.get("column_names") == ["ride_id"]
        for constraint in inspector.get_unique_constraints("payments")
    ) or any(
        index.get("unique") and index.get("column_names") == ["ride_id"]
        for index in inspector.get_indexes("payments")
    ):
        return

    duplicate = bind.execute(sa.text(
        "SELECT ride_id FROM payments GROUP BY ride_id HAVING COUNT(*) > 1 LIMIT 1"
    )).first()
    if duplicate:
        raise RuntimeError(
            "Cannot enforce one payment per ride: reconcile duplicate payment rows before migrating."
        )
    op.create_index("uq_payments_ride_id", "payments", ["ride_id"], unique=True)


def downgrade():
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("payments")}
    if "uq_payments_ride_id" in indexes:
        op.drop_index("uq_payments_ride_id", table_name="payments")
