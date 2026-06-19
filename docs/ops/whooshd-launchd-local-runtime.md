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
- `com.resonant.mlx-vlm-gemma12b`
  - owns the Gemma 12B `mlx_vlm` server on `127.0.0.1:8082`
  - launches the exact working command shape: `python -m mlx_vlm server --model ...`

This preserves Whoosh'd as the single Model Bay/proxy endpoint while making
the 12B upstream repeatable across reboot/login.

## Render

From the Whoosh'd repo root:

```bash
python3 ops/launchd/render_launchd_plists.py \
  --output-dir .local/launchd \
  --whooshd-root "/Volumes/Dev_SSD/ResonantConstructs/Whoosh'd" \
  --user chriscastillo \
  --dry-run
```

This writes concrete plists into `.local/launchd/` and prints the exact
install commands without calling `sudo`.

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
- backs up the existing `com.resonant.whooshd.plist` before replacement
- copies both plists into `/Library/LaunchDaemons/`
- sets `root:wheel` ownership and `0644` permissions
- `bootout`s old jobs before `bootstrap` + `kickstart`

It never stores credentials and relies on operator-provided `sudo`.

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

## Rollback

To roll back:

```bash
sudo launchctl bootout system/com.resonant.whooshd 2>/dev/null || true
sudo launchctl bootout system/com.resonant.mlx-vlm-gemma12b 2>/dev/null || true
sudo cp /Library/LaunchDaemons/com.resonant.whooshd.plist.bak /Library/LaunchDaemons/com.resonant.whooshd.plist
sudo chown root:wheel /Library/LaunchDaemons/com.resonant.whooshd.plist
sudo chmod 644 /Library/LaunchDaemons/com.resonant.whooshd.plist
sudo launchctl bootstrap system /Library/LaunchDaemons/com.resonant.whooshd.plist
sudo launchctl kickstart -k system/com.resonant.whooshd
```

If the sidecar plist was added only for this path, remove it explicitly:

```bash
sudo rm -f /Library/LaunchDaemons/com.resonant.mlx-vlm-gemma12b.plist
```
