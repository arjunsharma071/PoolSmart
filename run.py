import argparse
import logging
import os
import socket
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def main():
    parser = argparse.ArgumentParser(description="Run the PoolSmart service.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--demo",
        action="store_true",
        help="Run the complete local simulator using an isolated demo database.",
    )
    modes.add_argument(
        "--reset-demo",
        action="store_true",
        help="Reset and reseed only the local demo database, then exit.",
    )
    modes.add_argument(
        "--local-preview",
        action="store_true",
        help="Serve a read-only local preview without external service credentials.",
    )
    args = parser.parse_args()
    local_preview = args.local_preview or os.getenv("POOLSMART_LOCAL_PREVIEW") == "1"
    demo_mode = args.demo or args.reset_demo
    if demo_mode:
        demo_database = ROOT / ".poolsmart-demo.db"
        os.environ["POOLSMART_MODE"] = "demo"
        os.environ["DATABASE_URL"] = "sqlite:///./.poolsmart-demo.db"
        os.environ["JWT_SECRET"] = "poolsmart-demo-only-secret-change-before-use-2026"
        os.environ["PUBLIC_BASE_URL"] = "http://127.0.0.1:8000"
        os.environ["HOST"] = "127.0.0.1"
        os.environ["PORT"] = "8000"
        if args.reset_demo:
            with socket.socket() as check:
                check.settimeout(0.25)
                if check.connect_ex(("127.0.0.1", 8000)) == 0:
                    print("Stop the local PoolSmart server before resetting its demo database.", file=sys.stderr)
                    raise SystemExit(4)
            for suffix in ("", "-wal", "-shm"):
                database_file = Path(str(demo_database) + suffix)
                if database_file.exists():
                    database_file.unlink()
    elif local_preview:
        os.environ["POOLSMART_LOCAL_PREVIEW"] = "1"

    from app.config import get_settings

    settings = get_settings()
    missing = settings.missing_runtime_settings()
    if missing and not (local_preview or demo_mode):
        print(settings.setup_instructions(missing), file=sys.stderr)
        raise SystemExit(2)
    try:
        from alembic import command
        from alembic.config import Config
        import uvicorn

        alembic = Config(str(ROOT / "alembic.ini"))
        alembic.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
        command.upgrade(alembic, "head")
        if args.reset_demo:
            from app.demo import seed_demo_data

            seed_demo_data()
    except Exception as error:
        print("Database migration failed (%s). Check DATABASE_URL and storage permissions." % type(error).__name__, file=sys.stderr)
        raise SystemExit(3)
    if args.reset_demo:
        print("Demo database reset and seeded. Start it with: python app.py --demo")
        return
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1" if demo_mode or local_preview else os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        proxy_headers=True,
        forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1"),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
