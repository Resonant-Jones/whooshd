"""Isolated two-request concurrency experiment (roadmap follow-up).

Runs a SEPARATE Whoosh'd broker instance with
``WHOOSHD_MLX_VLM_MAX_CONCURRENT_REQUESTS=2`` against the same 12B runtime,
without touching the working deployment on port 8000.

Measures, per experiment:
  * aggregate and per-request token throughput
  * individual TTFT
  * host swap activity (the thrash signal)
  * host memory pressure
  * failures and overloads
  * lifecycle cleanup after the run

Stops and reports FAILURE on evidence of instability: any non-2xx response
that is not an overload, any request error, or swap pages *out* during the
run (page-out is the thrash signal; page-in alone is normal warm-up).

── Lane attestation (why this file cannot certify an unverified lane) ──────

The broker's broker-level ``/runtime/capacity`` snapshot carries **no**
request-specific lane ceiling, and a successful chat completion does not
return the per-request admission details that would carry one.  A broker
whose lane limit is still one can therefore answer two concurrent requests
with 2xx each — serially — while every counter in this harness looks clean.

So a concurrency-2 claim requires *positive* attestation before it is
allowed to be reported as ``PASS``:

  1. the operator declares the lane ceiling the target broker is expected to
     enforce (``EXPERIMENT_EXPECTED_LANE_MAX_CONCURRENT``);
  2. the target broker is reachable and its own admission read succeeded;
  3. the broker's global admission ceiling is high enough for two concurrent
     requests;
  4. and either the broker *reports* a lane ceiling that matches the
     declaration, or the run itself positively observes two concurrent active
     jobs in the lane (peak ``active_jobs`` >= 2) with nothing queued.

If any of that is missing or unreachable the verdict is ``UNVERIFIED`` —
never ``PASS``.  A missing measurement is reported as unknown, never as zero.

Verdicts
--------
``PASS``        every claim is backed by measured, consistent evidence.
``FAILED``      a measured failure, or a nonzero leftover state.
``UNVERIFIED``  evidence was missing, unreachable, or inconclusive.

Exit codes: 0 = PASS, 1 = FAILED, 2 = UNVERIFIED.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

BASE = os.environ.get("EXPERIMENT_BASE_URL", "http://127.0.0.1:18940")
MODEL = os.environ.get("EXPERIMENT_MODEL", "local-chat")
UPSTREAM = os.environ.get("EXPERIMENT_UPSTREAM_URL", "http://127.0.0.1:8082")
UPSTREAM_MODEL = os.environ.get(
    "EXPERIMENT_UPSTREAM_MODEL",
    "/Volumes/Dev_SSD/whooshd/model-weights/hub/models--mlx-community--gemma-4-12B-it-qat-4bit",
)
PROMPT_CHARS = int(os.environ.get("EXPERIMENT_PROMPT_CHARS", "2048"))
MAX_TOKENS = int(os.environ.get("EXPERIMENT_MAX_TOKENS", "128"))
TIMEOUT = float(os.environ.get("EXPERIMENT_TIMEOUT", "300"))

#: Operator-declared lane ceiling the target broker is expected to enforce.
#: The broker exposes no request-specific lane ceiling on its broker-level
#: snapshot, so without this declaration the experiment cannot attest that it
#: is talking to the intended environment.
EXPECTED_LANE_MAX_CONCURRENT_RAW = os.environ.get("EXPERIMENT_EXPECTED_LANE_MAX_CONCURRENT")
#: Optional operator-declared lane name (e.g. ``mlx_vlm``).
EXPECTED_LANE_RAW = os.environ.get("EXPERIMENT_EXPECTED_LANE")
#: How often the admission endpoint is sampled while a band is in flight.
SAMPLE_INTERVAL = float(os.environ.get("EXPERIMENT_ADMISSION_SAMPLE_INTERVAL", "0.05"))

VERDICT_PASS = "PASS"
VERDICT_FAILED = "FAILED"
VERDICT_UNVERIFIED = "UNVERIFIED"

ATTESTATION_ATTESTED = "ATTESTED"
ATTESTATION_PENDING = "PENDING"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_UNVERIFIED = 2

PROMPT = (
    "You are a careful assistant. Summarise the following operational note in "
    "three short sentences, then state the single most important action item. "
    "The note describes a multi-tenant inference broker that admits requests "
    "against a bounded queue, schedules them FIFO, and records lifecycle "
    "metadata for every request without ever persisting prompt content. "
) * (PROMPT_CHARS // 400 + 1)


# ── Evidence helpers (pure) ────────────────────────────────────────────────


def parse_expected_lane_limit(raw: str | None) -> int | None:
    """Parse the operator-declared expected lane ceiling.

    Returns ``None`` when the declaration is absent or is not a positive
    integer.  An absent declaration is *not* evidence of any particular
    ceiling: the broker exposes no request-specific lane ceiling, so the
    harness cannot attest one on the operator's behalf.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        value = int(text)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _search_key(sources: dict[str, Any], key: str) -> tuple[Any, str | None]:
    """Find the first non-``None`` value for ``key`` in ``sources``.

    ``sources`` maps a surface name to a decoded JSON payload.  The returned
    source string names the surface and the path within it.
    """

    def walk(node: Any, path: str) -> tuple[Any, str | None]:
        if isinstance(node, dict):
            for candidate, value in node.items():
                child = f"{path}.{candidate}" if path else str(candidate)
                if candidate == key and value is not None:
                    return value, child
                found = walk(value, child)
                if found is not None:
                    return found
        elif isinstance(node, list):
            for index, item in enumerate(node):
                found = walk(item, f"{path}[{index}]")
                if found is not None:
                    return found
        return None

    for name, payload in sources.items():
        if payload is None:
            continue
        found = walk(payload, "")
        if found is not None:
            value, path = found
            return value, f"{name}{path}"
    return None, None


