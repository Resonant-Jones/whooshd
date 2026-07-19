# Whoosh'd Loopback Deployment Containment Proof

## Current outcome

`PASS`

CWC-H01B-R3 completed the convergent installation and every mandatory
containment, availability, Docker bridge, real-generation, and restart-stability
probe from exact local source HEAD
`6b7b65513e1c4e8ee95e3303f8dbf18614fdea4c`. The live service pair is now
registered, healthy, and bound only to loopback. See the fifth execution section
for the current proof. All earlier BLOCKED and FAIL attempts remain below as the
historical repair record.

## First attempt outcome

`BLOCKED`

The repository-supported loopback plist rendered and validated successfully, but the system-domain installer could not pass the required macOS administrator authorization boundary. The installed service was not changed. It still uses `--codexify`, declares `WHOOSHD_HOST=0.0.0.0`, and owns a wildcard `*:8000` listener. No containment claim is made.

## Capture window

- Date: 2026-07-18
- Window: 16:05:04-16:09:27 EDT
- Deployment mode being restored: `localhost single-user`
- Evidence class: exact-source inspection plus live process/listener inspection; post-repair proof was not reached

## Repository identity

- Repository: Whoosh'd
- Branch: `codex/authoritative-runtime-registry`
- Repository HEAD used for reconciliation: `ac3f2cf4a91be32c633e1b1ecae17dc6b6fe736e`
- Remote: `origin` -> `https://github.com/Resonant-Jones/whooshd.git`
- Worktree before proof: branch tracked its same-named origin branch and had one unrelated untracked `.venv311/` directory
- The unrelated `.venv311/` directory was not modified or staged by this task.

## LaunchDaemon and process identity

- Label: `com.resonant.whooshd`
- Installed plist: `/Library/LaunchDaemons/com.resonant.whooshd.plist`
- Installed launcher: `/Users/chriscastillo/.local/bin/whooshd`
- Working directory: `/Volumes/Dev_SSD/ResonantConstructs/Whoosh'd`
- Installed arguments: `/Users/chriscastillo/.local/bin/whooshd --codexify`
- Installed bind environment: `WHOOSHD_HOST=0.0.0.0`, `WHOOSHD_PORT=8000`
- Confirmed PID: `978`
- Process owner: `chriscastillo`
- Process start time: `Fri Jul 17 14:35:42 2026`
- Effective process command: Python 3.11 running `uvicorn whooshd.app:app --host 0.0.0.0 --port 8000`

The executable, launchd label, working directory, launcher arguments, PID, and port owner all identified the intended installed Whoosh'd service before any repair was attempted.

## Listener containment

### Before repair

`lsof -nP -iTCP:8000 -sTCP:LISTEN` returned PID `978` with `TCP *:8000 (LISTEN)`.

Result: `FAIL`. The intended unauthenticated service was configured and bound as a wildcard listener.

### After repair

Not reached. Administrator authorization failed twice before the repository installer could copy or reload the system plist. A final re-probe still returned PID `978` with `TCP *:8000 (LISTEN)` and the installed plist still contained `--codexify` plus `WHOOSHD_HOST=0.0.0.0`.

Result: `BLOCKED`.

## Repair procedure

The documented procedure in `docs/ops/whooshd-launchd-local-runtime.md` was followed up to the privilege boundary:

1. Inspected the installed plist, launcher, launchd job, process identity, and listener.
2. Rendered the current repository templates into a temporary directory with explicit `--host 127.0.0.1 --port 8000` behavior.
3. Used the renderer's supported `--model-registry-path configs/models.friends-family-guest.yaml` option to preserve the installed registry target rather than change model inventory as part of this containment task.
4. Validated both rendered plists with `plutil` and ran the installer in dry-run mode.
5. Invoked `ops/launchd/install_local_launchd.sh install`. Terminal `sudo` stopped at the administrator-password boundary and was cancelled without supplying a password to the task.
6. Retried the same repository installer through the macOS native administrator authorization dialog twice. Both attempts returned error `-60007` (`administrator user name or password was incorrect`).
7. Re-probed the installed plist, launchd job, and listener. They were unchanged.

No installed plist, launcher, source file, model registry, adapter, queue, ThreadWake, Codexify, or user-data file was hand-edited.

### Runbook deviations

- Rendered output was placed in a temporary directory and supplied through the installer's supported `OUTPUT_DIR` variable instead of the repository-local `.local/launchd/` default.
- The supported renderer option for the already-installed friends/family registry path was supplied to avoid changing model inventory.
- macOS native administrator authorization was attempted only as a credential-safe way to run the same documented installer; it did not succeed and caused no installed change.

The runbook remains factually sufficient to render and validate the intended loopback plist. Completion requires an operator who can satisfy its explicitly documented `sudo` boundary.

## Loopback health

Before repair, `curl -fsS --max-time 5 http://127.0.0.1:8000/health` failed to connect even though `lsof` still reported the wildcard listener.

Post-repair loopback health was not run because the repair did not occur.

Result: `BLOCKED`. This is process-liveness evidence only; it is not model readiness evidence.

## Docker bridge

