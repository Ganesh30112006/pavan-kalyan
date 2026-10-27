"""Administration commands.

    python -m app.cli migrate
    python -m app.cli create-user --email ada@example.com --name "Ada" --role owner --new-organization "Acme"
    python -m app.cli create-user --email bob@example.com --name "Bob" --role developer --organization-id <id>
    python -m app.cli set-password --email ada@example.com

The password is read from the terminal (or CLOUDBRIDGE_BOOTSTRAP_PASSWORD for automation); it is
never accepted as a command-line argument, so it does not end up in shell history.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from sqlalchemy import func, select

from app import clock, validation
from app.config import Settings
from app.db import Database
from app.domain import USER_ROLES
from app.models import Organization, User
from app.security import hash_password, password_problem

ROOT = Path(__file__).resolve().parent.parent


def migrate(settings: Settings) -> None:
    from alembic import command
    from alembic.config import Config

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")


def _password() -> str:
    from_env = os.environ.get("CLOUDBRIDGE_BOOTSTRAP_PASSWORD")
    if from_env:
        password = from_env
    else:
        password = getpass.getpass("Password: ")
        if getpass.getpass("Repeat password: ") != password:
            sys.exit("The passwords do not match.")
    if problem := password_problem(password):
        sys.exit(problem)
    return password


def create_user(settings: Settings, args: argparse.Namespace) -> None:
    email = args.email.strip().lower()
    if not validation.looks_like_email(email):
        sys.exit("Enter a valid e-mail address.")
    name = args.name.strip()
    if not name or len(name) > 100:
        sys.exit("The display name must be 1 to 100 characters.")
    database = Database(settings.database_url)
    with database.session() as db:
        if db.scalar(select(func.count()).select_from(User).where(func.lower(User.email) == email)):
            sys.exit("A user with this e-mail address already exists.")
        if args.new_organization is not None:
            org_name = args.new_organization.strip()
            if not org_name or len(org_name) > 100:
                sys.exit("The organization name must be 1 to 100 characters.")
            organization = Organization(name=org_name, created_at=clock.utcnow())
            db.add(organization)
            db.flush()
        else:
            found = db.get(Organization, args.organization_id)
            if found is None:
                sys.exit("No organization has this ID.")
            organization = found
        user = User(
            organization_id=organization.id,
            email=email,
            display_name=name,
            role=args.role,
            password_hash=hash_password(_password()),
            time_format="24h",
            created_at=clock.utcnow(),
        )
        db.add(user)
        db.commit()
        print(f"Created {args.role} {email} in organization {organization.name} ({organization.id}).")
    database.dispose()


def set_password(settings: Settings, args: argparse.Namespace) -> None:
    database = Database(settings.database_url)
    with database.session() as db:
        user = db.scalar(select(User).where(func.lower(User.email) == args.email.strip().lower()))
        if user is None:
            sys.exit("No user has this e-mail address.")
        user.password_hash = hash_password(_password())
        db.commit()
        print(f"Password updated for {user.email}.")
    database.dispose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="CloudBridge administration")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="Create or upgrade the database schema")
    create = commands.add_parser("create-user", help="Create a user")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--role", choices=USER_ROLES, required=True)
    where = create.add_mutually_exclusive_group(required=True)
    where.add_argument("--new-organization", metavar="NAME")
    where.add_argument("--organization-id", metavar="ID")
    reset = commands.add_parser("set-password", help="Set a user's password")
    reset.add_argument("--email", required=True)
    args = parser.parse_args(argv)

    settings = Settings()
    if args.command == "migrate":
        migrate(settings)
        print("Database schema is up to date.")
    elif args.command == "create-user":
        create_user(settings, args)
    elif args.command == "set-password":
        set_password(settings, args)


if __name__ == "__main__":
    main()