def _search_positive_int(sources: dict[str, Any], key: str) -> tuple[int | None, str | None]:
    value, source = _search_key(sources, key)
    if isinstance(value, bool) or value is None:
        return None, None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None, None
    if number <= 0:
        return None, None
    return number, source


def _search_text(sources: dict[str, Any], key: str) -> tuple[str | None, str | None]:
    value, source = _search_key(sources, key)
    if value is None or isinstance(value, (dict, list, bool)):
        return None, None
    text = str(value).strip()
    if not text:
        return None, None
    return text, source


def extract_lane_evidence(sources: dict[str, Any]) -> dict[str, Any]:
    """Pull lane identity and lane ceiling out of broker payloads, if present.

    Returns a dict with ``lane``, ``lane_max_concurrent`` and the source path
    for each.  Missing evidence is reported as ``None`` — never as a zero or
    a default that would look like a measurement.
    """
    ceiling, ceiling_source = _search_positive_int(sources, "lane_max_concurrent")
    lane, lane_source = _search_text(sources, "lane")
    return {
        "lane": lane,
        "lane_source": lane_source,
        "lane_max_concurrent": ceiling,
        "lane_max_concurrent_source": ceiling_source,
    }


@dataclass
class LaneAttestation:
    """Outcome of the lane-configuration attestation."""

    status: str
    reasons: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def attested(self) -> bool:
        return self.status == ATTESTATION_ATTESTED

    @property
    def failures(self) -> list[str]:
        return [
            reason.split(": ", 1)[1]
            for reason in self.reasons
            if reason.startswith("FAILED:")
        ]

    @property
    def unverified(self) -> list[str]:
        return [
            reason.split(": ", 1)[1]
            for reason in self.reasons
            if reason.startswith("UNVERIFIED:")
        ]


def _prefixed(prefix: str, message: str) -> str:
    return f"{prefix}: {message}"


