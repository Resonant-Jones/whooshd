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
