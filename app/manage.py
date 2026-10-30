"""Administrative commands, invoked explicitly by an operator."""
import argparse
import asyncio
import getpass
import re
from datetime import timedelta

from sqlalchemy import delete, func, select, update

from app.config import Settings
from app.db import create_engine, session_factory
from app.models import Generation, LoginSession, User, WorkerHeartbeat
from app.security import password_hasher


async def execute(args: argparse.Namespace) -> None:
    settings = Settings()
    engine = create_engine(settings)
    try:
        async with session_factory(engine).begin() as db:
            if args.command == "check-worker":
                recent = await db.scalar(select(WorkerHeartbeat.id).where(
                    WorkerHeartbeat.seen_at > func.now() - timedelta(seconds=30),
                ).limit(1))
                if recent is None:
                    raise SystemExit("No live worker heartbeat")
                return
            username = args.username.lower()
            if not re.fullmatch(r"[a-z0-9_.@-]{1,64}", username):
                raise SystemExit("Invalid username")
            user = await db.scalar(select(User).where(User.username == username).with_for_update())
            if args.command == "disable-user":
                if user is None:
                    raise SystemExit("User does not exist")
                user.active = False
                await db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
                await db.execute(update(Generation).where(
                    Generation.user_id == user.id, Generation.status == "pending",
                ).values(status="failed", error_code="account_disabled", finished_at=func.now(),
                         error_message="Учётная запись отключена администратором."))
                print("User disabled; all sessions revoked")
                return
            if args.command == "create-user" and user is not None:
                raise SystemExit("User already exists")
            if args.command == "set-password" and user is None:
                raise SystemExit("User does not exist")
            password = getpass.getpass("Password (at least 12 characters): ")
            confirmation = getpass.getpass("Repeat password: ")
            if password != confirmation or not 12 <= len(password) <= 256:
                raise SystemExit("Passwords must match and contain 12–256 characters")
            encoded = password_hasher.hash(password)
            if user is None:
                name = args.name or username
                if not 1 <= len(name) <= 80:
                    raise SystemExit("Display name must contain 1–80 characters")
                db.add(User(username=username, display_name=name, password_hash=encoded))
            else:
                user.password_hash = encoded
                await db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
        print("User saved")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Вектор: управление пользователями")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("create-user", "set-password", "disable-user"):
        item = sub.add_parser(command)
        item.add_argument("username")
        if command == "create-user":
            item.add_argument("--name")
    sub.add_parser("check-worker")
    asyncio.run(execute(parser.parse_args()))


if __name__ == "__main__":
    main()