def attest_lane_configuration(
    *,
    admission: dict | None,
    admission_reachable: bool,
    expected_lane_max_concurrent: int | None = None,
    reported_lane_max_concurrent: int | None = None,
    reported_lane_max_concurrent_source: str | None = None,
    reported_lane: str | None = None,
    expected_lane: str | None = None,
    global_ceilings: dict[str, Any] | None = None,
    observation: dict | None = None,
) -> LaneAttestation:
    """Attest that the target broker really runs the expected lane ceiling.

    ``admission`` is the decoded ``/runtime/admission`` body (``None`` when
    the read failed).  ``global_ceilings`` maps a ceiling name to its value
    (e.g. ``{"max_active_requests": 2}``); a ceiling of ``None`` means
    "not reported" and is never treated as zero.

    ``observation`` is the in-run concurrency observation
    (``{"samples": int, "peak_active_jobs": int | None,
    "peak_queue_depth": int | None}``).  When it is ``None`` the attestation
    stays ``PENDING``: the static evidence alone may be enough, but the run
    has not happened yet.

    Returns a :class:`LaneAttestation` whose ``reasons`` are prefixed with
    ``FAILED:`` or ``UNVERIFIED:`` so they can be split for the verdict.
    """
    reasons: list[str] = []
    evidence: dict[str, Any] = {
        "expected_lane_max_concurrent": expected_lane_max_concurrent,
        "expected_lane": expected_lane,
        "reported_lane_max_concurrent": reported_lane_max_concurrent,
        "reported_lane_max_concurrent_source": reported_lane_max_concurrent_source,
        "reported_lane": reported_lane,
        "broker_reachable": bool(admission_reachable),
        "global_ceilings": dict(global_ceilings or {}),
    }

    # ── 1. The target broker must be reachable ────────────────────────
    if not admission_reachable or admission is None:
        reasons.append(
            _prefixed(
                "UNVERIFIED",
                "broker admission state unreachable: the target broker could not be attested",
            )
        )
        return LaneAttestation(status=VERDICT_UNVERIFIED, reasons=reasons, evidence=evidence)

    # ── 2. The operator must declare the expected lane ceiling ───────
    if expected_lane_max_concurrent is None:
        reasons.append(
            _prefixed(
                "UNVERIFIED",
                "no expected lane ceiling declared: set EXPERIMENT_EXPECTED_LANE_MAX_CONCURRENT "
                "(the broker exposes no request-specific lane ceiling, so none can be attested)",
            )
        )
    elif expected_lane_max_concurrent < 2:
        reasons.append(
            _prefixed(
                "FAILED",
                f"declared expected lane ceiling {expected_lane_max_concurrent} "
                "cannot certify concurrency 2",
            )
        )

    # ── 3. The broker's global ceiling must allow two at once ─────────
    for name, value in sorted((global_ceilings or {}).items()):
        if value is None:
            reasons.append(
                _prefixed("UNVERIFIED", f"broker did not report the {name} ceiling")
            )
            continue
        try:
            ceiling = int(value)
        except (TypeError, ValueError):
            reasons.append(
                _prefixed("UNVERIFIED", f"broker reported a non-numeric {name} ceiling ({value!r})")
            )
            continue
        if ceiling < 2:
            reasons.append(
                _prefixed(
                    "FAILED",
                    f"broker {name} ceiling is {ceiling}: two concurrent requests are impossible",
                )
            )

    # ── 4. A broker-reported lane ceiling must match the declaration ──
    if reported_lane_max_concurrent is not None and expected_lane_max_concurrent is not None:
        if reported_lane_max_concurrent != expected_lane_max_concurrent:
            reasons.append(
                _prefixed(
                    "FAILED",
                    f"broker reports lane ceiling {reported_lane_max_concurrent} "
                    f"(from {reported_lane_max_concurrent_source}) but "
                    f"{expected_lane_max_concurrent} was expected",
                )
            )
    if expected_lane and reported_lane and expected_lane != reported_lane:
        reasons.append(
            _prefixed(
                "FAILED",
                f"broker reports lane {reported_lane!r} but lane {expected_lane!r} was expected",
            )
        )

    # ── 5. The run must corroborate the declaration ──────────────────
    config_attested = (
        reported_lane_max_concurrent is not None
        and expected_lane_max_concurrent is not None
        and reported_lane_max_concurrent == expected_lane_max_concurrent
    )
    evidence["config_attested_by_broker_report"] = config_attested

    observation_provided = observation is not None
    obs = observation or {}
    samples = obs.get("samples") or 0
    peak_active = obs.get("peak_active_jobs")
    peak_queue = obs.get("peak_queue_depth")
    evidence["observation_samples"] = samples
    evidence["observed_peak_active_jobs"] = peak_active
    evidence["observed_peak_queue_depth"] = peak_queue

    if peak_queue is not None and peak_queue > 0:
        reasons.append(
            _prefixed(
                "FAILED",
                f"broker queued a request during the x2 band (peak queue_depth={peak_queue}): "
                "the two requests did not run concurrently",
            )
        )

    if observation_provided:
        if peak_active is not None and peak_active >= 2:
            config_attested = True
            evidence["config_attested_by_observation"] = True
        elif not config_attested:
            reasons.append(
                _prefixed(
                    "UNVERIFIED",
                    "could not observe two concurrent active jobs during the x2 band "
                    f"(samples={samples}, peak_active_jobs={peak_active}): concurrency 2 is unproven",
                )
            )
    else:
        evidence["config_attested_by_observation"] = None

    if any(reason.startswith("FAILED:") for reason in reasons):
        status = VERDICT_FAILED
    elif any(reason.startswith("UNVERIFIED:") for reason in reasons):
        status = VERDICT_UNVERIFIED
    elif config_attested:
        status = ATTESTATION_ATTESTED
    else:
        status = ATTESTATION_PENDING if not observation_provided else VERDICT_UNVERIFIED

    return LaneAttestation(status=status, reasons=reasons, evidence=evidence)