Post-repair `host.docker.internal` health was not run because the repair did not occur. The sandboxed preliminary Docker daemon check also lacked access to the Docker socket; that environmental result is not treated as deployment evidence.

Result: `BLOCKED`.

## Non-loopback denial

Post-repair denial was not run because the repair did not occur. The service remained configured for `0.0.0.0` and `lsof` continued to report `*:8000`, which is already a containment failure regardless of HTTP status.

No personal LAN or Tailscale address is recorded in this artifact.

Result: `BLOCKED`.

## Restart stability

The required post-repair restart and repeated containment probes were not run because the initial repair could not cross the administrator authorization boundary.

Result: `BLOCKED`.

## Exact commands used

Personal network addresses were never printed or committed. Environment output was bounded to relevant plist fields.

```bash
git status --short --branch
git rev-parse HEAD
git remote -v
plutil -p /Library/LaunchDaemons/com.resonant.whooshd.plist
launchctl print system/com.resonant.whooshd
sed -n '1,220p' /Users/chriscastillo/.local/bin/whooshd
lsof -nP -iTCP:8000 -sTCP:LISTEN
ps -o pid=,ppid=,user=,lstart=,command= -p 978
curl -fsS --max-time 5 http://127.0.0.1:8000/health

python3 ops/launchd/render_launchd_plists.py \
  --output-dir '<temporary-directory>' \
  --whooshd-root "/Volumes/Dev_SSD/ResonantConstructs/Whoosh'd" \
  --user chriscastillo \
  --model-registry-path configs/models.friends-family-guest.yaml \
  --dry-run

OUTPUT_DIR='<temporary-directory>' bash ops/launchd/install_local_launchd.sh dry-run
OUTPUT_DIR='<temporary-directory>' bash ops/launchd/install_local_launchd.sh install

plutil -extract ProgramArguments json -o - /Library/LaunchDaemons/com.resonant.whooshd.plist
plutil -extract EnvironmentVariables.WHOOSHD_HOST raw -o - /Library/LaunchDaemons/com.resonant.whooshd.plist
```

## Not proven

- Loopback-only containment is not proven and is not currently true.
- Localhost health after repair is not proven.
- Docker bridge reachability through `host.docker.internal` after repair is not proven.
- Non-loopback denial after repair is not proven.
- Restart stability is not proven.
- Model generation and model readiness were intentionally not tested or proven.
- Authenticated LAN, Tailscale, sidecar trust, remote-node, service-authentication, and mTLS deployment modes remain unproven and unsupported by this artifact.

## Resume condition

From the Whoosh'd repository root, an authorized local operator must run the already-rendered or freshly rendered repository installer and satisfy macOS administrator authorization. The complete listener, loopback health, Docker bridge, non-loopback denial, and restart-stability sequence must then be repeated against the same recorded source HEAD (or a new exact HEAD must be recorded). Until that happens, this proof must remain `BLOCKED` and the wildcard listener must be treated as an unresolved P0 containment issue.

---

## Second attempt — 2026-07-18

### Final outcome

Outcome: `BLOCKED`

Administrator authorization failed before the checked-in installer executed. The installed LaunchDaemon and live process were not changed. The unauthenticated wildcard listener remains an unresolved P0 containment issue.

### Execution window and repository identity

- Window: 17:08:48-17:09:56 EDT
- Branch: `codex/authoritative-runtime-registry`
- Exact HEAD tested: `552544a9df6b51de9445f279aa50987452f2e395`
- Previous proof commit: `552544a9df6b51de9445f279aa50987452f2e395`
- Relationship: the second attempt began directly on the committed first-attempt proof; `git merge-base --is-ancestor` confirmed that proof commit in the tested ancestry.
- Initial worktree status: branch was one commit ahead of its same-named origin branch with only the unrelated untracked `.venv311/` directory.
- Installed artifact relationship: the generated replacement plists came from the tested source HEAD. They were not installed because authorization failed.

### Administrator authorization

The repository installer was prepared and validated, then invoked through the macOS native administrator authorization dialog so no credential would enter task output or the proof. macOS returned error `-60007` (`administrator user name or password was incorrect`) before the shell command ran.

Result: `BLOCKED`. In accordance with the task invariant, no retry, privilege workaround, manual plist edit, unload, or process termination was attempted after this failure.

### Service identity before repair

- LaunchDaemon: `system/com.resonant.whooshd`
- Installed plist: `/Library/LaunchDaemons/com.resonant.whooshd.plist`
- Launcher: `/Users/chriscastillo/.local/bin/whooshd`
- Arguments: `/Users/chriscastillo/.local/bin/whooshd --codexify`
- Installed bind environment: `WHOOSHD_HOST=0.0.0.0`, `WHOOSHD_PORT=8000`
- Working directory: `/Volumes/Dev_SSD/ResonantConstructs/Whoosh'd`
- Service state: `running`
- PID: `978`
- Process owner: `chriscastillo`
- Process start time: `Fri Jul 17 14:35:42 2026`
- Effective command: Python 3.11 running `uvicorn whooshd.app:app --host 0.0.0.0 --port 8000`
- Listener: PID `978`, `TCP *:8000 (LISTEN)`
- Loopback health: connection failed with curl exit `7`

