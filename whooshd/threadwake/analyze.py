#!/usr/bin/env python3
"""ThreadWake analysis CLI — read-only visibility into the live daemon.

Usage:
    python -m whooshd.threadwake.analyze

Produces a metadata-only summary of candidates, manifests, and
artifacts from the running Whoosh'd daemon.  No inference, no KV tensors,
no raw prompts.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
ANALYSIS_PATH = "/runtime/threadwake/analysis"


def _analysis_url(base_url: str) -> str:
    """Build the daemon analysis URL from a root Whoosh'd base URL."""
    return f"{base_url.rstrip('/')}{ANALYSIS_PATH}"


def fetch_analysis(
    base_url: str,
    timeout: float = 5.0,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    """Fetch live ThreadWake analysis from the running daemon."""
    request = Request(
        _analysis_url(base_url),
        headers={"Accept": "application/json"},
        method="GET",
    )
    with opener(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("daemon returned a non-object JSON response")
    return data


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch live ThreadWake analysis from a running Whoosh'd daemon.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("WHOOSHD_BASE_URL", DEFAULT_BASE_URL),
        help=f"Whoosh'd daemon root URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="HTTP timeout in seconds (default: 5)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Fetch the live ThreadWake analysis report and print results."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        output = fetch_analysis(args.base_url, timeout=args.timeout)
        print(json.dumps(output, indent=2))
        return 0
    except HTTPError as exc:
        print(
            json.dumps(
                {"error": f"daemon returned HTTP {exc.code}", "analysis": {}},
            ),
        )
        return 1
    except URLError as exc:
        print(
            json.dumps(
                {
                    "error": f"could not reach Whoosh'd daemon: {exc.reason}",
                    "analysis": {},
                },
            ),
        )
        return 1
    except Exception as exc:
        print(json.dumps({"error": str(exc), "analysis": {}}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