def evaluate_cleanup(
    state: dict | None, reachable: bool
) -> tuple[list[str], list[str]]:
    """Evaluate the final (at-rest) broker state.

    Returns ``(failures, unverified)``.  A nonzero ``active_jobs`` or
    ``queue_depth`` is a measured failure; an unreachable or missing counter
    is *unverified* — never silently zero.
    """
    failures: list[str] = []
    unverified: list[str] = []
    if not reachable or state is None:
        unverified.append(
            "final admission read unreachable: lifecycle cleanup could not be verified"
        )
        return failures, unverified

    active = state.get("active_jobs")
    depth = state.get("queue_depth")
    if active is None:
        unverified.append("final admission read did not report active_jobs")
    else:
        try:
            active_value = int(active)
        except (TypeError, ValueError):
            unverified.append(f"final admission read reported a non-numeric active_jobs ({active!r})")
        else:
            if active_value != 0:
                failures.append(
                    f"nonzero active_jobs at rest ({active_value}): work left behind after the run"
                )
    if depth is None:
        unverified.append("final admission read did not report queue_depth")
    else:
        try:
            depth_value = int(depth)
        except (TypeError, ValueError):
            unverified.append(f"final admission read reported a non-numeric queue_depth ({depth!r})")
        else:
            if depth_value != 0:
                failures.append(
                    f"nonzero queue_depth at rest ({depth_value}): queued work left behind after the run"
                )
    return failures, unverified


def collect_band_failures(bands: list[dict]) -> list[str]:
    """Measured failures across the warm bands.

    A response that came back without usable token usage is **not** a
    request failure — the request may well have succeeded and only the
    measurement be missing.  Those samples are reported by
    :func:`collect_band_unverified` instead.
    """
    failures: list[str] = []
    for band in bands:
        for record in band.get("records", []):
            if not record.get("ok") and not record.get("measurement_missing"):
                failures.append(
                    f"band x{band.get('concurrency')} request failed: {record.get('error')}"
                )
        swap_out = band.get("swap_out_delta_pages")
        if swap_out and swap_out > 0:
            failures.append(
                f"band x{band.get('concurrency')} swapped OUT "
                f"{swap_out} pages while warm (thrash signal)"
            )
    return failures


def collect_band_unverified(bands: list[dict]) -> list[str]:
    """Evidence the warm bands did not obtain.

    Missing evidence is never a clean run.  A band whose responses
    carried no usable token usage, or whose swap counters could not be
    read, leaves one of the experiment's own criteria unmeasured.  Such
    a band makes the verdict ``UNVERIFIED`` — not ``PASS``, because the
    criterion was never checked, and not ``FAILED``, because nothing
    failed.
    """
    unverified: list[str] = []
    for band in bands:
        concurrency = band.get("concurrency")
        missing = int(band.get("unverified_count") or 0)
        if missing:
            unverified.append(
                f"band x{concurrency} returned {missing} response(s) without usable "
                "completion-token usage, so no throughput was measured for them"
            )
        if band.get("swap_out_delta_pages") is None:
            unverified.append(
                f"band x{concurrency} could not read swap counters, so the thrash "
                "criterion was not measured"
            )
    return unverified


def compose_verdict(failures: list[str], unverified: list[str]) -> str:
    """PASS only when nothing failed and nothing is unknown."""
    if failures:
        return VERDICT_FAILED
    if unverified:
        return VERDICT_UNVERIFIED
    return VERDICT_PASS


def verdict_exit_code(verdict: str) -> int:
    if verdict == VERDICT_PASS:
        return EXIT_OK
    if verdict == VERDICT_FAILED:
        return EXIT_FAILED
    return EXIT_UNVERIFIED


# ── Broker reads (network) ─────────────────────────────────────────────────


async def read_admission(client: httpx.AsyncClient) -> tuple[dict | None, bool]:
    """Read ``/runtime/admission``.  Returns ``(body, reachable)``."""
    try:
        resp = await client.get(f"{BASE}/runtime/admission", timeout=20)
    except Exception:
        return None, False
    if resp.status_code != 200:
        return None, False
    try:
        body = resp.json()
    except Exception:
        return None, False
    return (body if isinstance(body, dict) else None), True


async def read_capacity(client: httpx.AsyncClient) -> tuple[dict | None, bool]:
    """Read ``/runtime/capacity``.  Returns ``(body, reachable)``."""
    try:
        resp = await client.get(f"{BASE}/runtime/capacity", timeout=20)
    except Exception:
        return None, False
    if resp.status_code != 200:
        return None, False
    try:
        body = resp.json()
    except Exception:
        return None, False
    return (body if isinstance(body, dict) else None), True