The LaunchDaemon definition, PID 1 parent, executable command, working directory, and port owner confirmed the intended Whoosh'd service before the mutation gate.

### Documented repair procedure attempted

1. Re-read this proof, `docs/ops/whooshd-launchd-local-runtime.md`, `README.md`, and `docs/architecture.md` at the tested HEAD. No ADR index or architecture-decision directory was present.
2. Re-inspected repository status, proof ancestry, the installed plist, launchd state, PID identity, listener, and loopback health.
3. Rendered both checked-in launchd templates into `.local/launchd/` with explicit `--host 127.0.0.1 --port 8000` behavior.
4. Supplied the renderer's supported `--model-registry-path configs/models.friends-family-guest.yaml` option to preserve the installed registry target rather than change model inventory.
5. Validated both generated plists with `plutil -lint`, syntax-checked the installer and smoke script with `bash -n`, and completed the checked-in installer's dry-run successfully.
6. Invoked the checked-in installer through the native macOS administrator authorization gate.
7. Authorization failed before installer execution. The procedure stopped immediately.
8. Re-probed the installed plist, service state, listener, loopback health, and repository status.

Generated `.local/launchd/` files are ignored machine-local output. No source, model registry, adapter, queue, ThreadWake, Codexify, virtual-environment, or user-data file was modified.

### Service identity after the blocked repair

The state was unchanged:

- LaunchDaemon state: `running`
- PID: `978`
- Installed arguments: `/Users/chriscastillo/.local/bin/whooshd --codexify`
- Installed host: `WHOOSHD_HOST=0.0.0.0`
- Listener: PID `978`, `TCP *:8000 (LISTEN)`

Listener containment result: `BLOCKED`; loopback-only containment is not true.

### Loopback health

Both the pre-mutation and final probes used:

`curl -fsS --connect-timeout 2 --max-time 5 http://127.0.0.1:8000/health`

Both failed to connect with curl exit `7`.

Result: `BLOCKED`. This does not establish model readiness or generation behavior.

### Docker bridge

The required post-repair Docker probe was not run because administrator authorization failed and no repaired service existed to probe.

Result: `BLOCKED` under the task's failure policy; no Docker bridge claim is made.

### Non-loopback denial

The required post-repair denial probe was not run because the repair did not execute. The unchanged `0.0.0.0` configuration and `*:8000` listener already fail the listener-containment gate.

No private LAN or Tailscale address was captured in this artifact.

Result: `BLOCKED`; non-loopback denial is not proven.

### Restart stability

No post-repair restart was performed because the initial authorized mutation did not occur. Restarting the unchanged wildcard service would not prove containment.

Result: `BLOCKED`; restart-stable containment is not proven.

### Deviations and ambiguity

- The renderer used its supported explicit registry-path option to preserve the installed registry target. No registry file or entry was edited.
- A temporary apostrophe-free symlink to the repository was used only so AppleScript could invoke the same checked-in installer safely. It did not substitute a different installer or service definition.
- The installed service reports `running` and `lsof` reports `*:8000`, while loopback HTTP connection attempts fail. That inconsistent liveness surface is recorded without interpretation and does not weaken the containment failure.
- The installed wildcard artifact's source revision remains unverified. The replacement artifacts were rendered from exact HEAD `552544a9df6b51de9445f279aa50987452f2e395` but were not installed.

### Sanitized commands used

```bash
git status --short --branch
git rev-parse HEAD
git log -1 --oneline
git merge-base --is-ancestor 552544a9df6b51de9445f279aa50987452f2e395 HEAD
plutil -extract Label raw -o - /Library/LaunchDaemons/com.resonant.whooshd.plist
plutil -extract ProgramArguments json -o - /Library/LaunchDaemons/com.resonant.whooshd.plist
plutil -extract EnvironmentVariables.WHOOSHD_HOST raw -o - /Library/LaunchDaemons/com.resonant.whooshd.plist
plutil -extract EnvironmentVariables.WHOOSHD_PORT raw -o - /Library/LaunchDaemons/com.resonant.whooshd.plist
launchctl print system/com.resonant.whooshd
lsof -nP -iTCP:8000 -sTCP:LISTEN
ps -o pid=,ppid=,user=,lstart=,command= -p 978
curl -fsS --connect-timeout 2 --max-time 5 http://127.0.0.1:8000/health

python3 ops/launchd/render_launchd_plists.py \
  --output-dir .local/launchd \
  --whooshd-root "/Volumes/Dev_SSD/ResonantConstructs/Whoosh'd" \
  --user chriscastillo \
  --model-registry-path configs/models.friends-family-guest.yaml \
  --dry-run

plutil -lint .local/launchd/com.resonant.whooshd.plist
plutil -lint .local/launchd/com.resonant.mlx-vlm-gemma12b.plist
bash -n ops/launchd/install_local_launchd.sh
bash -n scripts/smoke/whooshd_12b_smoke.sh
bash ops/launchd/install_local_launchd.sh dry-run

# Invoked through macOS native administrator authorization:
cd '<temporary-repository-symlink>' && /bin/bash ops/launchd/install_local_launchd.sh install
```

### Not proven by the second attempt

