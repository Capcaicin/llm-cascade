"""In-process telemetry for the router — stdlib only.

Counters, histograms, and gauges with a fixed label set, plus a structured
per-request JSON logger. Rendered as Prometheus text (`/metrics`) or JSON
(`/metrics.json`) by the router. Thread-safe; overhead is a dict lookup and
a mutex per observation.

Why not prometheus_client? This stays a zero-dep addition so anyone cloning
the repo gets metrics without `pip install`. If you later want pull-scraping
from a real Prometheus server, swap this out — the /metrics text format is
already compatible.
"""

from __future__ import annotations

import bisect
import json
import logging
import threading
import time
from typing import Iterable


# Default histogram buckets (seconds). Covers sub-second chat up through
# two-pass / refine latencies. Last bucket is +Inf implicitly.
DEFAULT_BUCKETS = (0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0)


def _label_key(labels: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
    if not labels:
        return ()
    return tuple(sorted((k, str(v)) for k, v in labels.items()))


def _label_str(key: tuple[tuple[str, str], ...]) -> str:
    if not key:
        return ""
    inner = ",".join(f'{k}="{_escape(v)}"' for k, v in key)
    return "{" + inner + "}"


def _escape(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class Counter:
    __slots__ = ("name", "help", "_values", "_lock")

    def __init__(self, name: str, help_text: str = "") -> None:
        self.name = name
        self.help = help_text
        self._values: dict[tuple, float] = {}
        self._lock = threading.Lock()

    def inc(self, amount: float = 1.0, labels: dict[str, str] | None = None) -> None:
        key = _label_key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def snapshot(self) -> list[tuple[tuple, float]]:
        with self._lock:
            return list(self._values.items())


class Gauge:
    __slots__ = ("name", "help", "_values", "_lock")

    def __init__(self, name: str, help_text: str = "") -> None:
        self.name = name
        self.help = help_text
        self._values: dict[tuple, float] = {}
        self._lock = threading.Lock()

    def set(self, value: float, labels: dict[str, str] | None = None) -> None:
        key = _label_key(labels)
        with self._lock:
            self._values[key] = value

    def snapshot(self) -> list[tuple[tuple, float]]:
        with self._lock:
            return list(self._values.items())


class Histogram:
    __slots__ = ("name", "help", "buckets", "_state", "_lock")

    def __init__(self, name: str, help_text: str = "",
                 buckets: Iterable[float] = DEFAULT_BUCKETS) -> None:
        self.name = name
        self.help = help_text
        self.buckets = tuple(sorted(buckets))
        # labels -> {"counts": [...], "sum": float, "count": int}
        self._state: dict[tuple, dict] = {}
        self._lock = threading.Lock()

    def observe(self, value: float, labels: dict[str, str] | None = None) -> None:
        key = _label_key(labels)
        idx = bisect.bisect_right(self.buckets, value)
        with self._lock:
            st = self._state.get(key)
            if st is None:
                st = {"counts": [0] * (len(self.buckets) + 1), "sum": 0.0, "count": 0}
                self._state[key] = st
            st["counts"][idx] += 1
            st["sum"] += value
            st["count"] += 1

    def snapshot(self) -> list[tuple[tuple, dict]]:
        with self._lock:
            return [(k, {"counts": list(v["counts"]), "sum": v["sum"], "count": v["count"]})
                    for k, v in self._state.items()]


# ── Registry ─────────────────────────────────────────────────────────────────

_START_TIME = time.time()

REQUESTS = Counter(
    "router_requests_total",
    "Total /chat/completions requests, labeled by model, task_type, mode, outcome.",
)
REQUEST_LATENCY = Histogram(
    "router_request_seconds",
    "Wall-clock latency of /chat/completions in seconds, labeled by model and mode.",
)
OUTPUT_CHARS = Counter(
    "router_output_chars_total",
    "Total characters produced by the model (proxy for tokens when the backend omits usage).",
)
OUTPUT_CPS = Gauge(
    "router_output_chars_per_second",
    "Rolling throughput (chars/sec) over the last completed request per model.",
)
AUTO_REFINE = Counter(
    "router_auto_refine_triggered_total",
    "How often the sorter/auto-refine gate elected to run a critic pass.",
)
TWO_PASS = Counter(
    "router_two_pass_triggered_total",
    "How often an explicit two-pass alias (router-two-pass, -uncensored) dispatched.",
)
RATE_LIMITED = Counter(
    "router_rate_limited_total",
    "429 responses emitted by the per-IP rate limiter.",
)
UPSTREAM_ERRORS = Counter(
    "router_upstream_errors_total",
    "Exceptions bubbling out of Ollama or a subagent, returned as 502.",
)

_ALL: tuple = (REQUESTS, REQUEST_LATENCY, OUTPUT_CHARS, OUTPUT_CPS,
               AUTO_REFINE, TWO_PASS, RATE_LIMITED, UPSTREAM_ERRORS)


# ── Per-request structured logger ────────────────────────────────────────────
# A separate logger so operators can route `ai_stack.telemetry` to a file or
# drop it into a log aggregator without polluting the general router log.
_log = logging.getLogger("ai_stack.telemetry")
_log.setLevel(logging.INFO)
# Don't double-log if the root already has a handler; leave configuration to
# the caller (or the uvicorn log setup).

def log_request(payload: dict) -> None:
    """Emit one structured line per completed request. Caller assembles the dict."""
    try:
        _log.info(json.dumps(payload, separators=(",", ":"), default=str))
    except Exception:
        # Never let a log failure propagate into a user-facing error.
        pass


# ── Renderers ────────────────────────────────────────────────────────────────

def _fmt_number(v: float) -> str:
    if v == float("inf"):
        return "+Inf"
    if v == float("-inf"):
        return "-Inf"
    if v != v:  # NaN
        return "NaN"
    if float(v).is_integer():
        return str(int(v))
    return repr(v)


def prometheus_text() -> str:
    """Render all registered metrics in Prometheus exposition format."""
    lines: list[str] = []

    # Process-level gauges (emitted inline; not registered as Gauge objects
    # because they derive from the start-time constant).
    lines.append("# HELP router_start_time_seconds Unix timestamp when the router booted.")
    lines.append("# TYPE router_start_time_seconds gauge")
    lines.append(f"router_start_time_seconds {_fmt_number(_START_TIME)}")
    lines.append("# HELP router_uptime_seconds Seconds since the router booted.")
    lines.append("# TYPE router_uptime_seconds gauge")
    lines.append(f"router_uptime_seconds {_fmt_number(time.time() - _START_TIME)}")

    for c in (REQUESTS, OUTPUT_CHARS, AUTO_REFINE, TWO_PASS,
              RATE_LIMITED, UPSTREAM_ERRORS):
        lines.append(f"# HELP {c.name} {c.help}")
        lines.append(f"# TYPE {c.name} counter")
        for key, val in c.snapshot():
            lines.append(f"{c.name}{_label_str(key)} {_fmt_number(val)}")

    lines.append(f"# HELP {OUTPUT_CPS.name} {OUTPUT_CPS.help}")
    lines.append(f"# TYPE {OUTPUT_CPS.name} gauge")
    for key, val in OUTPUT_CPS.snapshot():
        lines.append(f"{OUTPUT_CPS.name}{_label_str(key)} {_fmt_number(val)}")

    h = REQUEST_LATENCY
    lines.append(f"# HELP {h.name} {h.help}")
    lines.append(f"# TYPE {h.name} histogram")
    for key, st in h.snapshot():
        cumulative = 0
        base_inner = _label_str(key).strip("{}")
        sep = "," if base_inner else ""
        for i, b in enumerate(h.buckets):
            cumulative += st["counts"][i]
            lines.append(
                f'{h.name}_bucket{{{base_inner}{sep}le="{_fmt_number(b)}"}} {cumulative}'
            )
        cumulative += st["counts"][-1]
        lines.append(
            f'{h.name}_bucket{{{base_inner}{sep}le="+Inf"}} {cumulative}'
        )
        lines.append(f'{h.name}_sum{_label_str(key)} {_fmt_number(st["sum"])}')
        lines.append(f'{h.name}_count{_label_str(key)} {st["count"]}')

    lines.append("")
    return "\n".join(lines)


def snapshot_json() -> dict:
    """Compact JSON mirror for dashboards that don't want to parse Prom text."""
    out: dict = {
        "start_time_seconds": _START_TIME,
        "uptime_seconds": time.time() - _START_TIME,
        "counters": {},
        "gauges": {},
        "histograms": {},
    }
    for c in (REQUESTS, OUTPUT_CHARS, AUTO_REFINE, TWO_PASS,
              RATE_LIMITED, UPSTREAM_ERRORS):
        out["counters"][c.name] = [
            {"labels": dict(k), "value": v} for k, v in c.snapshot()
        ]
    out["gauges"][OUTPUT_CPS.name] = [
        {"labels": dict(k), "value": v} for k, v in OUTPUT_CPS.snapshot()
    ]
    h_out = []
    for key, st in REQUEST_LATENCY.snapshot():
        cum: list[dict] = []
        running = 0
        for i, b in enumerate(REQUEST_LATENCY.buckets):
            running += st["counts"][i]
            cum.append({"le": b, "count": running})
        running += st["counts"][-1]
        cum.append({"le": "+Inf", "count": running})
        h_out.append({
            "labels": dict(key),
            "sum": st["sum"],
            "count": st["count"],
            "buckets": cum,
        })
    out["histograms"][REQUEST_LATENCY.name] = h_out
    return out