class AdmissionSampler:
    """Sample the broker's admission counters while a band is in flight.

    The peak ``active_jobs`` is the only honest way to tell "the lane ran two
    requests at once" from "the lane queued the second request and ran them
    one after the other" — the response bodies of the two requests are
    indistinguishable.
    """

    def __init__(self, client: httpx.AsyncClient, interval: float | None = None):
        self._client = client
        self._interval = SAMPLE_INTERVAL if interval is None else interval
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.peak_active_jobs: int | None = None
        self.peak_queue_depth: int | None = None
        self.samples = 0

    async def start(self) -> None:
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> dict[str, Any]:
        if self._task is not None:
            self._stop.set()
            try:
                await self._task
            except Exception:
                pass
            self._task = None
        return self.observation()

    def observation(self) -> dict[str, Any]:
        return {
            "samples": self.samples,
            "peak_active_jobs": self.peak_active_jobs,
            "peak_queue_depth": self.peak_queue_depth,
            "sample_interval_seconds": self._interval,
        }

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                resp = await self._client.get(f"{BASE}/runtime/admission", timeout=5)
                if resp.status_code == 200:
                    body = resp.json()
                    if isinstance(body, dict):
                        active = body.get("active_jobs")
                        depth = body.get("queue_depth")
                        if isinstance(active, int) and not isinstance(active, bool):
                            self.peak_active_jobs = (
                                active
                                if self.peak_active_jobs is None
                                else max(self.peak_active_jobs, active)
                            )
                        if isinstance(depth, int) and not isinstance(depth, bool):
                            self.peak_queue_depth = (
                                depth
                                if self.peak_queue_depth is None
                                else max(self.peak_queue_depth, depth)
                            )
                        self.samples += 1
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                continue


# ── Host measurement ───────────────────────────────────────────────────────


def swap_counters() -> dict[str, int] | None:
    try:
        out = subprocess.run(
            ["vm_stat"], capture_output=True, text=True, timeout=10, check=False
        ).stdout
    except Exception:
        return None
    counters: dict[str, int] = {}
    for line in out.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        if key in ("swapins", "swapouts"):
            try:
                counters[key] = int(value.strip().rstrip("."))
            except ValueError:
                pass
    return counters or None


def mem_pressure() -> str:
    try:
        out = subprocess.run(
            ["memory_pressure"], capture_output=True, text=True, timeout=15, check=False
        ).stdout
        for line in out.splitlines():
            if "percentage" in line.lower():
                return line.strip()
    except Exception:
        pass
    return "unavailable"


# ── Requests ───────────────────────────────────────────────────────────────


def usable_completion_tokens(usage: Any) -> int | None:
    """The completion-token count carried by a usage object, or ``None``.

    A response whose usage object carries no usable completion-token
    count is **not** measurement evidence: the experiment cannot compute
    a throughput figure from it.  Such a sample is recorded as missing
    evidence rather than as a zero-token sample, because "no tokens
    observed" and "no tokens reported" are different claims.
    """
    if not isinstance(usage, dict):
        return None
    value = usage.get("completion_tokens")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0:
        return None
    return int(value)


async def one_request(client: httpx.AsyncClient, index: int) -> dict:
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT[:PROMPT_CHARS]}],
        "max_tokens": MAX_TOKENS,
    }
    t0 = time.time()
    record: dict = {"index": index}
    try:
        resp = await client.post(f"{BASE}/v1/chat/completions", json=payload, timeout=TIMEOUT)
        record["status"] = resp.status_code
        record["latency_ms"] = round((time.time() - t0) * 1000, 1)
        body: Any = None
        if 200 <= resp.status_code < 300:
            body = resp.json()
            usage = body.get("usage") if isinstance(body, dict) else None
            completion_tokens = usable_completion_tokens(usage)
            record["completion_tokens"] = completion_tokens
            record["prompt_tokens"] = (
                usage.get("prompt_tokens") if isinstance(usage, dict) else None
            )
            if completion_tokens is None:
                # A 2xx response without usable token usage is not a
                # successful *measurement*.  It is recorded as missing
                # evidence so the run cannot be certified PASS on a
                # sample it never actually measured.
                record["ok"] = False
                record["measurement_missing"] = True
                record["error"] = "missing_completion_token_usage"
            elif completion_tokens == 0:
                record["ok"] = False
                record["measurement_missing"] = True
                record["error"] = "zero_completion_tokens"
            else:
                record["ok"] = True
        else:
            record["ok"] = False
            record["error"] = (resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text)[:200]
            try:
                body = resp.json()
            except Exception:
                body = None
        # Probe for lane evidence: if a surface ever returns the per-request
        # admission details, the harness picks them up instead of assuming.
        evidence = extract_lane_evidence({"chat_completion_response": body})
        if evidence["lane"] or evidence["lane_max_concurrent"]:
            record["lane_evidence"] = evidence
    except Exception as exc:
        record["ok"] = False
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["latency_ms"] = round((time.time() - t0) * 1000, 1)
    return record