- Loopback-only containment
- Local health after repair
- Docker bridge reachability through `host.docker.internal`
- Direct non-loopback denial
- Restart-stable containment
- Model generation or model-specific readiness
- Authenticated LAN, Tailscale, sidecar trust, remote-node, service-authentication, or mTLS modes

### Second-attempt resume condition

Completion still requires valid macOS administrator authorization for the checked-in installer, followed by every mandatory listener, loopback health, Docker bridge, non-loopback denial, and restart-stability probe. Until all of those succeed in one execution window, the final outcome remains `BLOCKED`.

---

## Third attempt — CWC-H01B — 2026-07-19

### Final outcome

Final outcome: `FAIL`

Outcome: `FAIL`

CWC-H01A established that direct `sudo` worked twice in a foreground human-controlled Terminal. The documented installer then completed, and the installed Whoosh'd plist became loopback-only. However, the repaired Whoosh'd service could not start because the current launchd template omitted the previously installed `WHOOSHD_PYTHON` setting. The launcher selected a broken Python 3.14 virtual environment and entered a launchd crash loop. Local health therefore failed, so Docker bridge and restart-stability proof could not proceed.

The original wildcard listener is no longer running. Port `8000` has no listener, which removes the immediate remote exposure but also leaves Whoosh'd unavailable.

### Execution window and repository identity

- Date: 2026-07-19
- Window: 07:08:33-08:28:41 EDT
- Branch: `codex/authoritative-runtime-registry`
- Exact HEAD tested: `2020e65ce9a2a2ff665f9bcc3092a889ca878ab4`
- First blocked proof commit: `552544a9df6b51de9445f279aa50987452f2e395`
- Second blocked proof commit: `2020e65ce9a2a2ff665f9bcc3092a889ca878ab4`
- Both prior proof commits were confirmed in the tested ancestry.
- Initial worktree status: branch was two commits ahead of its same-named origin branch with only the unrelated untracked `.venv311/` directory.
- The replacement plist was rendered from the tested source HEAD.

### Operator prerequisite

CWC-H01A: `PASS`.

- Required repository mechanism: direct `sudo`
- Foreground interactive Terminal: available
- Direct `sudo`: passed
- Repeat test: passed
- macOS error `-60007`: did not recur
- Service or system mutation during authorization preflight: none

### Service identity before repair

- LaunchDaemon: `system/com.resonant.whooshd`
- Installed launcher: `/Users/chriscastillo/.local/bin/whooshd`
- Installed arguments: `/Users/chriscastillo/.local/bin/whooshd --codexify`
- Installed host: `WHOOSHD_HOST=0.0.0.0`
- Working directory: `/Volumes/Dev_SSD/ResonantConstructs/Whoosh'd`
- State: `running`
- PID: `978`
- Listener: `TCP *:8000 (LISTEN)`
- Loopback health: failed with curl exit `7`

The launchd job, executable command, PID 1 parent, working directory, and port owner confirmed the intended Whoosh'd service before mutation.

### Documented repair procedure

1. Re-inspected repository HEAD, proof ancestry, installed plist, launchd job, PID, listener, and health.
2. Rendered the repository templates into ignored `.local/launchd/` output with explicit `--host 127.0.0.1 --port 8000` arguments.
3. Supplied the renderer's supported `--model-registry-path configs/models.friends-family-guest.yaml` option to preserve the installed registry target.
4. Validated both plists with `plutil -lint`, syntax-checked the installer, and ran its dry-run successfully.
5. The human operator ran `sudo -v && bash ops/launchd/install_local_launchd.sh install` in a foreground Terminal.
6. The first install attempt copied the plists, unloaded both old jobs, and failed at the first bootstrap with `Bootstrap failed: 5: Input/output error`.
7. Inspection showed valid `root:wheel` `0644` installed plists, both old jobs unloaded, and no listeners on ports `8000` or `8082`. Bounded launchd logs showed both services had just been removed.
8. The same documented installer was repeated once after the removal completed. It returned successfully and loaded both jobs.
9. The MLX-VLM sidecar started on loopback. Whoosh'd entered a launchd crash loop and never established a port `8000` listener.

No plist was hand-edited. No source, model registry, adapter, queue, ThreadWake, Codexify, or user-data file was modified.

### Installed service after repair

- Installed plist: `/Library/LaunchDaemons/com.resonant.whooshd.plist`
- Ownership and mode: `root:wheel`, `0644`
- Plist validation: `OK`
- Arguments: `/Users/chriscastillo/.local/bin/whooshd --host 127.0.0.1 --port 8000`
- Installed host: `WHOOSHD_HOST=127.0.0.1`
- `WHOOSHD_PYTHON`: absent from installed plist
- Launchd state: `spawn scheduled`
- Runs observed during capture: at least `10`
- Last exit code: `1`
- Stable PID: none
- Listener on port `8000`: none

Listener containment: the wildcard listener is gone and no non-loopback listener exists. Availability containment proof cannot pass because the intended loopback listener is also absent.

### Startup failure and runbook contradiction

The installed launcher resolves Python as follows:

1. use `WHOOSHD_PYTHON` when explicitly set;
2. otherwise select `$WHOOSHD_ROOT/.venv/bin/python`;
3. fall back to `.venv311/bin/python` only when `.venv/bin/python` is not executable.

