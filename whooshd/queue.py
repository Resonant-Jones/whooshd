"""Bounded FIFO request queue for admission overflow.

When WHOOSHD_ENABLE_QUEUE is true and the active request limit is reached,
structurally-valid requests are enqueued instead of being rejected immediately.

The queue is polled for capacity — when an active slot opens, the oldest
waiting request is dequeued and executed.  Requests waiting longer than
the timeout are marked ``timed_out`` and removed without calling the adapter.

This module is deliberately small: no priority lanes, no batching,
no prompt-prefix caching, no durable snapshots.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from whooshd.config import (
    get_enable_queue,
    get_max_queue_depth,
    get_queue_poll_interval_ms,
    get_queue_timeout_seconds,
)
from whooshd.contracts import CancellationToken, ChatCompletionRequest
from whooshd.scheduler import (
    Scheduler,
    SchedulerCandidate,
)

logger = logging.getLogger(__name__)


@dataclass
class QueueEntry:
    """A single request waiting in the queue."""
    request_id: str
    request: ChatCompletionRequest
    enqueued_at: float = field(default_factory=time.time)
    # ── Batch execution ────────────────────────────────────────────
    batch_claimed: bool = False
    batch_result_future: Any = None  # asyncio.Future, set at runtime


class RequestQueue:
    """Bounded FIFO queue for requests waiting for active capacity.

    Thread-safe for asyncio use, but not multiprocess-safe.
    """

    def __init__(self) -> None:
        self._deque: deque[QueueEntry] = deque()
        self._capacity_event = asyncio.Event()
        self._scheduler = Scheduler()

    # ── Properties ────────────────────────────────────────────────────

    @property
    def depth(self) -> int:
        return len(self._deque)

    @property
    def enabled(self) -> bool:
        return get_enable_queue()

    @property
    def max_depth(self) -> int:
        return get_max_queue_depth()

    @property
    def timeout_seconds(self) -> float:
        return get_queue_timeout_seconds()

    @property
    def poll_interval_s(self) -> float:
        return get_queue_poll_interval_ms() / 1000.0

    @property
    def is_full(self) -> bool:
        return self.depth >= self.max_depth

    @property
    def scheduler(self) -> Scheduler:
        return self._scheduler

    def build_candidates(self) -> list[SchedulerCandidate]:
        """Build scheduler candidates from the current queue contents.

        Only safe metadata is included — no prompts, messages, or opaque refs.
        """
        return [
            SchedulerCandidate(
                request_id=e.request_id,
                queued_at=e.enqueued_at,
                model=getattr(e.request, "model", None),
                stream=getattr(e.request, "stream", False),
                threadwake_cache_ready=getattr(e, "cache_ready", False),
                bypass_count=self._scheduler._bypass_counts.get(e.request_id, 0),
            )
            for e in self._deque
        ]

    def build_batch_candidates(self) -> list:
        """Build safe batching analysis candidates from queue entries.

        Returns only metadata — no prompts, messages, or opaque refs.
        """
        from whooshd.batching import BatchCandidate
        return [
            BatchCandidate(
                request_id=e.request_id,
                queued_at=e.enqueued_at,
                model=getattr(e.request, "model", "unknown"),
                stream=getattr(e.request, "stream", False),
            )
            for e in self._deque
        ]

    # ── Batch group and claiming ─────────────────────────────────────

    def find_batch_group(
        self,
        selected_request_id: str,
        *,
        analyzer: Any,
        backend: str | None,
        enabled: bool = False,
    ) -> list[QueueEntry] | None:
        """Find an eligible batch group containing the selected entry.

        Returns a list of QueueEntry in queue order, or None if batching
        is not possible for the selected entry.
        """
        if not enabled:
            return None

        candidates = self.build_batch_candidates()
        analysis = analyzer.analyze(candidates, enabled=enabled)

        for group in analysis.groups:
            if group.eligible and selected_request_id in group.request_ids:
                entries = [
                    e for e in self._deque
                    if e.request_id in group.request_ids
                ]
                if len(entries) >= 2:
                    return entries
        return None

    def claim_batch_entries(
        self,
        entries: list[QueueEntry],
    ) -> list[asyncio.Future]:
        """Atomically claim entries for batch execution.

        Sets ``batch_claimed=True`` and creates a ``batch_result_future``
        for each entry.  Returns the futures in the same order as entries.
        """
        futures: list[asyncio.Future] = []
        for entry in entries:
            if entry.batch_claimed:
                continue
            entry.batch_claimed = True
            future = asyncio.get_event_loop().create_future()
            entry.batch_result_future = future
            futures.append(future)
        return futures

    def resolve_batch_results(
        self,
        entries: list[QueueEntry],
        results: list[tuple[str, Any]],
    ) -> None:
        """Resolve batch result futures with mapped responses.

        ``results`` is a list of (request_id, response) tuples.
        Unmatched entries get None.
        """
        result_map = {rid: resp for rid, resp in results}
        for entry in entries:
            future = entry.batch_result_future
            if future is not None and not future.done():
                response = result_map.get(entry.request_id)
                future.set_result(response)

    # ── Queue operations ──────────────────────────────────────────────

    def enqueue(self, entry: QueueEntry) -> None:
        """Append a request to the back of the FIFO queue."""
        self._deque.append(entry)

    def dequeue(self) -> Optional[QueueEntry]:
        """Remove and return the oldest entry, or None if empty."""
        if self._deque:
            entry = self._deque.popleft()
            self._scheduler.remove_request(entry.request_id)
            return entry
        return None

    def remove(self, request_id: str) -> Optional[QueueEntry]:
        """Remove a specific request by ID (for cancellation).

        Returns the removed entry, or None if not found.
        """
        for i, entry in enumerate(self._deque):
            if entry.request_id == request_id:
                del self._deque[i]
                self._scheduler.remove_request(request_id)
                return entry
        return None

    def peek(self) -> Optional[QueueEntry]:
        """Return the oldest entry without removing it."""
        if self._deque:
            return self._deque[0]
        return None

    def oldest_age_ms(self) -> float:
        """Age in milliseconds of the oldest queued request, or 0."""
        entry = self.peek()
        if entry is None:
            return 0.0
        return (time.time() - entry.enqueued_at) * 1000.0

    # ── Capacity signaling ────────────────────────────────────────────

    def notify_capacity(self) -> None:
        """Signal that an active slot may have opened.

        Wakes all waiters; each will re-check capacity and dequeue
        if they are next in line.
        """
        self._capacity_event.set()

    def _clear_capacity_signal(self) -> None:
        self._capacity_event.clear()

    # ── Waiting ───────────────────────────────────────────────────────

    async def wait_for_execution(
        self,
        entry: QueueEntry,
        *,
        cancel_token: Optional[CancellationToken] = None,
        capacity_available: Optional[callable] = None,
    ) -> bool:
        """Poll until this entry is at the front of the queue AND capacity
        is available, or until the timeout expires, or cancellation is requested.

        ``capacity_available`` is a zero-arg callable that returns True when
        an active execution slot is free (e.g. ``runtime.active_jobs < max``).

        Returns:
            True  — caller should dequeue and execute the request.
            False — the request timed out or was cancelled; do not call adapter.
        """
        deadline = time.monotonic() + self.timeout_seconds

        while True:
            # ── Check cancellation before anything else ────────────
            if cancel_token is not None and cancel_token.is_cancelled():
                removed = self.remove(entry.request_id)
                if removed is not None:
                    logger.debug(
                        "queue.cancelled request_id=%s depth_after=%d",
                        entry.request_id,
                        self.depth,
                    )
                return False

            # ── Check if we are at the front of the queue ──────────
            front = self.peek()
            if front is not None and front.request_id == entry.request_id:
                # We are next — check capacity.
                if capacity_available is None or capacity_available():
                    # Dequeue ourselves and return success.
                    dequeued = self.dequeue()
                    if dequeued is not None and dequeued.request_id == entry.request_id:
                        return True
                    # Unexpected: someone else dequeued us?  Treat as timeout.
                    logger.warning(
                        "queue.dequeue_mismatch expected=%s got=%s",
                        entry.request_id,
                        dequeued.request_id if dequeued else None,
                    )
                    return False
                # At front but no capacity — wait for capacity signal.

            # ── Check timeout ──────────────────────────────────────
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                removed = self.remove(entry.request_id)
                if removed is not None:
                    logger.info(
                        "queue.timeout request_id=%s waited=%.1fs depth_after=%d",
                        entry.request_id,
                        self.timeout_seconds,
                        self.depth,
                    )
                return False

            # ── Wait for capacity signal or poll interval ──────────
            try:
                await asyncio.wait_for(
                    self._capacity_event.wait(),
                    timeout=min(remaining, self.poll_interval_s),
                )
                self._clear_capacity_signal()
            except asyncio.TimeoutError:
                # Poll interval elapsed — loop back and re-check.
                pass


# Module-level singleton for the app to import.
_queue: Optional[RequestQueue] = None


def get_queue() -> RequestQueue:
    global _queue
    if _queue is None:
        _queue = RequestQueue()
    return _queue