async def upstream_probe(client: httpx.AsyncClient) -> dict:
    """Read upstream-reported prefill and prompt-cache counters.

    The probe is *available* only when the upstream answered with a success
    status **and** the payload carried real usage data.  A 4xx/5xx error
    envelope, an unparsable body, or a 2xx response without usage is a
    **failed probe**: it is recorded with its reason and never reported as
    measured cache evidence.  An absent counter is absent, not zero.
    """
    payload = {
        "model": UPSTREAM_MODEL,
        "messages": [{"role": "user", "content": PROMPT[:PROMPT_CHARS]}],
        "max_tokens": MAX_TOKENS,
    }
    out: dict = {"available": False}
    try:
        resp = await client.post(f"{UPSTREAM}/v1/chat/completions", json=payload, timeout=TIMEOUT)
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}"
        out["failure_reason"] = f"transport_error:{type(exc).__name__}"
        return out

    if not (200 <= resp.status_code < 300):
        out["error"] = f"http_status:{resp.status_code}"
        out["failure_reason"] = f"http_status:{resp.status_code}"
        return out

    try:
        body = resp.json()
    except Exception:
        out["error"] = "invalid_json"
        out["failure_reason"] = "invalid_json"
        return out

    if not isinstance(body, dict):
        out["error"] = "invalid_payload"
        out["failure_reason"] = "invalid_payload"
        return out

    usage = body.get("usage")
    if not isinstance(usage, dict) or not usage:
        # A 2xx error envelope, or a payload with no usage, is not
        # measurement evidence.
        out["error"] = "missing_usage"
        out["failure_reason"] = "missing_usage"
        return out

    details = usage.get("prompt_tokens_details") or {}
    timings = body.get("timings") or {}
    out = {
        "available": True,
        "prompt_tokens": usage.get("prompt_tokens"),
        "cached_prompt_tokens": details.get("cached_tokens"),
        "prefill_ms": timings.get("prompt_ms"),
        "predicted_tokens": timings.get("predicted_n"),
        # The upstream reports decode speed as ``predicted_per_second``.
        "generation_tps": timings.get("predicted_per_second"),
    }
    return out


async def run_band(client: httpx.AsyncClient, concurrency: int) -> dict:
    swap_before = swap_counters()
    pressure_before = mem_pressure()

    sampler = AdmissionSampler(client)
    await sampler.start()

    t0 = time.time()
    records = await asyncio.gather(
        *(one_request(client, i) for i in range(concurrency))
    )
    wall = time.time() - t0

    observation = await sampler.stop()

    swap_after = swap_counters()
    pressure_after = mem_pressure()

    ok = [r for r in records if r.get("ok")]
    # Responses that came back 2xx but carried no usable token usage:
    # the request may have succeeded, but this sample contributes no
    # measurement.  They are counted separately so a missing measurement
    # is never reported as a request failure, and never as a zero-token
    # measurement.
    unmeasured = [r for r in records if r.get("measurement_missing")]
    failed = [
        r
        for r in records
        if not r.get("ok") and not r.get("measurement_missing")
    ]
    tokens = sum(r.get("completion_tokens") or 0 for r in ok)
    latencies = [r["latency_ms"] for r in ok]

    # Per-request generation rate is the mean of each request's *own*
    # rate, not the band's average token count divided by the band's wall
    # time.  The band wall is effectively the slowest request's duration,
    # so a wall-time division underreports every faster request.
    per_request_rates = []
    for record in ok:
        completion = record.get("completion_tokens")
        latency_ms = record.get("latency_ms")
        if completion is None or not latency_ms:
            continue
        seconds = latency_ms / 1000.0
        if seconds <= 0:
            continue
        per_request_rates.append(completion / seconds)
    per_request_rate = (
        round(sum(per_request_rates) / len(per_request_rates), 3)
        if per_request_rates
        else None
    )

    swap_out_delta = None
    swap_in_delta = None
    if swap_before and swap_after:
        swap_out_delta = swap_after.get("swapouts", 0) - swap_before.get("swapouts", 0)
        swap_in_delta = swap_after.get("swapins", 0) - swap_before.get("swapins", 0)

    return {
        "concurrency": concurrency,
        "wall_seconds": round(wall, 2),
        "success_count": len(ok) + len(unmeasured),
        "failure_count": len(failed),
        "measured_count": len(ok),
        "unverified_count": len(unmeasured),
        "token_evidence_available": bool(ok),
        "records": records,
        "aggregate_tokens_per_second": (
            round(tokens / wall, 3) if wall > 0 and ok else None
        ),
        "per_request_tokens_per_second": per_request_rate,
        "latency_p50_ms": round(statistics.median(latencies), 1) if latencies else None,
        "latency_max_ms": round(max(latencies), 1) if latencies else None,
        "swap_out_delta_pages": swap_out_delta,
        "swap_in_delta_pages": swap_in_delta,
        "memory_pressure_before": pressure_before,
        "memory_pressure_after": pressure_after,
        "admission_observation": observation,
    }


# ── Report ─────────────────────────────────────────────────────────────────


