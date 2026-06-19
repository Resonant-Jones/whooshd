#!/usr/bin/env python3
"""Render local launchd plists for Whoosh'd and the Gemma 12B mlx-vlm sidecar."""

from __future__ import annotations

import argparse
import html
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = Path(__file__).resolve().parent


def _xml_escape(value: str) -> str:
    return html.escape(value, quote=False)


def _render_template(template_name: str, values: dict[str, str]) -> str:
    template_path = TEMPLATE_DIR / template_name
    text = template_path.read_text()
    for key, value in values.items():
        text = text.replace(f"{{{{{key}}}}}", _xml_escape(value))
    return text


def _require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise SystemExit(f"{label} not found: {path}")


def _require_dir(path: Path, label: str) -> None:
    if not path.is_dir():
        raise SystemExit(f"{label} not found: {path}")


def _resolve_registry_path(whooshd_root: Path, raw_value: str) -> Path:
    candidate = Path(raw_value)
    if candidate.is_absolute():
        return candidate

    rooted = (whooshd_root / candidate).resolve()
    if rooted.is_file():
        return rooted

    repo_local = (REPO_ROOT / candidate).resolve()
    if repo_local.is_file():
        return repo_local

    return rooted


def _print_install_commands(output_dir: Path, whooshd_label: str, mlx_vlm_label: str) -> None:
    whooshd_plist = output_dir / f"{whooshd_label}.plist"
    mlx_vlm_plist = output_dir / f"{mlx_vlm_label}.plist"
    whooshd_sys = Path("/Library/LaunchDaemons") / whooshd_plist.name
    mlx_vlm_sys = Path("/Library/LaunchDaemons") / mlx_vlm_plist.name
    backup = Path("/Library/LaunchDaemons") / f"{whooshd_plist.name}.bak"

    print()
    print("Validation:")
    print(f"  plutil -lint {whooshd_plist}")
    print(f"  plutil -lint {mlx_vlm_plist}")
    print()
    print("Suggested install commands:")
    print(f"  sudo cp {whooshd_sys} {backup} 2>/dev/null || true")
    print(f"  sudo cp {whooshd_plist} {whooshd_sys}")
    print(f"  sudo cp {mlx_vlm_plist} {mlx_vlm_sys}")
    print(f"  sudo chown root:wheel {whooshd_sys} {mlx_vlm_sys}")
    print(f"  sudo chmod 644 {whooshd_sys} {mlx_vlm_sys}")
    print(f"  sudo launchctl bootout system/{whooshd_label} 2>/dev/null || true")
    print(f"  sudo launchctl bootout system/{mlx_vlm_label} 2>/dev/null || true")
    print(f"  sudo launchctl bootstrap system {whooshd_sys}")
    print(f"  sudo launchctl bootstrap system {mlx_vlm_sys}")
    print(f"  sudo launchctl kickstart -k system/{whooshd_label}")
    print(f"  sudo launchctl kickstart -k system/{mlx_vlm_label}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, help="Directory for rendered plist files.")
    parser.add_argument("--whooshd-root", default="/Volumes/Dev_SSD/ResonantConstructs/Whoosh'd")
    parser.add_argument("--user", default="chriscastillo")
    parser.add_argument("--whooshd-launcher", default="/Users/chriscastillo/.local/bin/whooshd")
    parser.add_argument("--whooshd-label", default="com.resonant.whooshd")
    parser.add_argument("--mlx-vlm-label", default="com.resonant.mlx-vlm-gemma12b")
    parser.add_argument("--model-registry-path", default="configs/models.yaml")
    parser.add_argument("--whooshd-host", default="127.0.0.1")
    parser.add_argument("--whooshd-port", default="8000")
    parser.add_argument("--mlx-vlm-host", default="127.0.0.1")
    parser.add_argument("--mlx-vlm-port", default="8082")
    parser.add_argument(
        "--mlx-vlm-python",
        default="/Volumes/Dev_SSD/ResonantConstructs/Whoosh'd/.venv311/bin/python",
    )
    parser.add_argument(
        "--mlx-vlm-model-path",
        default="/Volumes/Dev_SSD/whooshd/model-weights/hub/models--mlx-community--gemma-4-12B-it-qat-4bit",
    )
    parser.add_argument("--path-value", default="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/Users/chriscastillo/.local/bin")
    parser.add_argument("--log-dir", default="/tmp")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    output_dir = Path(args.output_dir).resolve()
    whooshd_root = Path(args.whooshd_root).resolve()
    log_dir = Path(args.log_dir)
    whooshd_launcher = Path(args.whooshd_launcher)
    mlx_vlm_python = Path(args.mlx_vlm_python)
    mlx_vlm_model_path = Path(args.mlx_vlm_model_path)

    registry_path = _resolve_registry_path(whooshd_root, args.model_registry_path)

    _require_dir(whooshd_root, "Whoosh'd root")
    _require_file(registry_path, "Model registry")
    _require_file(whooshd_launcher, "Whoosh'd launcher")
    _require_file(mlx_vlm_python, "MLX-VLM Python")
    if not mlx_vlm_model_path.exists():
        raise SystemExit(f"MLX-VLM model path not found: {mlx_vlm_model_path}")

    output_dir.mkdir(parents=True, exist_ok=True)

    common = {
        "USER_NAME": args.user,
        "PATH_VALUE": args.path_value,
    }

    whooshd_values = {
        **common,
        "WHOOSHD_LABEL": args.whooshd_label,
        "WHOOSHD_LAUNCHER": str(whooshd_launcher),
        "WHOOSHD_ROOT": str(whooshd_root),
        "WHOOSHD_MODEL_REGISTRY_PATH": str(registry_path),
        "WHOOSHD_MLX_ENABLED": "true",
        "WHOOSHD_MLX_VLM_ENABLED": "true",
        "WHOOSHD_MLX_VLM_HOST": args.mlx_vlm_host,
        "WHOOSHD_MLX_VLM_PORT": args.mlx_vlm_port,
        "WHOOSHD_MLX_VLM_MODEL": str(mlx_vlm_model_path),
        "WHOOSHD_HOST": args.whooshd_host,
        "WHOOSHD_PORT": args.whooshd_port,
        "WHOOSHD_STDOUT": str(log_dir / "whooshd.out"),
        "WHOOSHD_STDERR": str(log_dir / "whooshd.err"),
    }
    mlx_vlm_values = {
        **common,
        "MLX_VLM_LABEL": args.mlx_vlm_label,
        "MLX_VLM_PYTHON": str(mlx_vlm_python),
        "MLX_VLM_MODEL_PATH": str(mlx_vlm_model_path),
        "MLX_VLM_HOST": args.mlx_vlm_host,
        "MLX_VLM_PORT": args.mlx_vlm_port,
        "MLX_VLM_WORKING_DIR": str(whooshd_root),
        "MLX_VLM_STDOUT": str(log_dir / "mlx-vlm-gemma12b.out"),
        "MLX_VLM_STDERR": str(log_dir / "mlx-vlm-gemma12b.err"),
    }

    whooshd_plist = output_dir / f"{args.whooshd_label}.plist"
    mlx_vlm_plist = output_dir / f"{args.mlx_vlm_label}.plist"

    whooshd_plist.write_text(_render_template("com.resonant.whooshd.plist.template", whooshd_values))
    mlx_vlm_plist.write_text(_render_template("com.resonant.mlx-vlm-gemma12b.plist.template", mlx_vlm_values))

    print(f"Rendered {whooshd_plist}")
    print(f"Rendered {mlx_vlm_plist}")
    if args.dry_run:
        print("Dry run only: no system files were modified.")
    _print_install_commands(output_dir, args.whooshd_label, args.mlx_vlm_label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
