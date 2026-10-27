"""Resource admission and cancellation shared by expensive image commands."""
import asyncio
import logging
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)



class ResourceBusy(ValueError):
    pass


def memory_snapshot():
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        values[key] = int(value.split()[0]) * 1024
    pressure = {}
    for line in Path("/proc/pressure/memory").read_text().splitlines():
        fields = line.split()
        pressure[fields[0]] = float(dict(x.split("=") for x in fields[1:])["avg10"])
    return values["MemTotal"], values["MemAvailable"], pressure


def cgroup_headroom():
    """Respect service and shared application budgets as well as machine RAM."""
    hierarchy = Path('/sys/fs/cgroup')
    relative = next(line[3:] for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::'))
    current = hierarchy / relative.lstrip('/')
    free = []
    while current != hierarchy and hierarchy in current.parents:
        try:
            maximum = (current / 'memory.max').read_text().strip()
            if maximum != 'max':
                usage = int((current / 'memory.current').read_text())
                # memory.current includes file cache. Credit only half of clean,
                # inactive file pages; never credit anonymous or active memory.
                reclaimable = 0
                try:
                    stats = dict(line.split() for line in (current / 'memory.stat').read_text().splitlines())
                    clean = max(0, int(stats.get('inactive_file', 0))
                                - int(stats.get('file_dirty', 0))
                                - int(stats.get('file_writeback', 0)))
                    reclaimable = min(usage, clean) // 2
                except (OSError, ValueError):
                    pass  # Missing statistics retain the original strict budget.
                free.append(max(0, int(maximum) - usage + reclaimable))
        except OSError:
            pass
        current = current.parent
    return min(free) if free else None


class MemoryGate:
    def __init__(self):
        self.paused = False
        self.healthy_since = None

    def budget(self):
        total, available, pressure = memory_snapshot()
        reserve = max(128 * 1024**2, int(total * 0.20))
        stressed = available < reserve or pressure.get("some", 0) >= 10 or pressure.get("full", 0) >= 2
        if stressed:
            self.paused = True
            self.healthy_since = None
        if self.paused and not stressed:
            if available > reserve * 1.5 and pressure.get("some", 0) < 2 and pressure.get("full", 0) < 0.5:
                if self.healthy_since is None:
                    self.healthy_since = time.monotonic()
                if time.monotonic() - self.healthy_since >= 10:
                    self.paused = False
            else:
                self.healthy_since = None
        if self.paused:
            raise ResourceBusy("服务器内存紧张，图片处理暂时暂停，请稍后重试")
        budget = min(int(total * 0.35), available - reserve)
        headroom = cgroup_headroom()
        if headroom is not None:
            budget = min(budget, int(headroom * 0.80))
        if budget < 64 * 1024**2:
            raise ResourceBusy("当前可用内存不足，请稍后重试")
        return budget


gate = MemoryGate()


