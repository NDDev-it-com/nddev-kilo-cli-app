#!/usr/bin/env python3
"""Validate the public nddev-kilo-cli-app release surface."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
KILO_VERSION = "7.4.16"
SHARED_CI_COMMIT = "2ccb80e96f5771b6a6b4eae63a4f47e232906dc7"
SETUPS = ("safe", "balanced", "full-auto")
MANAGED_FILES = (
    "config.json",
    "instructions/nddev-builder.md",
    "skills/nddev-builder/SKILL.md",
)


class ValidationError(Exception):
    """Public contract validation failure."""


def fail(message: str) -> None:
    raise ValidationError(message)


def load_json(path: Path) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                fail(f"{path}: duplicate key {key}")
            result[key] = value
        return result

    with path.open(encoding="utf-8") as handle:
        value = json.load(handle, object_pairs_hook=reject_duplicates)
    if not isinstance(value, dict):
        fail(f"{path}: expected JSON object")
    return value


def require_file(relative: str) -> Path:
    path = ROOT / relative
    if not path.is_file():
        fail(f"missing required file: {relative}")
    return path


def validate_versions() -> None:
    version_text = require_file("VERSION").read_text(encoding="ascii").strip()
    version = load_json(require_file("build/version.json"))
    manifest = load_json(require_file("build/manifest.json"))
    if version_text != VERSION:
        fail("VERSION is not synchronized")
    if (
        version.get("build_version") != version_text
        or manifest.get("build_version") != version_text
    ):
        fail("build version files are not synchronized")
    if version.get("kilo_cli_current") != KILO_VERSION:
        fail("unexpected Kilo CLI current version")
    if manifest.get("setups") != list(SETUPS):
        fail("manifest setup list is not synchronized")


def validate_contract() -> None:
    contract = load_json(require_file("config/nddev-contract.json"))
    if "skeleton" in contract:
        fail("public contract still declares skeleton state")
    if contract.get("product_name") != "nddev-kilo-cli-app":
        fail("unexpected product name")
    if contract.get("runtime_launch", {}).get("executable") != "kilo":
        fail("runtime executable must be kilo")
    runtime_launch = contract.get("runtime_launch", {})
    if runtime_launch.get("max_runtime_seconds") != 3600:
        fail("runtime launch timeout must be pinned to 3600 seconds")
    if runtime_launch.get("forwards_child_exit_code") is not True:
        fail("runtime launch must forward child exit codes")
    if contract.get("setup_system", {}).get("setup_ids") != list(SETUPS):
        fail("setup ids are not synchronized")
    builder = contract.get("builder", {})
    if builder.get("projection") != "native-agent-skill-command-config":
        fail("builder projection is not the Kilo native projection")
    if builder.get("marketplace") is not None:
        fail("marketplace must remain null unless an official Kilo CLI marketplace is proven")
    managed = contract.get("managed_state", {})
    if managed.get("config_file") != "config.json":
        fail("managed config file must be config.json")
    if managed.get("managed_files") != list(MANAGED_FILES):
        fail("managed file list is not synchronized")


def validate_baseline() -> None:
    baseline = load_json(require_file("references/kilo-cli-baseline.json"))
    if baseline.get("release", {}).get("tag") != "v7.4.16":
        fail("unexpected Kilo release tag")
    npm = baseline.get("npm", {})
    if npm.get("package") != "@kilocode/cli" or npm.get("version") != KILO_VERSION:
        fail("unexpected npm package identity")
    if npm.get("dist", {}).get("shasum") != "c39f0f94f1cae2aeed28b4a5b5a952a5efab2b1d":
        fail("unexpected npm shasum")
    assets = json.dumps(baseline.get("release", {}).get("cli_assets", []), sort_keys=True)
    if "dd233dbee98d19f35a62ad3fdc8bd4ed912a8300f0ddd61f432633fe148f136b" not in assets:
        fail("linux x64 CLI artifact hash is missing")
    if "kilo-vscode" in assets:
        fail("CLI baseline must not use VS Code assets as runtime artifacts")


def validate_setups() -> None:
    for setup_id in SETUPS:
        root = ROOT / "setups" / setup_id
        metadata = load_json(require_file(f"setups/{setup_id}/setup.json"))
        config = load_json(require_file(f"setups/{setup_id}/config.json"))
        if metadata.get("id") != setup_id:
            fail(f"{setup_id}: metadata id mismatch")
        if metadata.get("permission_profile") != setup_id:
            fail(f"{setup_id}: permission profile mismatch")
        if metadata.get("managed_files") != list(MANAGED_FILES):
            fail(f"{setup_id}: managed file list mismatch")
        if metadata.get("builder_enabled") is not True:
            fail(f"{setup_id}: builder is not enabled by default")
        if config.get("default_agent") != "nddev-builder":
            fail(f"{setup_id}: builder is not the default agent")
        if config.get("agent", {}).get("nddev-builder", {}).get("mode") != "all":
            fail(f"{setup_id}: builder agent mode must be all")
        if config.get("command", {}).get("nddev-builder", {}).get("agent") != "nddev-builder":
            fail(f"{setup_id}: builder command does not bind to the builder agent")
        if config.get("sandbox", {}).get("enabled") is not True:
            fail(f"{setup_id}: sandbox must be enabled")
        if setup_id == "full-auto":
            if (
                config.get("permission") != "allow"
                or config.get("sandbox", {}).get("network") != "allow"
            ):
                fail("full-auto must use allow permissions and sandbox network allow")
        elif (
            config.get("permission") == "allow"
            or config.get("sandbox", {}).get("network") != "deny"
        ):
            fail(f"{setup_id}: expected gated permissions and sandbox network deny")
        for relative in MANAGED_FILES[1:]:
            require_file(f"setups/{setup_id}/{relative}")
        skill_text = (root / "skills" / "nddev-builder" / "SKILL.md").read_text(encoding="utf-8")
        if "name: nddev-builder" not in skill_text:
            fail(f"{setup_id}: builder skill metadata is missing")


def validate_workflows() -> None:
    workflows = (
        ".github/workflows/actionlint.yml",
        ".github/workflows/codeql.yml",
        ".github/workflows/dependency-review.yml",
        ".github/workflows/release.yml",
        ".github/workflows/scorecard.yml",
        ".github/workflows/secret-scan.yml",
        ".github/workflows/zizmor.yml",
    )
    for relative in workflows:
        text = require_file(relative).read_text(encoding="utf-8")
        if SHARED_CI_COMMIT not in text:
            fail(f"{relative}: shared CI workflow is not pinned to the expected immutable commit")


def validate_manager_parse_args() -> None:
    sys.path.insert(0, str(ROOT / "cli-tools"))
    import nddev_kilo_cli

    nddev_kilo_cli.parse_args(["list", "--json"])
    nddev_kilo_cli.parse_args(["status", "--target", "/tmp/nddev-kilo-check", "--json"])


def main() -> int:
    try:
        validate_versions()
        validate_contract()
        validate_baseline()
        validate_setups()
        validate_workflows()
        validate_manager_parse_args()
    except ValidationError as exc:
        print(f"public contract validation failed: {exc}", file=sys.stderr)
        return 1
    print("public contract validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
