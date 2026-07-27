#!/usr/bin/env python3
"""Validate the public nddev-kilo-cli-app release surface."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANAGER_PATH = ROOT / "cli-tools" / "nddev_kilo_cli.py"
MANAGER_SPEC = importlib.util.spec_from_file_location("nddev_kilo_cli", MANAGER_PATH)
if MANAGER_SPEC is None or MANAGER_SPEC.loader is None:
    raise RuntimeError(f"cannot load {MANAGER_PATH}")
nddev_kilo_cli = importlib.util.module_from_spec(MANAGER_SPEC)
sys.modules[MANAGER_SPEC.name] = nddev_kilo_cli
MANAGER_SPEC.loader.exec_module(nddev_kilo_cli)
VERSION = "0.1.0"
KILO_VERSION = "7.4.16"
SHARED_CI_COMMIT = "2ccb80e96f5771b6a6b4eae63a4f47e232906dc7"
SETUPS = ("safe", "balanced", "full-auto")
MANAGED_FILES = (
    "xdg-config/kilo/kilo.jsonc",
    "instructions/nddev-builder.md",
    "skills/nddev-builder/SKILL.md",
)
CONTRACT_KEYS = {
    "contract_version",
    "product_name",
    "github_repository",
    "license",
    "runtime_compatibility",
    "runtime_launch",
    "setup_system",
    "managed_state",
    "software_lifecycle",
    "builder",
    "safety",
}


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
    if version.get("kilo_cli_package") != nddev_kilo_cli.KILO_PACKAGE:
        fail("unexpected Kilo CLI package")
    if version.get("kilo_cli_package_integrity") != nddev_kilo_cli.KILO_PACKAGE_INTEGRITY:
        fail("unexpected Kilo CLI package integrity")
    if version.get("kilo_cli_package_shasum") != nddev_kilo_cli.KILO_PACKAGE_SHASUM:
        fail("unexpected Kilo CLI package shasum")
    if manifest.get("setups") != list(SETUPS):
        fail("manifest setup list is not synchronized")
    software = manifest.get("software_lifecycle", {})
    if software.get("installer", {}).get("argv") != list(nddev_kilo_cli.BUN_INSTALL_ARGV):
        fail("manifest Bun installer argv mismatch")
    if software.get("layout", {}).get("manifest") != str(nddev_kilo_cli.SOFTWARE_MANIFEST_RELATIVE):
        fail("manifest software layout mismatch")
    if software.get("bounds", {}).get("max_paths") != nddev_kilo_cli.SOFTWARE_TREE_MAX_PATHS:
        fail("manifest software path bound mismatch")
    if (
        software.get("bounds", {}).get("stage_version_probe_timeout_seconds")
        != nddev_kilo_cli.STAGE_VERSION_PROBE_TIMEOUT_SECONDS
    ):
        fail("manifest stage version probe timeout mismatch")


def validate_contract() -> None:
    contract = load_json(require_file("config/nddev-contract.json"))
    if set(contract) != CONTRACT_KEYS:
        fail("public contract top-level keys are not exact")
    if contract.get("product_name") != "nddev-kilo-cli-app":
        fail("unexpected product name")
    if contract.get("runtime_launch", {}).get("executable") != "kilo":
        fail("runtime executable must be kilo")
    runtime_launch = contract.get("runtime_launch", {})
    if runtime_launch.get("config_path") != "xdg-config/kilo/kilo.jsonc":
        fail("runtime config path must be target-owned kilo.jsonc")
    if runtime_launch.get("path_inherited") is not False:
        fail("runtime launch must not inherit PATH")
    if runtime_launch.get("subcommand") != ["run"]:
        fail("runtime launch subcommand must not force --auto globally")
    if runtime_launch.get("auto_for_setups") != ["full-auto"]:
        fail("runtime launch --auto must be limited to full-auto")
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
    if managed.get("config_file") != "xdg-config/kilo/kilo.jsonc":
        fail("managed config file must be target-owned kilo.jsonc")
    if managed.get("managed_files") != list(MANAGED_FILES):
        fail("managed file list is not synchronized")
    software = contract.get("software_lifecycle", {})
    if software.get("install_tool") != "bun":
        fail("software lifecycle must use Bun")
    if software.get("install_argv") != list(nddev_kilo_cli.BUN_INSTALL_ARGV):
        fail("software lifecycle Bun argv mismatch")
    if software.get("package_integrity") != nddev_kilo_cli.KILO_PACKAGE_INTEGRITY:
        fail("software lifecycle integrity mismatch")
    if (
        software.get("stage_version_probe_timeout_seconds")
        != nddev_kilo_cli.STAGE_VERSION_PROBE_TIMEOUT_SECONDS
    ):
        fail("software lifecycle stage version probe timeout mismatch")
    if software.get("status_executes_binary") is not False:
        fail("software-status must not execute the target binary")
    if software.get("layout", {}).get("bin") != f"bin/{nddev_kilo_cli.KILO_COMMAND}":
        fail("software lifecycle bin path mismatch")


def validate_baseline() -> None:
    baseline = load_json(require_file("references/kilo-cli-baseline.json"))
    if baseline.get("release", {}).get("tag") != "v7.4.16":
        fail("unexpected Kilo release tag")
    npm = baseline.get("npm", {})
    if npm.get("package") != "@kilocode/cli" or npm.get("version") != KILO_VERSION:
        fail("unexpected npm package identity")
    if npm.get("bin") != {"kilo": "bin/kilo", "kilocode": "bin/kilo"}:
        fail("unexpected npm bin map")
    if npm.get("scripts", {}).get("postinstall") != "node ./postinstall.mjs":
        fail("unexpected npm postinstall script")
    if npm.get("dist", {}).get("integrity") != nddev_kilo_cli.KILO_PACKAGE_INTEGRITY:
        fail("unexpected npm integrity")
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
    nddev_kilo_cli.parse_args(["list", "--json"])
    nddev_kilo_cli.parse_args(["status", "--target", "/tmp/nddev-kilo-check", "--json"])
    nddev_kilo_cli.parse_args(["software-status", "--target", "/tmp/nddev-kilo-check", "--json"])


def validate_launch_guard() -> None:
    executable = "/tmp/nddev-kilo-check/bin/kilo"
    for setup_id, expected in (
        ("safe", [executable, "run", "prompt"]),
        ("balanced", [executable, "run", "prompt"]),
        ("full-auto", [executable, "run", "--auto", "prompt"]),
    ):
        command = nddev_kilo_cli.launch_command_for_setup(executable, setup_id, ["prompt"])
        if command != expected:
            fail(f"{setup_id}: launch argv mismatch")
        if setup_id != "full-auto" and "--auto" in command:
            fail(f"{setup_id}: launch must not include --auto")
        if setup_id == "full-auto" and command.count("--auto") != 1:
            fail("full-auto launch must include exactly one managed --auto")
    forbidden_cases = (
        ["--", "--auto"],
        ["--", "--auto=true"],
        ["--", "--no-auto"],
        ["--", "--dangerously-skip-permissions"],
        ["--", "--dangerously-skip-permissions=true"],
        ["--", "--config", "/tmp/other.json"],
        ["--", "--config=/tmp/other.json"],
        ["--", "--model", "provider/model"],
        ["--", "--model=provider/model"],
        ["--", "-m", "provider/model"],
        ["--", "-mprovider/model"],
        ["--", "-c"],
        ["--", "-s", "session-id"],
        ["--", "-s=session-id"],
        ["--", "-f/tmp/secret"],
        ["--", "--", "--auto"],
        ["--", "prompt", "--", "--dangerously-skip-permissions"],
        ["--", "--dir=/tmp/other"],
        ["--", "--attach", "http://127.0.0.1:4096"],
        ["--", "--share"],
    )
    for case in forbidden_cases:
        try:
            nddev_kilo_cli.normalize_launch_child_args(list(case))
        except nddev_kilo_cli.ManagerError:
            continue
        fail(f"launch guard accepted managed child argv: {case}")


def snapshot_tree(root: Path) -> list[tuple[str, str, int, bytes | str | None]]:
    if not root.exists() and not root.is_symlink():
        return [(".", "absent", 0, None)]
    records: list[tuple[str, str, int, bytes | str | None]] = []
    for path in [root, *sorted(root.rglob("*"))]:
        info = path.lstat()
        relative = "." if path == root else path.relative_to(root).as_posix()
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISLNK(info.st_mode):
            records.append((relative, "symlink", mode, os.readlink(path)))
        elif stat.S_ISDIR(info.st_mode):
            records.append((relative, "directory", mode, None))
        elif stat.S_ISREG(info.st_mode):
            records.append((relative, "file", mode, path.read_bytes()))
        else:
            records.append((relative, "other", mode, None))
    return records


def validate_hardlink_materialization_bound() -> None:
    with tempfile.TemporaryDirectory(prefix="nddev-kilo-public-bound-") as raw:
        workspace = Path(raw)
        workspace.chmod(nddev_kilo_cli.OWNER_DIR_MODE)
        target = workspace / "target"
        target.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE)
        marker = target / "unmanaged-marker"
        marker.write_bytes(b"before\n")
        marker.chmod(nddev_kilo_cli.OWNER_FILE_MODE)
        before = snapshot_tree(target)

        original_run_bun_install = nddev_kilo_cli.run_bun_install

        def fake_run_bun_install(stage_root: Path, live_stage: Path) -> None:
            del stage_root
            hardlink_root = (
                live_stage
                / nddev_kilo_cli.SOFTWARE_GLOBAL_DIR_RELATIVE
                / "node_modules"
                / "@kilocode"
                / "cli"
                / "bin"
            )
            hardlink_root.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE, parents=True)
            source = hardlink_root / "oversized-hardlink-source"
            source.write_bytes(b"x" * 65)
            source.chmod(nddev_kilo_cli.OWNER_FILE_MODE)
            peer = hardlink_root / "oversized-hardlink-peer"
            os.link(source, peer)
            if source.lstat().st_nlink < 2 or peer.lstat().st_nlink < 2:
                fail("hardlink regression fixture did not create hardlinks")
            nddev_kilo_cli.materialize_hardlinked_regular_files(
                live_stage,
                max_file_bytes=64,
                max_tree_bytes=64,
            )

        nddev_kilo_cli.run_bun_install = fake_run_bun_install
        try:
            try:
                nddev_kilo_cli.install_or_update_cli(target, "install-cli")
            except nddev_kilo_cli.ManagerError as exc:
                if "64-byte limit" not in str(exc):
                    fail(f"hardlink materialization failed for the wrong reason: {exc}")
            else:
                fail("oversized hardlinked staged file was accepted")
        finally:
            nddev_kilo_cli.run_bun_install = original_run_bun_install

        if snapshot_tree(target) != before:
            fail("failed hardlink materialization changed the target tree")
        for relative in nddev_kilo_cli.SOFTWARE_REPLACE_PATHS:
            if nddev_kilo_cli.path_exists_no_follow(target / relative):
                fail(f"failed hardlink materialization left partial software state: {relative}")
        leftovers = [
            path.name
            for path in workspace.iterdir()
            if path != target and path.name.startswith(f".{target.name}.nddev-kilo-cli")
        ]
        if leftovers:
            fail(f"failed hardlink materialization left transaction artifacts: {leftovers}")


def main() -> int:
    try:
        validate_versions()
        validate_contract()
        validate_baseline()
        validate_setups()
        validate_workflows()
        validate_manager_parse_args()
        validate_launch_guard()
        validate_hardlink_materialization_bound()
    except ValidationError as exc:
        print(f"public contract validation failed: {exc}", file=sys.stderr)
        return 1
    print("public contract validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
