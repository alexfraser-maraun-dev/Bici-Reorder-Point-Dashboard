"""Current process RSS, for confirming memory work on the deployed instance.

Render's own chart samples every 15s and shows the container, which is enough to see
that memory climbed but not what climbed it. This reports the worker's live RSS at
points we care about — each scrape phase, and the heaviest request path — so a
staircase can be attributed to a phase instead of reconstructed from CPU shape after
the fact.

Deliberately dependency-free and never raising: a probe that can break a request is
worse than no probe. Set MEMORY_PROBE_ENABLED=false to silence the logging.
"""
import os
import resource
import sys


def _enabled() -> bool:
    return os.getenv("MEMORY_PROBE_ENABLED", "true").strip().lower() in (
        "1", "true", "yes", "on")


def rss_mb() -> float:
    """Resident set size in MB, or 0.0 if it cannot be read.

    Prefers /proc/self/statm (Linux, where this deploys): it reports *current* RSS,
    while getrusage returns a high-water mark that never comes down — and the whole
    question here is whether memory comes back down.
    """
    try:
        with open("/proc/self/statm", "r") as fh:
            pages = int(fh.read().split()[1])
        return round(pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024), 1)
    except Exception:
        pass
    try:
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports KB, macOS bytes.
        return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)
    except Exception:
        return 0.0


def log_rss(label: str) -> float:
    """Prints `label` with the current RSS and returns it. Never raises."""
    try:
        mb = rss_mb()
        if _enabled() and mb:
            print(f"[mem] {label}: {mb} MB")
        return mb
    except Exception:
        return 0.0
