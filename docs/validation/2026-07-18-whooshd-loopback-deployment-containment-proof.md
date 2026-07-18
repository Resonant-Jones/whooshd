# Whoosh'd Loopback Deployment Containment Proof

## Outcome

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
