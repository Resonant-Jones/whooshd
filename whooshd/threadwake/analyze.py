#!/usr/bin/env python3
"""ThreadWake analysis CLI — read-only visibility into the analysis loop.

Usage:
    python -m whooshd.threadwake.analyze

Produces a metadata-only summary of candidates, manifests, and
artifacts.  No inference, no KV tensors, no raw prompts.
"""

from __future__ import annotations

import json
import sys


def main():
    """Run the ThreadWake analysis loop and print results."""
    try:
        from whooshd.runtime.threadwake import ThreadWakeManager
        from whooshd.runtime.threadwake.analysis_loop import ThreadWakeAnalysisLoop

        mgr = ThreadWakeManager()
        loop = ThreadWakeAnalysisLoop(index=mgr._index)

        result = loop.run(limit=50)
        last = loop.last_result() or {}

        output = {
            "analysis": {
                "candidates_scanned": last.get("candidates_scanned", 0),
                "candidates_eligible": last.get("candidates_eligible", 0),
                "manifests_created": last.get("manifests_created", 0),
                "artifacts_registered": last.get("artifacts_registered", 0),
                "skipped": last.get("skipped", 0),
                "errors": last.get("errors", 0),
                "run_count": loop.run_count,
            },
            "threadwake_status": {
                "enabled": mgr.get_health().get("enabled", False),
                "mode": mgr.get_health().get("mode", "off"),
            },
        }
        print(json.dumps(output, indent=2))
    except Exception as exc:
        print(json.dumps({"error": str(exc), "analysis": {}}))
        sys.exit(1)


if __name__ == "__main__":
    main()
