# Whoosh'd Local launchd Runtime

## Problem

The existing root-owned launchd plist for Whoosh'd hardcodes an old direct MLX
configuration:

- `WHOOSHD_ADAPTER=mlx`
- `WHOOSHD_MLX_MODEL=zecanard/gemma-4-E4B-it-ultra-uncensored-heretic-MLX-4bit-mixed_4_6`
- `WHOOSHD_ROOT=/Volumes/Dev_SSD/ResonantConstructs/Whoosh'd`

That configuration is stale for the current registry-backed Model Bay posture.
The working Gemma 4 12B path depends on two cooperating local daemons:

- Whoosh'd proxy on `http://127.0.0.1:8000`
- Gemma 12B `mlx_vlm` upstream on `http://127.0.0.1:8082`

Launchd process presence is not proof. Required proof is:

- upstream `mlx_vlm` inventory reachable
- Whoosh'd `/v1/models` contains `gemma-4-12b-it-qat-4bit`
- real `POST /v1/chat/completions` through the alias returns the expected text

## Files

- `ops/launchd/com.resonant.whooshd.plist.template`
- `ops/launchd/com.resonant.mlx-vlm-gemma12b.plist.template`
- `ops/launchd/render_launchd_plists.py`
- `ops/launchd/install_local_launchd.sh`
- `scripts/smoke/whooshd_12b_smoke.sh`

## Design

The persistence plan is intentionally split:

- `com.resonant.whooshd`
  - owns the OpenAI-compatible proxy on port `8000`
  - points at the runtime registry in `configs/models.yaml`
  - does not hardcode the old E4B model in launchd env
  - binds `127.0.0.1:8000` by default in the launchd bundle
  - uses explicit `--host` / `--port` launcher args instead of `--codexify`
- `com.resonant.mlx-vlm-gemma12b`
  - owns the Gemma 12B `mlx_vlm` server on `127.0.0.1:8082`
  - launches the exact working command shape: `python -m mlx_vlm server --model ...`

This preserves Whoosh'd as the single Model Bay/proxy endpoint while making
the 12B upstream repeatable across reboot/login.

On this machine, Docker containers can still reach loopback-bound host services
through `host.docker.internal`. That is already proven by the launchd-owned
Gemma 12B sidecar on `127.0.0.1:8082`, which is reachable from the Codexify
backend container at `http://host.docker.internal:8082/v1/models`.

The bundle intentionally avoids `whooshd --codexify` because the current local
launcher implementation forces `0.0.0.0:8000` when that flag is present. For
the launchd path we want the bind host to remain renderer-controlled.

The Whoosh'd Python interpreter is also renderer-controlled. It has no implicit
default: the operator must provide one absolute machine-local path, and that
exact path is written to the generated plist as `WHOOSHD_PYTHON`. The renderer
does not select `.venv`, `.venv311`, system Python, or a `PATH` candidate.

## Render

From the Whoosh'd repo root:

```bash
WHOOSHD_PYTHON="$PWD/.venv311/bin/python"

python3 ops/launchd/render_launchd_plists.py \
  --output-dir .local/launchd \
  --whooshd-root "/Volumes/Dev_SSD/ResonantConstructs/Whoosh'd" \
  --whooshd-python "$WHOOSHD_PYTHON" \
  --user chriscastillo \
  --dry-run
```

This writes concrete plists into `.local/launchd/` and prints the exact
install commands without calling `sudo`.

The selected interpreter must be an absolute path to an existing executable.
Before rendering, it is launched from the Whoosh'd repository root and must
successfully import `fastapi`, `uvicorn`, `pydantic_core._pydantic_core`, and
`whooshd.app`. Any missing path, non-executable file, failed import, native
extension failure, timeout, or missing success marker stops rendering. VaultNode
currently proves `.venv311/bin/python`; that is machine-local evidence, not a
portable repository-wide interpreter requirement.

## Validate

```bash
plutil -lint .local/launchd/com.resonant.whooshd.plist
plutil -lint .local/launchd/com.resonant.mlx-vlm-gemma12b.plist
bash -n ops/launchd/install_local_launchd.sh
bash -n scripts/smoke/whooshd_12b_smoke.sh
```

## Install

Dry-run first:

```bash
bash ops/launchd/install_local_launchd.sh
```

Then install:

```bash
bash ops/launchd/install_local_launchd.sh install
```

The installer:

