import argparse
import getpass
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)


def migrate():
    from alembic import command
    from alembic.config import Config
    from app.config import get_settings

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
    command.upgrade(config, "head")


def create_admin(email, name):
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import User, utc_now
    from app.security import create_password_hash

    password = getpass.getpass("Administrator password (12 or more characters): ")
    confirmation = getpass.getpass("Repeat password: ")
    if len(password) < 12 or password != confirmation:
        raise SystemExit("Passwords must match and contain at least 12 characters.")
    session = SessionLocal()
    try:
        current = session.scalar(select(User).where(User.email == email))
        if current:
            raise SystemExit("That email already has an account.")
        admin = User(
            email=email,
            name=name,
            role="admin",
            password_hash=create_password_hash(password),
            verified_at=utc_now(),
            consent_at=utc_now(),
        )
        session.add(admin)
        session.commit()
        print("Administrator account created.")
    finally:
        session.close()


def allow_staff(email, role):
    from app.database import SessionLocal
    from app.models import AdminEmailAllowlist

    session = SessionLocal()
    try:
        session.merge(AdminEmailAllowlist(email=email, role=role))
        session.commit()
        print("Email access added.")
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(prog="manage.py")
    subcommands = parser.add_subparsers(dest="command", required=True)
    admin = subcommands.add_parser("create-admin")
    admin.add_argument("--email", required=True)
    admin.add_argument("--name", required=True)
    allow = subcommands.add_parser("allow-email")
    allow.add_argument("--email", required=True)
    allow.add_argument("--role", choices=("staff", "alumni"), default="staff")
    args = parser.parse_args()
    migrate()
    if args.command == "create-admin":
        create_admin(args.email.strip().lower(), args.name.strip())
    else:
        allow_staff(args.email.strip().lower(), args.role)


if __name__ == "__main__":
    main()
