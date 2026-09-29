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


_libc_malloc_trim = None


def _malloc_trim():
    """glibc's malloc_trim, or None where there is no glibc (macOS, musl)."""
    global _libc_malloc_trim
    if _libc_malloc_trim is None:
        try:
            import ctypes
            _libc_malloc_trim = ctypes.CDLL("libc.so.6").malloc_trim
        except Exception:
            _libc_malloc_trim = False
    return _libc_malloc_trim or None


def trim(label: str) -> tuple:
    """Collects garbage and hands freed heap pages back to the OS; returns
    (rss_before, rss_after) in MB. Never raises.

    Exists because of the 2026-09 OOMs: the nightly scrape freed its working set
    but RSS never came down, so each night stacked another ~75-100 MB on the last.
    glibc keeps freed memory inside its arenas (only the top of the main heap is
    returned automatically); malloc_trim(0) releases the free pages in every arena.
    gc.collect() first, because BeautifulSoup trees are reference cycles and are
    otherwise still live when we trim.
    """
    try:
        before = rss_mb()
        import gc
        gc.collect()
        fn = _malloc_trim()
        if fn is not None:
            fn(0)
        after = rss_mb()
        if _enabled() and before:
            print(f"[mem] trim {label}: {before} -> {after} MB")
        return before, after
    except Exception:
        return 0.0, 0.0