The repaired template does not render `WHOOSHD_PYTHON`. Both environments are executable, so the launcher selected `.venv/bin/python` from Python 3.14. That environment fails to import its Pydantic native extension:

`ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'`

Focused inspection proved:

- launcher `--doctor`: selected `.venv/bin/python`
- `.venv/bin/python -c 'import pydantic_core._pydantic_core'`: failed
- `.venv311/bin/python -c 'import pydantic_core._pydantic_core'`: passed
- the prior installed plist had explicitly used `.venv311/bin/python`
- the current renderer/template exposes no supported Whoosh'd Python-path option

This is a persistent installer/runbook defect for this machine's documented runtime: the checked-in procedure restores network containment but loses the interpreter selection required for service liveness. Fixing it requires a separate atomic installer/runbook task; this proof task does not authorize that change.

### Loopback health

`curl --noproxy '*' -fsS --connect-timeout 2 --max-time 5 http://127.0.0.1:8000/health`

Result: `FAIL`, curl exit `7`, HTTP `000`. No response body was produced.

This is liveness failure only. Model generation and model-specific readiness were not tested.

### Docker bridge

The Docker bridge probe was not run because the required local health gate failed and no host listener existed on port `8000`.

Result: `FAIL` by the task's mandatory-proof policy; Docker bridge reachability is not proven.

### Non-loopback denial

There was no listener on port `8000` after installation. The required address-specific denial probe was not treated as proof because the intended local service was also unavailable.

Result: direct non-loopback reachability is absent at the listener layer, but the mandatory deployment proof remains `FAIL` because loopback liveness failed.

No private LAN or Tailscale address is recorded.

### Restart stability

No additional restart was performed. The freshly installed service was already crash-looping and had no healthy baseline to restart.

Result: `FAIL`; restart-stable containment and availability are not proven.

### Sidecar observation

The documented MLX-VLM sidecar loaded successfully:

- LaunchDaemon: `system/com.resonant.mlx-vlm-gemma12b`
- State: `running`
- PID observed: `54842`
- Listener: `127.0.0.1:8082`

This does not prove Whoosh'd health, model readiness, or generation.

### Sanitized commands used

```bash
git status --short --branch
git rev-parse HEAD
git log -3 --oneline
git merge-base --is-ancestor 552544a9df6b51de9445f279aa50987452f2e395 HEAD
git merge-base --is-ancestor 2020e65ce9a2a2ff665f9bcc3092a889ca878ab4 HEAD
plutil -extract ProgramArguments json -o - /Library/LaunchDaemons/com.resonant.whooshd.plist
plutil -extract EnvironmentVariables.WHOOSHD_HOST raw -o - /Library/LaunchDaemons/com.resonant.whooshd.plist
launchctl print system/com.resonant.whooshd
lsof -nP -iTCP:8000 -sTCP:LISTEN
ps -o pid=,ppid=,user=,lstart=,command= -p 978
curl --noproxy '*' -fsS --connect-timeout 2 --max-time 5 http://127.0.0.1:8000/health

python3 ops/launchd/render_launchd_plists.py \
  --output-dir .local/launchd \
  --whooshd-root "/Volumes/Dev_SSD/ResonantConstructs/Whoosh'd" \
  --user chriscastillo \
  --model-registry-path configs/models.friends-family-guest.yaml \
  --dry-run

plutil -lint .local/launchd/com.resonant.whooshd.plist
plutil -lint .local/launchd/com.resonant.mlx-vlm-gemma12b.plist
bash -n ops/launchd/install_local_launchd.sh
bash ops/launchd/install_local_launchd.sh dry-run
sudo -v && bash ops/launchd/install_local_launchd.sh install

tail -n 100 /tmp/whooshd.err
/Users/chriscastillo/.local/bin/whooshd --doctor
.venv/bin/python -c 'import pydantic_core._pydantic_core'
.venv311/bin/python -c 'import pydantic_core._pydantic_core'
rg -n "WHOOSHD_PYTHON|PYTHON_BIN" /Users/chriscastillo/.local/bin/whooshd ops/launchd docs/ops/whooshd-launchd-local-runtime.md
```

### Not proven by CWC-H01B

- Healthy loopback Whoosh'd deployment
- Docker bridge reachability through `host.docker.internal`
- Restart-stable availability and containment
- Model generation or model-specific readiness
- Authenticated LAN, Tailscale, sidecar trust, remote-node, service-authentication, or mTLS modes

### Required next atomic task

Repair the launchd renderer/template and runbook so the Whoosh'd interpreter is explicit and validated before installation. The smallest compatible change is to add a renderer option and plist environment entry for `WHOOSHD_PYTHON`, render the known-good `.venv311/bin/python` path for this deployment, validate that path during render/install, and cover the selection with focused tests. After that task lands, rerun CWC-H01B from a fresh exact HEAD and complete all mandatory live probes.

---

## Fourth attempt — CWC-H01B-R2 — 2026-07-19

### Final outcome

Final outcome: `FAIL`

Outcome: `FAIL`

