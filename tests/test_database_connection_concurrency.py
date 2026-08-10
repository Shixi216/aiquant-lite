from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from database.connection_manager import connect_database


def test_parallel_connection_creation_for_one_file_is_stable(tmp_path: Path) -> None:
    database = tmp_path / "parallel.duckdb"
    with connect_database(database, configured_path=database) as connection:
        connection.execute("CREATE TABLE sample(value INTEGER)")

    def query(_: int) -> tuple[int]:
        with connect_database(database, configured_path=database) as connection:
            return connection.execute("SELECT COUNT(*) FROM sample").fetchone()

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(query, range(120)))
    assert results == [(0,)] * 120

