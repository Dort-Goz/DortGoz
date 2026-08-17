"""Canlı analiz öncelikli, süreçler arası çalışma lease'leri.

Canlı analizler paylaşımlı lease alır. Eğitim, detector evaluation ve shadow
işleri tek bir münhasır lease alır. Canlı istek, münhasır işe kooperatif durma
sinyali verir ve iş bütün kaynaklarını bırakmadan başlayamaz.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from uuid import uuid4


class ExclusiveWorkload(StrEnum):
    SHADOW = "shadow"
    TRAINING = "training"
    DETECTOR_EVALUATION = "detector_evaluation"


class ExecutionCoordinationError(RuntimeError):
    """Bir çalışma lease'i güvenli biçimde verilemedi."""


class LiveWorkloadActive(ExecutionCoordinationError):
    pass


class ExclusiveWorkloadActive(ExecutionCoordinationError):
    pass


class LivePreemptionTimeout(ExecutionCoordinationError):
    pass


@dataclass(frozen=True, slots=True)
class ExclusiveLeaseOwner:
    workload: ExclusiveWorkload
    owner_pid: int
    owner_ref: str
    owner_boot_id: str


@dataclass(slots=True)
class LiveExecutionLease:
    coordinator: ExecutionCoordinator
    lease_id: str
    _released: bool = False

    def release(self) -> None:
        if not self._released:
            self.coordinator._release_live(self.lease_id)
            self._released = True

    async def release_async(self) -> None:
        await asyncio.to_thread(self.release)


@dataclass(slots=True)
class ExclusiveExecutionLease:
    coordinator: ExecutionCoordinator
    lease_id: str
    workload: ExclusiveWorkload
    _released: bool = False

    def stop_requested(self) -> bool:
        if self._released:
            return True
        return self.coordinator._exclusive_stop_requested(self.lease_id)

    def release(self) -> None:
        if not self._released:
            self.coordinator._release_exclusive(self.lease_id)
            self._released = True