CWC-H01B.1 successfully made the launchd Python selection explicit and its
preflight passed against `.venv311`. The newly rendered and installed Whoosh'd
plist therefore contained the correct interpreter and loopback bind. The
checked-in installer nevertheless failed at its first `launchctl bootstrap`
command on two consecutive foreground-Terminal executions, each time returning
`Bootstrap failed: 5: Input/output error` and stopping before it could bootstrap
the MLX-VLM sidecar. After the repeat, neither LaunchDaemon was registered and
neither required listener existed.

This is not containment PASS. The wildcard listener remains removed, but the
required loopback-only service pair is unavailable.

### Execution window and repository identity

- Date: 2026-07-19
- Window: 09:42:43-09:49:44 EDT
- Branch: `codex/authoritative-runtime-registry`
- Exact HEAD tested: `a7b056c0b3af1ec5f6f83fb5e9d2a17c1c51b0f9`
- CWC-H01B.1 repair commit: `a7b056c0b3af1ec5f6f83fb5e9d2a17c1c51b0f9`
- Prior BLOCKED and FAIL receipts remained in the tested ancestry.
- Initial and final source worktree status contained only the unrelated,
  untracked `.venv311/` directory.

### Initial live state

- `system/com.resonant.whooshd`: `spawn scheduled`, active count `0`, last exit
  code `1`, 437 runs, no stable PID, and no port `8000` listener.
- Installed Whoosh'd arguments were already loopback-only, but its plist did not
  yet contain `WHOOSHD_PYTHON`.
- `system/com.resonant.mlx-vlm-gemma12b`: running as PID `54842`.
- MLX-VLM listener: `127.0.0.1:8082` only.
- MLX-VLM inventory returned two upstream identifiers.
- Whoosh'd loopback health failed to connect because no port `8000` listener
  existed.

### Render and pre-install validation

The current templates were rendered into ignored `.local/launchd/` output with:

- `WHOOSHD_PYTHON=/Volumes/Dev_SSD/ResonantConstructs/Whoosh'd/.venv311/bin/python`
- `WHOOSHD_HOST=127.0.0.1`
- `WHOOSHD_PORT=8000`
- explicit arguments `--host 127.0.0.1 --port 8000`
- no `--codexify`, `0.0.0.0`, or wildcard host value
- `configs/models.friends-family-guest.yaml`, preserving the installed registry
  and alias behavior from the preceding containment attempts

Both rendered plists passed `plutil -lint`. The standalone interpreter
preflight and the installer's repeated plist/interpreter preflight passed.
The installer dry-run completed without mutation.

### Installer executions

The human operator ran the checked-in installer twice from a foreground
Terminal:

```bash
sudo -v && bash ops/launchd/install_local_launchd.sh install
```

Both executions:

1. validated both rendered plists;
2. validated the explicit `.venv311` interpreter;
3. copied the rendered plists to `/Library/LaunchDaemons/`;
4. applied `root:wheel` ownership and `0644` mode;
5. unloaded the existing jobs; and
6. stopped at the first Whoosh'd bootstrap with launchctl error `5`.

After the first error, inspection found the new Whoosh'd job registered and
running as PID `67801` on `127.0.0.1:8000`, while the sidecar remained absent
because the installer had exited before its bootstrap command. Repeating the
same documented installer was therefore the smallest bounded way to retry the
incomplete bundle installation.

The repeat returned the same bootstrap error. At 09:48:20 EDT, and again in a
delayed stability check at 09:49:44 EDT, launchd reported that neither service
existed in the system domain. No manual bootstrap, hand-edited plist, competing
foreground process, or source repair was attempted.

### Installed artifacts after failure

- `/Library/LaunchDaemons/com.resonant.whooshd.plist`: `root:wheel`, `0644`,
  `plutil` valid.
- `/Library/LaunchDaemons/com.resonant.mlx-vlm-gemma12b.plist`: `root:wheel`,
  `0644`, `plutil` valid.
- Installed `WHOOSHD_PYTHON`:
  `/Volumes/Dev_SSD/ResonantConstructs/Whoosh'd/.venv311/bin/python`.
- Installed Whoosh'd bind: `127.0.0.1:8000` with no `--codexify` argument.
- Registered launchd jobs after the repeated failure: none.
- Process identities after the repeated failure: none for either service.
- Listeners after the repeated failure: none on ports `8000` or `8082`.

### Required proof results

- Listener containment: `FAIL`. No wildcard listener exists, but the required
  loopback listeners are also absent.
- Loopback health: `FAIL`; connection to `127.0.0.1:8000` was refused.
- Docker bridge: `FAIL` by mandatory-proof policy; no host port `8000` listener
  existed to probe through `host.docker.internal`.
- Non-loopback denial: not credited as containment proof because the intended
  local service was also unavailable.
- Upstream inventory after install: `FAIL`; connection to
  `127.0.0.1:8082/v1/models` was refused.
- Whoosh'd model inventory: `FAIL`; connection to
  `127.0.0.1:8000/v1/models` was refused.
- Real Gemma alias generation: not reached because neither service was running.
- `scripts/smoke/whooshd_12b_smoke.sh`: not run because its required local
  endpoints were absent.
