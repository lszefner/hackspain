import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
MIGRATIONS = sorted((ROOT / "supabase" / "migrations").glob("*.sql"))


@pytest.fixture(scope="session")
def postgres_dsn(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Start a disposable local PostgreSQL instance for persistence tests."""

    psycopg = pytest.importorskip("psycopg")
    initdb = shutil.which("initdb")
    postgres = shutil.which("postgres")
    if not initdb or not postgres:
        pytest.skip("local PostgreSQL binaries are required for runtime tests")
    data_dir = tmp_path_factory.mktemp("postgres-data")
    # PostgreSQL's Unix socket path is limited to 103 bytes; pytest's nested
    # temporary path can exceed that on macOS.
    socket_dir = Path(tempfile.mkdtemp(prefix="pgsock-"))
    subprocess.run(
        [
            initdb,
            "-D",
            str(data_dir),
            "-U",
            "postgres",
            "-A",
            "trust",
            "--no-locale",
            "--encoding=UTF8",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen(
        [
            postgres,
            "-D",
            str(data_dir),
            "-k",
            str(socket_dir),
            "-p",
            str(port),
            "-h",
            "127.0.0.1",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    dsn = f"postgresql://postgres@127.0.0.1:{port}/postgres"
    deadline = time.monotonic() + 15
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                details = process.stderr.read() if process.stderr else ""
                raise RuntimeError(f"postgres exited during startup: {details}")
            try:
                with psycopg.connect(dsn, connect_timeout=1) as connection:
                    for migration in MIGRATIONS:
                        connection.execute(migration.read_text(encoding="utf-8"))
                    connection.commit()
                break
            except psycopg.OperationalError:
                time.sleep(0.1)
        else:
            raise RuntimeError("timed out waiting for local PostgreSQL")
        yield dsn
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        shutil.rmtree(socket_dir, ignore_errors=True)


@pytest.fixture()
def db(postgres_dsn: str):
    psycopg = pytest.importorskip("psycopg")
    from ingestion.storage import PostgresRepository

    # Other tests use postgres_dsn directly with separate mock Storage buckets.
    # Isolate both metadata and bytes at the start of each repository fixture.
    with psycopg.connect(postgres_dsn) as connection:
        connection.execute("DROP SCHEMA IF EXISTS ingestion CASCADE")
        for migration in MIGRATIONS:
            connection.execute(migration.read_text(encoding="utf-8"))
    repository = PostgresRepository(postgres_dsn)
    yield repository
    repository.close()
    with psycopg.connect(postgres_dsn) as connection:
        connection.execute("DROP SCHEMA IF EXISTS ingestion CASCADE")
        connection.commit()
        for migration in MIGRATIONS:
            connection.execute(migration.read_text(encoding="utf-8"))
        connection.commit()