def build_report(
    *,
    broker: str,
    model: str,
    admission: dict | None,
    admission_reachable: bool,
    capacity: dict | None,
    capacity_reachable: bool,
    cold: dict,
    cold_swap_out: int | None,
    cold_swap_in: int | None,
    bands: list[dict],
    upstream: dict,
    rest_state: dict | None,
    rest_reachable: bool,
    attestation: LaneAttestation,
    failures: list[str],
    unverified: list[str],
) -> dict:
    verdict = compose_verdict(failures, unverified)
    return {
        "schema_version": "whooshd.lane_experiment.v1",
        "broker": broker,
        "model": model,
        "admission_at_start": {
            "reachable": admission_reachable,
            "max_active_requests": (admission or {}).get("max_active_requests"),
            "queue_enabled": (admission or {}).get("queue_enabled"),
            "lane_max_concurrent": (admission or {}).get("capacity", {}).get("lane_max_concurrent"),
            "lane_max_concurrent_reported": attestation.evidence.get(
                "reported_lane_max_concurrent"
            ),
            "lane_max_concurrent_source": attestation.evidence.get(
                "reported_lane_max_concurrent_source"
            ),
        },
        "lane_attestation": {
            "status": attestation.status,
            "reasons": list(attestation.reasons),
            "evidence": attestation.evidence,
        },
        "cold_start_observation": {
            "ok": cold.get("ok"),
            "latency_ms": cold.get("latency_ms"),
            "status": cold.get("status"),
            "error": cold.get("error"),
            "swap_out_delta_pages": cold_swap_out,
            "swap_in_delta_pages": cold_swap_in,
            "lane_evidence": cold.get("lane_evidence"),
            "note": (
                "Warm-up request, discarded from the concurrency comparison. "
                "A large swap delta here is the model being faulted back in "
                "from disk, not concurrency thrash."
            ),
        },
        "bands": bands,
        "upstream": upstream,
        "cleanup": {
            "reachable": rest_reachable,
            "active_jobs_at_rest": (rest_state or {}).get("active_jobs"),
            "queue_depth_at_rest": (rest_state or {}).get("queue_depth"),
        },
        "verdict": verdict,
        "failures": failures,
        "unverified": unverified,
    }


