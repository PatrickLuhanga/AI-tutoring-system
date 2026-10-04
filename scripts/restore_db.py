"""Restore the portable database seed into a fresh PostgreSQL instance.

The seed (``database_seed.sql`` at the repo root) carries the ingested vector
store - ``curriculum_chunks`` with their 384-dim embeddings, plus
``code_repair_patterns`` and every base table - so a fresh clone is not an empty
app that answers from general knowledge.

Two environments are supported, chosen automatically:

* **Docker (default here).** The compose stack runs PostgreSQL in the
  ``ai_tutoring_pg`` container. The seed is piped into ``psql`` *inside* that
  container, so no host psql client is required.
* **Host psql.** If no container is running, the configured ``DATABASE_URL`` /
  ``POSTGRES_*`` values are used to invoke a local ``psql``.

The dump is taken with ``--clean --if-exists``, so restoring is idempotent: it
drops and recreates the objects it defines. This is destructive to the target
database by design - the script refuses to run unless the target looks like a
throwaway or ``--yes`` is passed.

Usage::

    python -m scripts.restore_db --dry-run       # report what would happen
    python -m scripts.restore_db --yes           # restore, no prompt
    python -m scripts.restore_db --seed other.sql --container my_pg --yes
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SEED = PROJECT_ROOT / "database_seed.sql"
DEFAULT_CONTAINER = "ai_tutoring_pg"
DEFAULT_DB = "ai_tutoring"
DEFAULT_USER = "tutor_admin"


def _run(cmd: list[str], *, stdin=None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        stdin=stdin,
        capture_output=True,
        text=True,
        check=check,
    )


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        _run(["docker", "info"], check=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def _container_running(name: str) -> bool:
    try:
        out = _run(
            ["docker", "ps", "--filter", f"name=^/{name}$", "--format", "{{.Names}}"]
        ).stdout.strip()
        return out == name
    except Exception:  # noqa: BLE001
        return False


def _restore_docker(seed: Path, container: str, db: str, user: str) -> int:
    """Copy the seed into the container and psql it in."""
    print(f"Restoring via Docker container {container!r} -> database {db!r}")
    # Copy the file in (a 50 MB file over `docker exec -i` can be flaky on
    # Windows; `docker cp` is reliable and resumable).
    target = "/tmp/database_seed.sql"
    cp = _run(["docker", "cp", str(seed), f"{container}:{target}"], check=False)
    if cp.returncode != 0:
        print(cp.stderr, file=sys.stderr)
        return cp.returncode

    psql = [
        "docker", "exec", container,
        "psql", "-v", "ON_ERROR_STOP=1", "-U", user, "-d", db,
        "-f", target,
    ]
    result = _run(psql, check=False)
    if result.stdout:
        # pg_dump output is verbose; show only the tail.
        tail = "\n".join(result.stdout.strip().splitlines()[-15:])
        print(tail)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return result.returncode
    print("Restore complete.")
    return 0


def _restore_host(seed: Path) -> int:
    """Fall back to a host psql using POSTGRES_* from the environment."""
    from src.config import settings
    from sqlalchemy.engine import make_url

    if shutil.which("psql") is None:
        print(
            "No Docker container and no host `psql` found. Start the compose stack "
            "(docker compose up -d) or install the PostgreSQL client.",
            file=sys.stderr,
        )
        return 2

    url = make_url(settings.database_url)
    env = {
        **__import__("os").environ,
        "PGPASSWORD": url.password or "",
    }
    print(f"Restoring via host psql -> {url.host}:{url.port}/{url.database}")
    with seed.open("rb") as handle:
        result = subprocess.run(
            [
                "psql", "-v", "ON_ERROR_STOP=1",
                "-h", url.host or "localhost",
                "-p", str(url.port or 5432),
                "-U", url.username or "postgres",
                "-d", url.database or "postgres",
                "-f", "-",
            ],
            stdin=handle,
            capture_output=True,
            text=True,
            env=env,
        )
    if result.returncode != 0:
        print(result.stderr[-2000:], file=sys.stderr)
        return result.returncode
    print("Restore complete.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED, help="path to the SQL seed file")
    parser.add_argument("--container", default=DEFAULT_CONTAINER, help="Docker container name")
    parser.add_argument("--db", default=DEFAULT_DB, help="target database name")
    parser.add_argument("--user", default=DEFAULT_USER, help="database role")
    parser.add_argument("--yes", action="store_true", help="skip the destructive confirmation")
    parser.add_argument("--dry-run", action="store_true", help="report the plan, do nothing")
    args = parser.parse_args(argv)

    if not args.seed.exists():
        print(f"Seed not found: {args.seed}", file=sys.stderr)
        return 2

    size_mb = args.seed.stat().st_size / (1024 * 1024)
    use_docker = _docker_available() and _container_running(args.container)
    mode = f"Docker ({args.container})" if use_docker else "host psql"

    print(f"Seed          : {args.seed} ({size_mb:.1f} MB)")
    print(f"Restore mode  : {mode}")
    print(f"Target DB     : {args.db}")

    if args.dry_run:
        print("\n(dry run - nothing restored)")
        return 0

    if not args.yes:
        print(
            "\nThis DROPS and recreates the objects in the seed. Existing data in "
            "the target database will be lost."
        )
        if input("Continue? [y/N] ").strip().lower() not in {"y", "yes"}:
            print("Aborted.")
            return 1

    if use_docker:
        return _restore_docker(args.seed, args.container, args.db, args.user)
    return _restore_host(args.seed)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
