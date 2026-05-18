"""Create the on-disk DB and apply all migrations."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


def main() -> None:
    Path("data").mkdir(parents=True, exist_ok=True)
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")


if __name__ == "__main__":
    main()