- Restart stability: `FAIL`; the documented installation/restart procedure did
  not establish a running baseline and the delayed recheck remained unloaded.

### Newly exposed installer defect

The source defect is in the checked-in installer's launchd transition path, not
the repaired Python selection. The installer treats the first bootstrap error as
fatal and exits before attempting the sidecar bootstrap. Across the two observed
executions, the same command sequence produced inconsistent partial state: the
first left Whoosh'd registered despite reporting error `5`; the second left both
jobs absent. The exact internal launchd reason for error `5` is not proven, but
the repository procedure is demonstrably not idempotent or completion-safe for
this system-domain replacement sequence.

Per the task boundary, this proof records the defect and stops. It does not
repair installer sequencing or bypass the installer with manual service starts.

### Remaining unproven modes

- Healthy loopback Whoosh'd deployment
- Docker bridge reachability through `host.docker.internal`
- Direct non-loopback denial while the loopback service is healthy
- Gemma alias inventory and real generation through Whoosh'd
- Restart-stable containment and availability
- Authenticated LAN, Tailscale, sidecar-trust, remote-node,
  service-authentication, and mTLS deployment modes

### Required next atomic task

Repair and test the launchd installer transition so replacing already managed
system jobs is idempotent and completion-safe. The task should resolve the first
bootstrap error deterministically, wait for or verify bootout completion before
bootstrap, ensure both jobs either reach their intended registered state or fail
with an explicit recoverable receipt, and add controlled tests for repeated
installation. After that repair, rerun CWC-H01B from a fresh exact HEAD and
complete all containment, Docker, generation, and restart-stability probes.

---

## Fifth attempt — CWC-H01B-R3 — 2026-07-19

### Final outcome

Final outcome: `PASS`

Outcome: `PASS`

The convergent installer completed from an initially absent two-service state,
and then completed again from an initially registered-running state for the
restart-stability proof. Whoosh'd and MLX-VLM remained loopback-only, healthy,
Docker-reachable through `host.docker.internal`, unreachable through an active
non-loopback host address, and capable of real Gemma alias generation before
and after restart.

### Execution window and repository identity

- Date: 2026-07-19
- Window: 10:59:37-13:58:07 EDT
- Branch: `codex/authoritative-runtime-registry`
- Exact local HEAD tested: `6b7b65513e1c4e8ee95e3303f8dbf18614fdea4c`
- CWC-H01B.2 implementation commit:
  `6b7b65513e1c4e8ee95e3303f8dbf18614fdea4c`
- Initial and final source worktree status: only the unrelated untracked
  `.venv311/` directory.
- The tested source commit was local during the execution window. Remote
  publication was intentionally deferred until after proof capture.

### Pre-install state

- `system/com.resonant.whooshd`: absent; exact-target query exit `113`.
- `system/com.resonant.mlx-vlm-gemma12b`: absent; exact-target query exit `113`.
- Listener on port `8000`: none.
- Listener on port `8082`: none.
- Installer lock: absent.
- Both installed plist files were `root:wheel`, mode `0644`, and passed
  `plutil -lint`.
- The installed Whoosh'd plist already contained the explicit `.venv311`
  interpreter and loopback arguments, but neither definition was registered.

### Render and pre-install validation

The current templates were freshly rendered into ignored `.local/launchd/`
output from the exact tested HEAD with:

- `WHOOSHD_PYTHON=/Volumes/Dev_SSD/ResonantConstructs/Whoosh'd/.venv311/bin/python`
- `WHOOSHD_HOST=127.0.0.1`
- `WHOOSHD_PORT=8000`
- Whoosh'd arguments `--host 127.0.0.1 --port 8000`
- MLX-VLM arguments `--host 127.0.0.1 --port 8082`
- `configs/models.friends-family-guest.yaml`, preserving the existing deployed
  registry and alias behavior
- no `--codexify`, `0.0.0.0`, or IPv6 wildcard argument

Both plists passed `plutil -lint`. The explicit interpreter preflight passed.
The convergent installer dry-run passed with its launchctl and sudo command
surfaces disabled, proving that no mutation or launchd query occurred during
that validation.

### Initial convergent installation

The human operator ran once from a foreground Terminal:

```bash
sudo -v && bash ops/launchd/install_local_launchd.sh install
```

The installer reported:

- initial Whoosh'd state: `absent`
- initial MLX-VLM state: `absent`
- no removal required for either label
- Whoosh'd bootstrap: `registered-not-running`
- MLX-VLM bootstrap: `registered-not-running`
- Whoosh'd kickstart: `registered-running`
- MLX-VLM kickstart: `registered-running`
- final bundle result: `Install converged`

The installer completed its plist, interpreter, target-path, ownership/mode,
registration, kickstart, and final registration gates. Its machine-local lock
was absent after completion.

### Initial service identities and listeners

- Whoosh'd LaunchDaemon: `system/com.resonant.whooshd`
- Whoosh'd PID: `163`, parent PID `1`, owner `chriscastillo`
- Whoosh'd effective command: Python 3.11 running
  `uvicorn whooshd.app:app --host 127.0.0.1 --port 8000`
