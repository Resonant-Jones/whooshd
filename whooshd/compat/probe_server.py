"""Provider smoke-test probe.

Validates a running Whoosh'd server from the outside as a
Codexify-compatible local provider.  Test-facing for now;
can grow a thin CLI wrapper later.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ProviderSmokeResult:
    """Result of a full provider smoke test."""

    ok: bool = False
    health_ok: bool = False
    ready: bool = False
    readiness_http_status: int = 0
    readiness_reason: str | None = None
    openai_models_ok: bool = False
    ollama_tags_ok: bool = False
    non_streaming_chat_ok: bool = False
    streaming_chat_ok: bool = False
    streaming_visible_text: str | None = None
    errors: list[str] = field(default_factory=list)


async def smoke_test_server(
    client: httpx.AsyncClient,
    *,
    model: str = "qwen2.5-1.5b-instruct-mlx",
) -> ProviderSmokeResult:
    """Run the full provider smoke suite against a running Whoosh'd server.

    Args:
        client: An ``httpx.AsyncClient`` pointed at the server's base URL.
        model: Model ID to use for chat completion probes.

    Returns a ``ProviderSmokeResult`` with per-endpoint pass/fail status
    and any accumulated error messages.
    """
    import httpx

    from whooshd.compat.codexify_probe import reconstruct_assistant_text

    result = ProviderSmokeResult()
    errors: list[str] = []

    # ── /health ────────────────────────────────────────────────────────
    try:
        resp = await client.get("/health")
        body = resp.json()
        if resp.status_code == 200 and body.get("ok") is True:
            result.health_ok = True
        else:
            errors.append(f"/health returned status {resp.status_code}")
    except Exception as exc:
        errors.append(f"/health probe failed: {exc}")

    # ── /ready ─────────────────────────────────────────────────────────
    try:
        resp = await client.get("/ready")
        body = resp.json()
        result.readiness_http_status = resp.status_code
        result.ready = body.get("ready", False)
        result.readiness_reason = body.get("reason")
    except Exception as exc:
        errors.append(f"/ready probe failed: {exc}")

    # ── /v1/models ─────────────────────────────────────────────────────
    try:
        resp = await client.get("/v1/models")
        body = resp.json()
        if resp.status_code == 200 and len(body.get("data", [])) >= 1:
            result.openai_models_ok = True
        else:
            errors.append("/v1/models returned empty or non-200")
    except Exception as exc:
        errors.append(f"/v1/models probe failed: {exc}")

    # ── /api/tags ──────────────────────────────────────────────────────
    try:
        resp = await client.get("/api/tags")
        body = resp.json()
        if resp.status_code == 200 and len(body.get("models", [])) >= 1:
            result.ollama_tags_ok = True
        else:
            errors.append("/api/tags returned empty or non-200")
    except Exception as exc:
        errors.append(f"/api/tags probe failed: {exc}")

    # ── Non-streaming chat ─────────────────────────────────────────────
    try:
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Hello from smoke test."}],
                "stream": False,
            },
        )
        body = resp.json()
        if (
            resp.status_code == 200
            and body.get("object") == "chat.completion"
            and len(body.get("choices", [])) >= 1
        ):
            result.non_streaming_chat_ok = True
        else:
            errors.append(f"non-streaming chat returned status {resp.status_code}")
    except Exception as exc:
        errors.append(f"non-streaming chat probe failed: {exc}")

    # ── Streaming chat ─────────────────────────────────────────────────
    try:
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Hello from smoke test."}],
                "stream": True,
            },
        )
        body_text = resp.text
        if resp.status_code == 200 and "text/event-stream" in resp.headers.get(
            "content-type", ""
        ):
            visible = reconstruct_assistant_text(body_text)
            result.streaming_visible_text = visible
            result.streaming_chat_ok = True
        else:
            errors.append(f"streaming chat returned status {resp.status_code}")
    except Exception as exc:
        errors.append(f"streaming chat probe failed: {exc}")

    # ── Final verdict ──────────────────────────────────────────────────
    result.errors = errors
    result.ok = (
        result.health_ok
        and result.openai_models_ok
        and result.ollama_tags_ok
        and result.non_streaming_chat_ok
        and result.streaming_chat_ok
        and len(errors) == 0
    )

    return result


# ── CLI entry point (python -m whooshd.compat.probe_server) ─────────────────


async def _main() -> None:
    """Thin CLI wrapper for the smoke probe."""
    import argparse
    import httpx

    parser = argparse.ArgumentParser(description="Whoosh'd Provider Smoke Test")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="Base URL of the Whoosh'd server (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--model",
        default="stub-model",
        help="Model ID for chat probes (default: stub-model)",
    )
    args = parser.parse_args()

    async with httpx.AsyncClient(base_url=args.base_url, timeout=30) as client:
        result = await smoke_test_server(client, model=args.model)

    print("Whoosh'd Provider Smoke Test\n")
    print(f"health: {'ok' if result.health_ok else 'FAIL'}")
    print(
        f"ready: {'ok' if result.ready else f'not ready ({result.readiness_reason})'}"
    )
    print(f"openai models: {'ok' if result.openai_models_ok else 'FAIL'}")
    print(f"ollama tags: {'ok' if result.ollama_tags_ok else 'FAIL'}")
    print(f"non-streaming chat: {'ok' if result.non_streaming_chat_ok else 'FAIL'}")
    print(f"streaming chat: {'ok' if result.streaming_chat_ok else 'FAIL'}")

    if result.errors:
        print(f"\nerrors ({len(result.errors)}):")
        for err in result.errors:
            print(f"  - {err}")

    print(f"\nresult: {'pass' if result.ok else 'fail'}")
    raise SystemExit(0 if result.ok else 1)


if __name__ == "__main__":
    import asyncio

    asyncio.run(_main())