class ExecutionCoordinator:
    """Ters öncelikli okuyucu-yazıcı kilidini SQLite lease'leriyle uygula.

    Canlı çalışma okuyucudur ve birlikte çalışabilir. Münhasır işler yazıcıdır.
    Fark klasik RW kilidinden canlı önceliğidir: yeni canlı istek mevcut yazıcıya
    dur sinyali verir, teardown bitene kadar bekler, sonra lease alır.
    """

    def __init__(self, database_path: Path, *, poll_seconds: float = 0.05) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds pozitif olmalıdır")
        self.database_path = database_path.resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.poll_seconds = poll_seconds
        self._initialize()

    async def acquire_live(self, *, timeout_seconds: float = 60.0) -> LiveExecutionLease:
        """Münhasır işi durdur ve temiz kapanıştan sonra paylaşımlı lease al."""

        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds pozitif olmalıdır")
        return await asyncio.to_thread(self._acquire_live, timeout_seconds)

    def acquire_exclusive(
        self,
        workload: ExclusiveWorkload,
        *,
        owner_ref: str = "",
        owner_boot_id: str = "",
    ) -> ExclusiveExecutionLease:
        """Canlı veya başka münhasır iş yoksa tek münhasır lease'i al."""

        if len(owner_ref) > 240 or len(owner_boot_id) > 64:
            raise ValueError("lease sahip kimliği izin verilen uzunluğu aşıyor")
        lease_id = f"{workload.value}-{uuid4().hex}"
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._discard_dead_processes(connection)
            live_count = connection.execute(
                "SELECT COUNT(*) FROM execution_live_leases"
            ).fetchone()[0]
            if live_count:
                connection.rollback()
                raise LiveWorkloadActive(
                    f"{live_count} canlı analiz varken {workload.value} başlatılamaz"
                )
            current = connection.execute(
                "SELECT workload FROM execution_exclusive_lease WHERE slot = 1"
            ).fetchone()
            if current is not None:
                connection.rollback()
                raise ExclusiveWorkloadActive(
                    f"münhasır iş zaten çalışıyor: {current['workload']}"
                )
            connection.execute(
                """
                INSERT INTO execution_exclusive_lease(
                    slot, lease_id, workload, owner_pid, owner_ref,
                    owner_boot_id, stop_requested, acquired_at
                ) VALUES (1, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    lease_id,
                    workload.value,
                    os.getpid(),
                    owner_ref,
                    owner_boot_id,
                    time.time(),
                ),
            )
            connection.commit()
        return ExclusiveExecutionLease(self, lease_id, workload)

    def active_exclusive(self) -> ExclusiveLeaseOwner | None:
        """Canlı PID'ye ait güncel münhasır iş sahibini döndür."""

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._discard_dead_processes(connection)
            row = connection.execute(
                """
                SELECT workload, owner_pid, owner_ref, owner_boot_id
                FROM execution_exclusive_lease
                WHERE slot = 1
                """
            ).fetchone()
            connection.commit()
        if row is None:
            return None
        return ExclusiveLeaseOwner(
            workload=ExclusiveWorkload(row["workload"]),
            owner_pid=int(row["owner_pid"]),
            owner_ref=str(row["owner_ref"]),
            owner_boot_id=str(row["owner_boot_id"]),
        )

    def _acquire_live(self, timeout_seconds: float) -> LiveExecutionLease:
        lease_id = f"live-{uuid4().hex}"
        deadline = time.monotonic() + timeout_seconds
        while True:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._discard_dead_processes(connection)
                exclusive = connection.execute(
                    "SELECT lease_id FROM execution_exclusive_lease WHERE slot = 1"
                ).fetchone()
                if exclusive is None:
                    connection.execute(
                        """
                        INSERT INTO execution_live_leases(
                            lease_id, owner_pid, acquired_at
                        ) VALUES (?, ?, ?)
                        """,
                        (lease_id, os.getpid(), time.time()),
                    )
                    connection.commit()
                    return LiveExecutionLease(self, lease_id)
                connection.execute(
                    """
                    UPDATE execution_exclusive_lease
                    SET stop_requested = 1
                    WHERE slot = 1 AND lease_id = ?
                    """,
                    (exclusive["lease_id"],),
                )
                connection.commit()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LivePreemptionTimeout(
                    "münhasır iş güvenli sürede kapanmadı; canlı analiz başlatılmadı"
                )
            time.sleep(min(self.poll_seconds, remaining))

    def _exclusive_stop_requested(self, lease_id: str) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT stop_requested
                FROM execution_exclusive_lease
                WHERE slot = 1 AND lease_id = ?
                """,
                (lease_id,),
            ).fetchone()
        return row is None or bool(row["stop_requested"])

    def _release_live(self, lease_id: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM execution_live_leases WHERE lease_id = ?",
                (lease_id,),
            )

    def _release_exclusive(self, lease_id: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                DELETE FROM execution_exclusive_lease
                WHERE slot = 1 AND lease_id = ?
                """,
                (lease_id,),
            )

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS execution_live_leases (
                    lease_id TEXT PRIMARY KEY,
                    owner_pid INTEGER NOT NULL,
                    acquired_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS execution_exclusive_lease (
                    slot INTEGER PRIMARY KEY CHECK (slot = 1),
                    lease_id TEXT NOT NULL UNIQUE,
                    workload TEXT NOT NULL,
                    owner_pid INTEGER NOT NULL,
                    owner_ref TEXT NOT NULL DEFAULT '',
                    owner_boot_id TEXT NOT NULL DEFAULT '',
                    stop_requested INTEGER NOT NULL CHECK (stop_requested IN (0, 1)),
                    acquired_at REAL NOT NULL
                );
                """
            )
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(execution_exclusive_lease)"
                ).fetchall()
            }
            if "owner_ref" not in columns:
                connection.execute(
                    "ALTER TABLE execution_exclusive_lease "
                    "ADD COLUMN owner_ref TEXT NOT NULL DEFAULT ''"
                )
            if "owner_boot_id" not in columns:
                connection.execute(
                    "ALTER TABLE execution_exclusive_lease "
                    "ADD COLUMN owner_boot_id TEXT NOT NULL DEFAULT ''"
                )
            self._discard_dead_processes(connection)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=5000")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _discard_dead_processes(connection: sqlite3.Connection) -> None:
        live_rows = connection.execute(
            "SELECT lease_id, owner_pid FROM execution_live_leases"
        ).fetchall()
        for row in live_rows:
            if not _process_is_alive(row["owner_pid"]):
                connection.execute(
                    "DELETE FROM execution_live_leases WHERE lease_id = ?",
                    (row["lease_id"],),
                )
        exclusive = connection.execute(
            "SELECT lease_id, owner_pid FROM execution_exclusive_lease WHERE slot = 1"
        ).fetchone()
        if exclusive is not None and not _process_is_alive(exclusive["owner_pid"]):
            connection.execute(
                "DELETE FROM execution_exclusive_lease WHERE lease_id = ?",
                (exclusive["lease_id"],),
            )


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        process_query_limited_information = 0x1000
        still_active = 259
        handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
            process_query_limited_information, False, pid
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(  # type: ignore[attr-defined]
                handle, ctypes.byref(exit_code)
            ):
                return False
            return exit_code.value == still_active
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


__all__ = [
    "ExclusiveExecutionLease",
    "ExclusiveLeaseOwner",
    "ExclusiveWorkload",
    "ExclusiveWorkloadActive",
    "ExecutionCoordinationError",
    "ExecutionCoordinator",
    "LiveExecutionLease",
    "LivePreemptionTimeout",
    "LiveWorkloadActive",
]