- Whoosh'd listener: `127.0.0.1:8000` only
- MLX-VLM LaunchDaemon: `system/com.resonant.mlx-vlm-gemma12b`
- MLX-VLM PID: `196`, parent PID `1`, owner `chriscastillo`
- MLX-VLM effective command: Python 3.11 running `mlx_vlm server` with
  `--host 127.0.0.1 --port 8082`
- MLX-VLM listener: `127.0.0.1:8082` only

`lsof` reported only the two explicit IPv4 loopback listeners. It reported no
wildcard, LAN-address, or Tailscale-address listener on either service port.

### Initial containment and availability probes

- Whoosh'd `GET /health`: HTTP `200`, `ok=true`, `status=ready`,
  `model_lifecycle=ready`.
- MLX-VLM `GET /v1/models`: HTTP `200`, two upstream identifiers returned.
- Whoosh'd `GET /v1/models`: HTTP `200`; inventory contained
  `gemma-4-12b-it-qat-4bit`.
- Real `POST /v1/chat/completions` requested the
  `gemma-4-12b-it-qat-4bit` alias and returned HTTP `200`, finish reason `stop`,
  and exact assistant text `operational`.
- `bash scripts/smoke/whooshd_12b_smoke.sh`: `PASS`; upstream inventory,
  Whoosh'd alias inventory, and an additional real exact-text generation all
  succeeded.
- Docker bridge from the running `codexify_tester-backend-1` container:
  Whoosh'd health HTTP `200`, `ok=true`; inventory HTTP `200` and expected alias
  present through `http://host.docker.internal:8000`.
- Direct probe through the active default-route non-loopback address: denied
  with curl exit `7` and HTTP `000`. The address itself was not recorded.

### Convergent restart

The human operator invoked the same supported installer once for restart
stability. It reported both initial service states as `registered-running`, then:

- Whoosh'd removal confirmed absent on polling attempt `2`.
- MLX-VLM removal confirmed absent on polling attempt `4`.
- Both bootstraps confirmed `registered-not-running`.
- Both kickstarts retained `registered-running`.
- Final bundle result: `Install converged`.

No blind retry, raw manual bootstrap, competing foreground process, hand-edited
plist, or source repair occurred during the proof window.

### Post-restart service identities and listeners

- Whoosh'd PID: `9574`, parent PID `1`, owner `chriscastillo`.
- Whoosh'd process start: 11:21:58 EDT.
- Whoosh'd listener: `127.0.0.1:8000` only.
- MLX-VLM PID: `9616`, parent PID `1`, owner `chriscastillo`.
- MLX-VLM process start: 11:22:03 EDT.
- MLX-VLM listener: `127.0.0.1:8082` only.
- Both exact-target launchctl queries returned exit `0` in the final snapshot.
- Installer lock: absent.

The changed PIDs and later start times prove that the post-restart probes ran
against new service processes rather than reusing the initial baseline.

### Post-restart containment and availability probes

- Whoosh'd `GET /health`: HTTP `200`, `ok=true`, `status=ready`,
  `model_lifecycle=ready`.
- MLX-VLM inventory: HTTP `200`, two upstream identifiers returned.
- Whoosh'd inventory: HTTP `200`; expected Gemma alias present.
- Fresh real alias generation: HTTP `200`, finish reason `stop`, exact assistant
  text `operational`.
- Docker bridge from the running `codexify-dashboard-proof-backend` container:
  health HTTP `200`, `ok=true`; inventory HTTP `200`, expected alias present.
- Direct active non-loopback probe: denied with curl exit `7`, HTTP `000`.
- Final stability snapshot at 13:58:07 EDT: both exact jobs registered, both
  loopback listeners present, Whoosh'd health successful, and installer lock
  absent.

The first selected post-restart Docker container stopped before it could return
application evidence. That command produced no bridge claim. The probe was
repeated only against a different container that was currently running and
healthy; its explicit HTTP and alias results are the Docker evidence above.

### Mandatory proof matrix

- Installation converged: `PASS`
- Both LaunchDaemons registered: `PASS`
- Whoosh'd loopback-only listener: `PASS`
- MLX-VLM loopback-only listener: `PASS`
- No wildcard/LAN/Tailscale listener: `PASS`
- Loopback health: `PASS`
- MLX-VLM inventory: `PASS`
- Whoosh'd expected alias inventory: `PASS`
- Real alias generation: `PASS`
- Runbook smoke: `PASS`
- Docker bridge reachability: `PASS`
- Direct non-loopback denial: `PASS`
- Restart-stable registration: `PASS`
- Restart-stable listeners and health: `PASS`
- Restart-stable Docker bridge and denial: `PASS`
- Restart-stable inventory and real generation: `PASS`

### Scope and remaining unproven modes

This artifact proves the runbook-managed single-node localhost deployment and
Docker host bridge only. It does not prove or claim authenticated LAN service,
Tailscale-exposed service, remote-node operation, sidecar authentication,
service-to-service authentication, mTLS, reboot survival, or whole-Codexify chat
completion. Those remain separate modes or proof tasks.

No source, plist template, model registry, alias, adapter, queue, ThreadWake,
Codexify configuration, virtual environment, or user-data file was modified by
the live proof. Only ignored generated plist output and this proof receipt were
written.