async def main() -> int:
    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=32)) as client:
        admission, admission_reachable = await read_admission(client)
        capacity, capacity_reachable = await read_capacity(client)

        expected_lane_limit = parse_expected_lane_limit(EXPECTED_LANE_MAX_CONCURRENT_RAW)
        expected_lane = (EXPECTED_LANE_RAW or "").strip() or None

        print(f"experiment broker: {BASE}", file=sys.stderr)
        print(f"  admission reachable: {admission_reachable}", file=sys.stderr)
        print(f"  capacity reachable : {capacity_reachable}", file=sys.stderr)
        print(f"  max_active_requests : {(admission or {}).get('max_active_requests')}", file=sys.stderr)
        print(f"  queue_enabled       : {(admission or {}).get('queue_enabled')}", file=sys.stderr)
        print(f"  lane (via capacity) : {(admission or {}).get('capacity', {}).get('lane')}", file=sys.stderr)
        print(f"  lane_max_concurrent : {(admission or {}).get('capacity', {}).get('lane_max_concurrent')}", file=sys.stderr)
        print(f"  expected lane ceiling: {expected_lane_limit}", file=sys.stderr)
        print("", file=sys.stderr)

        def abort(report: dict) -> int:
            print(json.dumps(report, indent=2))
            return verdict_exit_code(report["verdict"])

        # ── Pre-flight: the target broker and its lane must be attested ─
        if expected_lane_limit is None:
            reasons = [
                _prefixed(
                    "UNVERIFIED",
                    "no expected lane ceiling declared: set EXPERIMENT_EXPECTED_LANE_MAX_CONCURRENT "
                    "(the broker exposes no request-specific lane ceiling, so none can be attested)",
                )
            ]
            attestation = LaneAttestation(status=VERDICT_UNVERIFIED, reasons=reasons)
            report = build_report(
                broker=BASE,
                model=MODEL,
                admission=admission,
                admission_reachable=admission_reachable,
                capacity=capacity,
                capacity_reachable=capacity_reachable,
                cold={},
                cold_swap_out=None,
                cold_swap_in=None,
                bands=[],
                upstream={},
                rest_state=None,
                rest_reachable=False,
                attestation=attestation,
                failures=[],
                unverified=attestation.unverified,
            )
            print("aborting: the lane configuration cannot be attested", file=sys.stderr)
            return abort(report)

        # ── Warm-up phase (discarded) ----------------------------------
        # The model may have been evicted since it was last used.  A cold
        # request faults the weights back in from disk and produces a very
        # large swap delta that says nothing about concurrency.  Warm the
        # model first, and report the cold observation separately.
        # It doubles as the probe request: if the broker surfaces lane
        # details on a response, they are picked up here.
        swap_cold_before = swap_counters()
        cold = await one_request(client, -1)
        swap_cold_after = swap_counters()
        cold_swap_out = None
        cold_swap_in = None
        if swap_cold_before and swap_cold_after:
            cold_swap_out = (
                swap_cold_after.get("swapouts", 0) - swap_cold_before.get("swapouts", 0)
            )
            cold_swap_in = (
                swap_cold_after.get("swapins", 0) - swap_cold_before.get("swapins", 0)
            )
        print(
            f"warm-up (discarded): ok={cold.get('ok')} "
            f"latency={cold.get('latency_ms')}ms swap_out_delta={cold_swap_out}",
            file=sys.stderr,
        )

        surfaces = {
            "admission": admission,
            "capacity": capacity,
            "probe": cold.get("lane_evidence"),
        }
        probe_evidence = extract_lane_evidence(
            {name: payload for name, payload in surfaces.items() if payload is not None}
        )
        reported_ceiling, reported_source = (
            probe_evidence["lane_max_concurrent"],
            probe_evidence["lane_max_concurrent_source"],
        )
        reported_lane = probe_evidence["lane"]

        global_ceilings: dict[str, Any] = {
            "max_active_requests": (admission or {}).get("max_active_requests"),
        }
        if capacity:
            # Only ceilings the broker actually reports are checked; a key it
            # does not publish is left unclaimed rather than assumed.
            for key in ("effective_active_limit", "operator_ceiling"):
                if key in capacity:
                    global_ceilings[key] = capacity.get(key)

        preflight = attest_lane_configuration(
            admission=admission,
            admission_reachable=admission_reachable,
            expected_lane_max_concurrent=expected_lane_limit,
            reported_lane_max_concurrent=reported_ceiling,
            reported_lane_max_concurrent_source=reported_source,
            reported_lane=reported_lane,
            expected_lane=expected_lane,
            global_ceilings=global_ceilings,
            observation=None,
        )
        preflight_failures = preflight.failures
        preflight_unverified = preflight.unverified
        if preflight_failures or preflight_unverified:
            reasons = list(preflight.reasons)
            report = build_report(
                broker=BASE,
                model=MODEL,
                admission=admission,
                admission_reachable=admission_reachable,
                capacity=capacity,
                capacity_reachable=capacity_reachable,
                cold=cold,
                cold_swap_out=cold_swap_out,
                cold_swap_in=cold_swap_in,
                bands=[],
                upstream={},
                rest_state=None,
                rest_reachable=False,
                attestation=preflight,
                failures=preflight_failures,
                unverified=preflight_unverified,
            )
            print(
                "aborting: the target broker/lane configuration could not be attested",
                file=sys.stderr,
            )
            return abort(report)

        results = []
        for concurrency in (1, 2):
            band = await run_band(client, concurrency)
            results.append(band)
            print(
                f"band x{concurrency}: ok={band['success_count']}/{band['concurrency']} "
                f"fail={band['failure_count']} "
                f"agg_tps={band['aggregate_tokens_per_second']} "
                f"lat_p50={band['latency_p50_ms']} "
                f"swap_out_delta={band['swap_out_delta_pages']} "
                f"peak_active={band['admission_observation'].get('peak_active_jobs')} "
                f"peak_queue={band['admission_observation'].get('peak_queue_depth')}",
                file=sys.stderr,
            )

        upstream = await upstream_probe(client)

        rest_state, rest_reachable = await read_admission(client)

    # ── Lane attestation, now including the in-run observation ────────
    observation = {}
    for band in results:
        if band.get("concurrency") == 2:
            observation = band.get("admission_observation") or {}
    attestation = attest_lane_configuration(
        admission=admission,
        admission_reachable=admission_reachable,
        expected_lane_max_concurrent=expected_lane_limit,
        reported_lane_max_concurrent=reported_ceiling,
        reported_lane_max_concurrent_source=reported_source,
        reported_lane=reported_lane,
        expected_lane=expected_lane,
        global_ceilings=global_ceilings,
        observation=observation,
    )

    failures = collect_band_failures(results) + attestation.failures
    unverified = list(attestation.unverified)
    unverified.extend(collect_band_unverified(results))

    cleanup_failures, cleanup_unverified = evaluate_cleanup(rest_state, rest_reachable)
    failures.extend(cleanup_failures)
    unverified.extend(cleanup_unverified)

    report = build_report(
        broker=BASE,
        model=MODEL,
        admission=admission,
        admission_reachable=admission_reachable,
        capacity=capacity,
        capacity_reachable=capacity_reachable,
        cold=cold,
        cold_swap_out=cold_swap_out,
        cold_swap_in=cold_swap_in,
        bands=results,
        upstream=upstream,
        rest_state=rest_state,
        rest_reachable=rest_reachable,
        attestation=attestation,
        failures=failures,
        unverified=unverified,
    )

    print(json.dumps(report, indent=2))
    return verdict_exit_code(report["verdict"])


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
