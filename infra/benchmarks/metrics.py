"""Resource metric collectors: CPU, memory, goroutines, DB connections,
open file descriptors, and engine RSS.

Collectors are async context managers that snapshot resource usage
at a configurable interval during a benchmark run.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger(__name__)


@dataclass
class ResourceSnapshot:
    """One point-in-time snapshot of resource usage."""

    elapsed_s: float
    """Seconds since the benchmark started."""
    cpu_pct: float | None = None
    rss_mb: float | None = None
    goroutines: int | None = None
    db_conns: int | None = None
    open_fds: int | None = None
    engine_rss_kb: int | None = None


class ResourceCollector:
    """Async context manager that snapshots resource metrics every *interval* seconds.

    Usage::

        collector = ResourceCollector(interval=5.0)
        async with collector:
            await run_benchmark(...)
        print(collector.snapshots)
    """

    def __init__(
        self,
        interval: float = 5.0,
        proxy_url: str = "http://localhost:8080",
        proxy_container: str = "undolog-proxy",
        engine_container: str = "undolog-engine",
    ) -> None:
        self._interval = interval
        self._proxy_url = proxy_url
        self._proxy_container = proxy_container
        self._engine_container = engine_container
        self._task: asyncio.Task[None] | None = None
        self._start_time: float = 0.0
        self.snapshots: list[ResourceSnapshot] = field(default_factory=list)

    async def __aenter__(self) -> ResourceCollector:
        self._start_time = time.monotonic()
        self._task = asyncio.create_task(self._poll())
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                # Expected when the collector context manager exits:
                # the polling task is explicitly cancelled above.
                log.debug("Resource polling task cancelled during collector shutdown")

    def set_interval(self, interval: float) -> None:
        """Update the polling interval for the next run."""
        self._interval = interval

    async def _poll(self) -> None:
        while True:
            snapshot = ResourceSnapshot(elapsed_s=time.monotonic() - self._start_time)
            snapshot.cpu_pct = await self._get_docker_cpu(self._proxy_container)
            snapshot.rss_mb = await self._get_docker_mem(self._proxy_container)
            snapshot.goroutines = await self._get_goroutines()
            snapshot.db_conns = await self._get_db_conns()
            snapshot.open_fds = await self._get_open_fds("undolog-proxy")
            snapshot.engine_rss_kb = await self._get_engine_rss()
            self.snapshots.append(snapshot)
            await asyncio.sleep(self._interval)

    async def _get_docker_cpu(self, container: str) -> float | None:
        try:
            result = await asyncio.create_subprocess_exec(
                "docker",
                "stats",
                container,
                "--no-stream",
                "--format",
                "{{.CPUPerc}}",
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            stdout, _ = await result.communicate()
            raw = stdout.decode().strip().rstrip("%")
            return float(raw) if raw else None
        except Exception:
            return None

    async def _get_docker_mem(self, container: str) -> float | None:
        try:
            result = await asyncio.create_subprocess_exec(
                "docker",
                "stats",
                container,
                "--no-stream",
                "--format",
                "{{.MemUsage}}",
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            stdout, _ = await result.communicate()
            raw = stdout.decode().strip()
            if "MiB" in raw:
                return float(raw.split()[0])
            if "GiB" in raw:
                return float(raw.split()[0]) * 1024
            return None
        except Exception:
            return None

    async def _get_goroutines(self) -> int | None:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self._proxy_url}/debug/vars",
                    timeout=2.0,
                )
                resp.raise_for_status()
                body = resp.json()
                return body.get("goroutines")
        except Exception:
            return None

    async def _get_db_conns(self) -> int | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "psql",
                "-U",
                "postgres",
                "-d",
                "undolog",
                "-t",
                "-c",
                "SELECT count(*) FROM pg_stat_activity",
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            return int(stdout.decode().strip()) if stdout else None
        except Exception:
            return None

    async def _get_open_fds(self, process_name: str) -> int | None:
        try:
            pid_result = await asyncio.create_subprocess_exec(
                "pgrep",
                process_name,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            pid_stdout, _ = await pid_result.communicate()
            pid = pid_stdout.decode().strip()
            if not pid:
                return None
            proc = await asyncio.create_subprocess_exec(
                "ls",
                f"/proc/{pid}/fd",
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if stdout:
                return len(stdout.decode().strip().split("\n"))
            return None
        except Exception:
            return None

    async def _get_engine_rss(self) -> int | None:
        try:
            pid_result = await asyncio.create_subprocess_exec(
                "pgrep",
                "undolog-engine",
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            pid_stdout, _ = await pid_result.communicate()
            pid = pid_stdout.decode().strip()
            if not pid:
                # Fall back to docker inspect for RSS.
                return await self._get_docker_engine_rss()
            proc = await asyncio.create_subprocess_exec(
                "cat",
                f"/proc/{pid}/status",
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if stdout:
                for line in stdout.decode().split("\n"):
                    if line.startswith("VmRSS:"):
                        parts = line.split()
                        if len(parts) >= 2:
                            return int(parts[1])
            return None
        except Exception:
            return await self._get_docker_engine_rss()

    async def _get_docker_engine_rss(self) -> int | None:
        """Fallback: get engine RSS from docker stats."""
        try:
            rss_mb = await self._get_docker_mem(self._engine_container)
            if rss_mb is not None:
                return int(rss_mb * 1024)
            return None
        except Exception:
            return None

    def summary(self) -> str:
        """Resource summary with min/mean/max across all snapshots."""
        if not self.snapshots:
            return "  (no resource data collected)"
        cpu_vals = [s.cpu_pct for s in self.snapshots if s.cpu_pct is not None]
        mem_vals = [s.rss_mb for s in self.snapshots if s.rss_mb is not None]
        gor_vals = [s.goroutines for s in self.snapshots if s.goroutines is not None]
        dbc_vals = [s.db_conns for s in self.snapshots if s.db_conns is not None]
        fds_vals = [s.open_fds for s in self.snapshots if s.open_fds is not None]
        rss_vals = [
            s.engine_rss_kb for s in self.snapshots if s.engine_rss_kb is not None
        ]

        def _fmt_mean_min_max(
            vals: list[float | int],
            unit: str,
        ) -> str:
            if not vals:
                return ""
            mn = sum(vals) / len(vals)
            return (
                f"mean={mn:.1f}{unit} "
                f"min={min(vals):.1f}{unit} "
                f"max={max(vals):.1f}{unit}"
            )

        parts: list[str] = []
        if cpu_vals:
            parts.append(f"CPU {_fmt_mean_min_max(cpu_vals, '%')}")
        if mem_vals:
            parts.append(f"RSS {_fmt_mean_min_max(mem_vals, 'MB')}")
        if gor_vals:
            parts.append(f"goroutines {_fmt_mean_min_max(gor_vals, '')}")
        if dbc_vals:
            parts.append(f"DB conns {_fmt_mean_min_max(dbc_vals, '')}")
        if fds_vals:
            parts.append(f"open FDs {_fmt_mean_min_max(fds_vals, '')}")
        if rss_vals:
            parts.append(f"engine RSS {_fmt_mean_min_max(rss_vals, 'kB')}")
        return "  Resources: " + ", ".join(parts) if parts else "  (no resource data)"

    def to_dicts(self) -> list[dict[str, Any]]:
        """Return snapshots as a list of JSON-serialisable dicts."""
        return [
            {
                "t": round(s.elapsed_s, 1),
                "cpu_pct": s.cpu_pct,
                "rss_mb": s.rss_mb,
                "goroutines": s.goroutines,
                "db_conns": s.db_conns,
                "open_fds": s.open_fds,
                "engine_rss_kb": s.engine_rss_kb,
            }
            for s in self.snapshots
        ]
