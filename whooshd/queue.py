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
from typing import Any, Optional

from whooshd.config import (
    get_enable_queue,
    get_max_queue_depth,
    get_queue_poll_interval_ms,
    get_queue_timeout_seconds,
)
from whooshd.contracts import CancellationToken, ChatCompletionRequest
from whooshd.correlation import normalize_identifier
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
    upstream_request_id: str | None = None
    # ── Batch execution ────────────────────────────────────────────
    batch_claimed: bool = False
    batch_result_future: Any = None  # asyncio.Future, set at runtime
    # -- Execution ownership --------------------------------------------
    # Set when this entry has been granted for execution.  A granted entry
    # has been removed from the queue and its waiting caller owns it.
    # Excluded from equality so entries stay comparable by request identity
    # rather than by their synchronization primitives.
    granted: bool = field(default=False, compare=False)
    # Woken when :attr:`granted` becomes true, so the owning waiter learns
    # of the grant without waiting for a poll tick.
    grant_event: asyncio.Event = field(
        default_factory=asyncio.Event, compare=False, repr=False
    )
    # -- Route-specific capacity ----------------------------------------
    # The inference lane that will execute this entry and that lane's own
    # concurrency ceiling.  A grant must check the *candidate's* lane, not
    # the granting waiter's: a cache-affinity selection may hand the slot
    # to a request bound for a different lane.
    lane: str | None = field(default=None, compare=False)
    lane_max_concurrent: int | None = field(default=None, compare=False)
    # -- Queue deadline ---------------------------------------------------
    # Monotonic timestamp after which this request may no longer be
    # granted execution.  Stamped at enqueue time.  A selection made by
    # *another* waiter must honour this deadline, not just the deadline
    # of the waiter doing the selecting.
    deadline_at: float | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        self.upstream_request_id = normalize_identifier(self.upstream_request_id)

    def mark_granted(self) -> None:
        """Record that this entry has been granted for execution."""
        self.granted = True
        self.grant_event.set()


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
        Unmatched entries get None (batch error).
        Safe to call multiple times — already-resolved futures are skipped.
        """
        result_map = {rid: resp for rid, resp in results}
        for entry in entries:
            future = entry.batch_result_future
            if future is None:
                continue
            if future.done():
                continue  # Idempotent — already resolved.
            response = result_map.get(entry.request_id)
            future.set_result(response)

    # ── Queue operations ──────────────────────────────────────────────

    @staticmethod
    def _is_expired(entry: Optional[QueueEntry]) -> bool:
        """Whether *entry* has waited past its own queue deadline.

        An entry with no deadline stamped is not expired — the deadline is
        an upper bound on waiting time, never a lower bound.
        """
        if entry is None or entry.deadline_at is None:
            return False
        return time.monotonic() >= entry.deadline_at

    def enqueue(self, entry: QueueEntry) -> None:
        """Append a request to the back of the FIFO queue.

        The entry's **queue deadline** is stamped here: the request may
        wait at most ``timeout_seconds`` from this moment.  Stamping it at
        enqueue time (rather than when a waiter starts polling) gives every
        entry the same deadline regardless of which coroutine happens to
        be selecting on its behalf.
        """
        if entry.deadline_at is None:
            entry.deadline_at = time.monotonic() + self.timeout_seconds
        self._deque.append(entry)

    def dequeue(self) -> Optional[QueueEntry]:
        """Remove and return the oldest entry, or None if empty."""
        if self._deque:
            entry = self._deque.popleft()
            self._scheduler.remove_request(entry.request_id)
            return entry
        return None

    def select_and_dequeue(
        self,
        *,
        capacity_available,  # zero-arg callable returning bool
        entry_eligible=None,  # optional callable(QueueEntry) -> bool
        reserve=None,  # optional callable(QueueEntry) -> None
    ) -> Optional[QueueEntry]:
        """Scheduler-authoritative selection and atomic dequeue.

        This is the **only** method that should drive queued-request
        execution.  It:

        1. Builds the candidate list from the live queue.
        2. Asks the scheduler which request should run next.
        3. Atomically removes the selected entry **by request_id** so
           no position-based race can execute a request twice.
        4. **Reserves the execution slot** for the selected entry before
           handing it over.

        Returns the selected :class:`QueueEntry`, or ``None`` if no
        request is eligible (capacity unavailable, queue empty, or the
        scheduler selected nothing).

        Race safety:
            * If the selected entry was cancelled between scheduling
              and dequeue, ``remove()`` returns ``None`` and we treat
              it as "no eligible request" (the caller should retry).
            * If two callers race to ``select_and_dequeue``, each
              removes a different request_id so no request executes
              twice.
            * ``build_candidates()`` is O(N) but read-only; the atomic
              removal is the synchronisation point.

        Capacity reservation:
            The grant **reserves** capacity rather than only transferring
            ownership.  Without the reservation there is a scheduling gap
            between the grant and the granted waiter's resumption, during
            which ``active_jobs`` does not yet include the granted
            request — and a newly arriving request can be admitted into
            the slot that has already been handed out.  ``reserve`` is
            called synchronously with the removal, so no other coroutine
            can observe the un-reserved state.

        ``entry_eligible`` filters candidates by their **own** lane's
        capacity before the scheduler chooses.  A cache-affinity
        selection may prefer a candidate bound for a different lane, and
        that candidate's lane must have room before it is granted.

        Queue deadline:
            Candidates whose **own** queue deadline has already passed are
            excluded before the scheduler chooses.  A selection made on
            behalf of another waiter must not hand execution to a request
            that has already waited longer than the queue timeout allows —
            otherwise that request would execute after its deadline, which
            the timeout contract forbids.
        """
        candidates = self.build_candidates()
        if not candidates:
            return None
        if not capacity_available():
            # Record the no-op decision so observers see CAPACITY_UNAVAILABLE.
            self._scheduler.choose_next(
                candidates, capacity_available=False
            )
            return None

        by_id = {e.request_id: e for e in self._deque}

        # Expire candidates whose own queue deadline has passed.
        live = [c for c in candidates if not self._is_expired(by_id.get(c.request_id))]
        if not live:
            self._scheduler.choose_next(
                candidates, capacity_available=False
            )
            return None
        candidates = live

        if entry_eligible is not None:
            eligible = [
                c
                for c in candidates
                if entry_eligible(by_id.get(c.request_id))
            ]
            if not eligible:
                self._scheduler.choose_next(
                    candidates, capacity_available=False
                )
                return None
            candidates = eligible

        decision = self._scheduler.choose_next(
            candidates, capacity_available=True
        )
        if decision.request_id is None:
            return None

        # Atomic removal by request_id.  remove() also calls
        # scheduler.remove_request so bypass counts do not leak.
        selected = self.remove(decision.request_id)
        if selected is None:
            # Another waiter claimed it first; nothing was removed here.
            return None

        # Reserve the execution slot.  This is synchronous with the
        # removal above — no await separates them — so the reservation is
        # atomic with respect to every other coroutine.
        if reserve is not None:
            reserve(selected)

        # Execution ownership.  The removed entry is marked granted so its
        # own waiting caller knows it may execute.  Without this, a
        # cache-affinity selection would remove a request that no caller
        # owns: it would neither run nor remain queued.
        selected.mark_granted()
        return selected

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

    async def _wait_for_signal(
        self, entry: QueueEntry, timeout: float
    ) -> None:
        """Wait for a capacity signal, this entry's grant, or the timeout.

        A waiter must wake when *its own* entry is granted, not only when
        the shared capacity event fires: the granting waiter may have
        selected a different candidate, and the granted entry's caller is
        the only one that can execute it.
        """
        if entry.grant_event.is_set():
            return
        tasks = {
            asyncio.ensure_future(self._capacity_event.wait()),
            asyncio.ensure_future(entry.grant_event.wait()),
        }
        try:
            await asyncio.wait(
                tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def wait_for_execution(
        self,
        entry: QueueEntry,
        *,
        cancel_token: Optional[CancellationToken] = None,
        capacity_available: Optional[callable] = None,
        entry_eligible: Optional[callable] = None,
        reserve: Optional[callable] = None,
    ) -> bool:
        """Poll until this entry is at the front of the queue AND capacity
        is available, or until the timeout expires, or cancellation is requested.

        ``capacity_available`` is a zero-arg callable that returns True when
        an active execution slot is free (e.g. ``runtime.active_jobs < max``).
        It expresses the **global** constraint only; per-lane constraints are
        expressed through ``entry_eligible``.

        ``entry_eligible`` is an optional callable that takes a
        :class:`QueueEntry` and returns True when that entry's own lane has
        room.  The scheduler only chooses among eligible candidates, so a
        cache-affinity selection can never grant work to a saturated lane.

        ``reserve`` is an optional callable that takes the granted
        :class:`QueueEntry` and reserves its execution slot.  It is invoked
        atomically with the grant.

        When the entry is at the front and capacity is available, the
        scheduler is consulted for the final selection (which under FIFO
        returns the front entry).  The selection is then committed
        atomically via :meth:`select_and_dequeue` — never via positional
        ``dequeue()`` — so position-based races cannot execute a request
        twice.

        Returns:
            True  — caller should execute the request (already removed).
            False — the request timed out or was cancelled; do not call adapter.
        """
        # The entry's deadline is stamped at enqueue time, so it is the
        # same deadline whoever acts on the entry's behalf.  Fall back to
        # stamping one now for an entry that was never enqueued.
        if entry.deadline_at is None:
            entry.deadline_at = time.monotonic() + self.timeout_seconds
        deadline = entry.deadline_at

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
            # Already granted?  Then this waiter owns the entry — but only
            # if the grant was made in time.  A grant that arrives after
            # this request's own queue deadline has passed must not be
            # honoured: the request has waited longer than the timeout
            # allows, so it is treated as timed out.
            if entry.granted:
                if self._is_expired(entry):
                    self.remove(entry.request_id)
                    logger.info(
                        "queue.grant_expired request_id=%s waited=%.1fs depth_after=%d",
                        entry.request_id,
                        self.timeout_seconds,
                        self.depth,
                    )
                    return False
                return True

            front = self.peek()
            if front is not None and front.request_id == entry.request_id:
                # We are next — check capacity.
                if capacity_available is None or capacity_available():
                    # Scheduler-authoritative selection + atomic dequeue.
                    # Under FIFO (the production default) the scheduler
                    # returns this same entry, so the remove() below
                    # commits the claim by request_id (not position).
                    selected = self.select_and_dequeue(
                        capacity_available=capacity_available or (lambda: True),
                        entry_eligible=entry_eligible,
                        reserve=reserve,
                    )
                    if (
                        selected is not None
                        and selected.request_id == entry.request_id
                    ):
                        return True
                    # Race: the entry was removed between peek() and
                    # select_and_dequeue (e.g. cancellation), or no
                    # candidate was eligible (e.g. every candidate's lane
                    # is saturated).  Treat as "not eligible" — the
                    # caller should not execute.
                    logger.debug(
                        "queue.select_lost request_id=%s selected=%s",
                        entry.request_id,
                        getattr(selected, "request_id", None),
                    )
                    # The scheduler preferred a different candidate
                    # (cache affinity).  That candidate has been removed
                    # and granted, and its own waiting caller owns it.
                    # This entry is still queued and still ours, so we
                    # keep waiting for our turn instead of abandoning it.
                    logger.debug(
                        "queue.select_deferred request_id=%s selected=%s",
                        entry.request_id,
                        getattr(selected, "request_id", None),
                    )
                    await self._wait_for_signal(
                        entry,
                        timeout=min(
                            max(deadline - time.monotonic(), 0.0),
                            self.poll_interval_s,
                        ),
                    )
                    continue
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
                await self._wait_for_signal(
                    entry, timeout=min(remaining, self.poll_interval_s)
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