- validates both generated plists with `plutil -lint`
- reads `WHOOSHD_PYTHON` and `WHOOSHD_ROOT` back from the rendered Whoosh'd plist
- repeats the complete interpreter/import preflight before the first `sudo`
- validates the installed target paths and any existing plist ownership/mode
- acquires a machine-local exclusion lock before inspecting or mutating launchd
- classifies both exact system service targets before transition
- backs up the existing `com.resonant.whooshd.plist` before replacement
- copies both plists into `/Library/LaunchDaemons/`
- sets `root:wheel` ownership and `0644` permissions
- verifies each required `bootout` by polling until the exact service is absent
- reconciles every `bootstrap` exit status against a post-command service query
- confirms both jobs are registered before either job is kickstarted
- verifies both jobs remain registered before reporting convergence

It never stores credentials and relies on operator-provided `sudo`.

Interpreter validation occurs before any installed plist is copied and before
either service is unloaded. A stale, missing, non-executable, or incompatible
machine-local Python therefore fails closed without converting a running service
into an outage.

## Convergent two-service installation

Installation treats Whoosh'd and MLX-VLM as one bundle. An exact-target
`launchctl print system/<label>` query supplies the registration decision:

- exit `0`: registered; a bounded `state = running` match is reported only as a
  runtime hint
- exit `113`: absent
- any other exit: indeterminate, so mutation stops

The installer does not use the rest of `launchctl print` as a stable data
format and does not print its environment-bearing output. Before removing a
registered job, it polls that exact target until absence is confirmed. The
poll has a bounded attempt count and interval; an arbitrary delay is never the
sole removal test.

After each bootstrap, the installer queries the exact service even when
bootstrap returned nonzero. A nonzero result such as error `5` is reconciled
when the intended service is demonstrably registered. Conversely, exit `0`
does not count as success when the job remains absent or indeterminate. Both
jobs must be registered before either kickstart, and both must remain
registered before installation is reported as converged.

The machine-local lock defaults to:

```text
/tmp/com.resonant.whooshd-launchd-installer.lock
```

A concurrent invocation fails before privileged mutation. The lock is removed
on normal exit and handled signals. If a process is forcibly killed and leaves
a stale directory, first verify that no installer process is running, then
remove only that exact lock directory before retrying.

### Partial-state recovery

When either service fails transition, the installer returns nonzero and prints:

- the failing stage and label
- bounded launchctl status and error interpretation
- the post-command registered, absent, or indeterminate state
- the companion service state
- the supported recovery path

Do not blindly loop the installer, manually start a competing process, or
restore the historical backup plist. Resolve the reported launchctl condition,
then invoke the same installer once; it will reclassify both service targets and
converge from registered, absent, or mixed state. Error `5` by itself does not
prove whether launchd registered a job—the post-command query is authoritative
for that installation decision.

Installer convergence proves registered service definitions only. Listener
containment, health, Docker bridge access, non-loopback denial, model inventory,
real generation, and restart stability remain mandatory in the separate live
containment proof.

## Diagnose Python preflight failures

Use the same absolute value supplied to `--whooshd-python`:

```bash
WHOOSHD_PYTHON="$PWD/.venv311/bin/python"

"$WHOOSHD_PYTHON" -c '
import fastapi
import uvicorn
import pydantic_core._pydantic_core
import whooshd.app
print("Whoosh launchd Python imports: OK")
'

python3 ops/launchd/validate_whooshd_python.py \
  --python "$WHOOSHD_PYTHON" \
  --whooshd-root "$PWD"
```

If the preflight fails, repair or deliberately replace that machine-local
environment, then render again with its explicit absolute path. Do not rename or
remove another environment to trigger launcher fallback behavior.

## Proof Commands

Check upstream:

```bash
curl -fsS http://127.0.0.1:8082/v1/models
```

Check Whoosh'd inventory:

```bash
curl -fsS http://127.0.0.1:8000/v1/models
```

Run focused smoke:

```bash
bash scripts/smoke/whooshd_12b_smoke.sh
```

Expected endpoints:

- Whoosh'd proxy: `http://127.0.0.1:8000`
- Gemma 12B upstream: `http://127.0.0.1:8082`

Dockerized Codexify services should continue using:

- `LOCAL_BASE_URL=http://host.docker.internal:8000/v1`
- `VAULTNODE_BASE_URL=http://host.docker.internal:8000`

## Rollback boundary

The installer does not automatically restore its backup because historical
Whoosh'd plists may reintroduce `--codexify` or a wildcard listener. Treat the
backup as evidence, not as a known-safe executable rollback. Any future
rollback must first be audited for explicit loopback binding, the intended
Python interpreter, registry continuity, and paired-sidecar behavior.
