#!/usr/bin/env python3
"""Validate the public nddev-kilo-cli-app release surface."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import tempfile
import shutil
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

VERSION = (ROOT / "VERSION").read_text(encoding="ascii").strip()
KILO_VERSION = nddev_kilo_cli.KILO_CURRENT_VERSION
SHARED_CI_COMMIT = "2ccb80e96f5771b6a6b4eae63a4f47e232906dc7"
SETUPS = nddev_kilo_cli.ACTIVE_SETUP_IDS
PROFILES = nddev_kilo_cli.PROFILE_IDS
LEGACY_SETUPS = nddev_kilo_cli.LEGACY_SETUP_IDS
MANAGED_FILES = nddev_kilo_cli.MANAGED_FILES
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
        fail("Kilo CLI current version is not synchronized with the manager")
    if version.get("kilo_cli_package") != nddev_kilo_cli.KILO_PACKAGE:
        fail("Kilo CLI package is not synchronized with the manager")
    if version.get("kilo_cli_install_channel") != "npm":
        fail("Kilo CLI install channel must be npm")
    if version.get("kilo_cli_package_integrity") != nddev_kilo_cli.KILO_PACKAGE_INTEGRITY:
        fail("Kilo CLI package integrity is not synchronized with the manager")
    if version.get("kilo_cli_package_shasum") != nddev_kilo_cli.KILO_PACKAGE_SHASUM:
        fail("Kilo CLI package shasum is not synchronized with the manager")
    if manifest.get("setups") != list(SETUPS):
        fail("manifest setup list is not synchronized")
    if manifest.get("permission_profiles") != list(PROFILES):
        fail("manifest permission profile list is not synchronized")
    if manifest.get("managed_state") != list(MANAGED_FILES):
        fail("manifest managed state list is not synchronized")
    software = manifest.get("software_lifecycle", {})
    if software.get("installer", {}).get("argv") != list(nddev_kilo_cli.NPM_INSTALL_ARGV):
        fail("manifest npm installer argv mismatch")
    if software.get("layout", {}).get("package_lock") != str(nddev_kilo_cli.SOFTWARE_LOCK_RELATIVE):
        fail("manifest package-lock path mismatch")
    if software.get("layout", {}).get("manifest") != str(nddev_kilo_cli.SOFTWARE_MANIFEST_RELATIVE):
        fail("manifest software layout mismatch")
    if software.get("bounds", {}).get("max_paths") != nddev_kilo_cli.SOFTWARE_TREE_MAX_PATHS:
        fail("manifest software path bound mismatch")


def validate_contract() -> None:
    contract = load_json(require_file("config/nddev-contract.json"))
    if set(contract) != CONTRACT_KEYS:
        fail("public contract top-level keys are not exact")
    if contract.get("contract_version") != 2:
        fail("unexpected public contract version")
    runtime_launch = contract.get("runtime_launch", {})
    if runtime_launch.get("subcommand") != ["run"]:
        fail("runtime launch subcommand must be kilo run")
    if runtime_launch.get("auto_for_profiles") != ["full-auto"]:
        fail("runtime launch --auto must be limited to full-auto profile")
    setup_system = contract.get("setup_system", {})
    if setup_system.get("setup_ids") != list(SETUPS):
        fail("setup ids are not synchronized")
    if setup_system.get("permission_profiles") != list(PROFILES):
        fail("permission profiles are not synchronized")
    if setup_system.get("legacy_setup_ids") != list(LEGACY_SETUPS):
        fail("legacy setup ids are not synchronized")
    managed = contract.get("managed_state", {})
    if managed.get("managed_files") != list(MANAGED_FILES):
        fail("managed file list is not synchronized")
    if "plugin" not in managed.get("managed_config_keys", []):
        fail("plugin must be a managed config key")
    software = contract.get("software_lifecycle", {})
    if software.get("install_tool") != "npm":
        fail("software lifecycle must use npm")
    if software.get("install_argv") != list(nddev_kilo_cli.NPM_INSTALL_ARGV):
        fail("software lifecycle npm argv mismatch")
    if software.get("official_postinstall") is not True:
        fail("official npm postinstall must be acknowledged")
    if software.get("status_executes_binary") is not False:
        fail("software-status must not execute the target binary")
    builder = contract.get("builder", {})
    if builder.get("projection") != nddev_kilo_cli.BUILDER_PROJECTION:
        fail("builder projection mismatch")
    if builder.get("marketplace") is not None:
        fail("Kilo marketplace emulation must remain absent")
    if builder.get("mcp") is not None:
        fail("MCP must remain absent from the public setup")
    plugin = builder.get("plugin", {})
    if plugin.get("kind") != "local-file" or plugin.get("external_imports") is not False:
        fail("builder plugin contract is invalid")
    if plugin.get("allowed_hooks") != ["experimental.session.compacting"]:
        fail("builder plugin hook contract is invalid")


def validate_baseline() -> None:
    baseline = load_json(require_file("references/kilo-cli-baseline.json"))
    if baseline.get("schema_version") != 2:
        fail("unexpected baseline schema")
    npm = baseline.get("npm", {})
    if npm.get("package") != nddev_kilo_cli.KILO_PACKAGE or npm.get("version") != KILO_VERSION:
        fail("unexpected npm package identity")
    if npm.get("dist_tags", {}).get("latest") != KILO_VERSION:
        fail("npm latest dist-tag is not the managed runtime version")
    if npm.get("dist", {}).get("integrity") != nddev_kilo_cli.KILO_PACKAGE_INTEGRITY:
        fail("unexpected npm integrity")
    if npm.get("scripts", {}).get("postinstall") != "node ./postinstall.mjs":
        fail("unexpected npm postinstall script")
    native_surfaces = baseline.get("native_surfaces", {})
    if native_surfaces.get("marketplace") is not None:
        fail("native marketplace must remain null")
    if native_surfaces.get("plugins") is None:
        fail("native plugin surface must be recorded")
    native_packages = npm.get("native_packages", {})
    supported = native_packages.get("supported", {})
    unsupported = native_packages.get("unsupported", {})
    if not isinstance(supported, dict) or not isinstance(unsupported, dict):
        fail("native package baseline must include supported and unsupported maps")
    for package, record in {**supported, **unsupported}.items():
        if not package.startswith("@kilocode/cli-"):
            fail(f"native package baseline has unexpected package identity: {package}")
        if record.get("version") != KILO_VERSION:
            fail(f"native package version is not synchronized: {package}")
        dist = record.get("dist")
        if not isinstance(dist, dict):
            fail(f"native package dist is missing: {package}")
        if not str(dist.get("tarball", "")).startswith("https://registry.npmjs.org/"):
            fail(f"native package tarball is not official registry: {package}")
        if not str(dist.get("integrity", "")).startswith("sha512-"):
            fail(f"native package integrity is missing: {package}")
        if not isinstance(dist.get("shasum"), str) or not dist["shasum"]:
            fail(f"native package shasum is missing: {package}")
    for package, record in supported.items():
        if record.get("os") not in (["darwin"], ["linux"]):
            fail(f"supported native package is not macOS or Linux: {package}")
    for package, record in unsupported.items():
        if record.get("os") != ["win32"]:
            fail(f"unsupported native package must be explicitly Windows today: {package}")
    observation = baseline.get("latest_release_observation", {})
    if observation.get("cli_source") != "https://registry.npmjs.org/@kilocode/cli":
        fail("baseline must use npm dist-tags as CLI latest source")


def validate_setups_and_profiles() -> None:
    if tuple(nddev_kilo_cli.setup_ids()) != SETUPS:
        fail("manager setup ids are not synchronized")
    if tuple(nddev_kilo_cli.profile_ids()) != PROFILES:
        fail("manager profile ids are not synchronized")
    setup = load_json(require_file("setups/nddev-builder/setup.json"))
    config = load_json(require_file("setups/nddev-builder/config.json"))
    if setup.get("managed_files") != list(MANAGED_FILES):
        fail("setup managed file list mismatch")
    if config.get("default_agent") != "nddev-builder":
        fail("builder is not the default agent")
    if config.get("plugin") != ["./nddev-builder-plugin.js"]:
        fail("builder plugin config spec mismatch")
    if "permission" in config or "sandbox" in config:
        fail("permission posture must live in profiles, not setup config")
    full_auto = load_json(require_file("profiles/full-auto/config.json"))
    if full_auto.get("permission") != "allow":
        fail("full-auto must use permission allow")
    if full_auto.get("sandbox") != {"enabled": False, "network": "allow"}:
        fail("full-auto must have sandbox off and unrestricted network")
    safe = load_json(require_file("profiles/safe/config.json"))
    if safe.get("sandbox", {}).get("enabled") is not True:
        fail("safe sandbox must be enabled")
    if safe.get("sandbox", {}).get("network") != "deny":
        fail("safe sandbox network must be denied")
    permission = safe.get("permission", {})
    if not isinstance(permission, dict):
        fail("safe permission must be an object")
    actions = set(permission.values())
    if actions - {"ask", "deny"}:
        fail("safe permission must use only ask and deny actions")
    if permission.get("agent_manager") != "deny" or permission.get("external_directory") != "deny":
        fail("safe must deny agent_manager and external_directory")
    for relative in MANAGED_FILES[1:]:
        require_file(f"setups/nddev-builder/{relative}")


def validate_builder_toolkit() -> None:
    entry = require_file("setups/nddev-builder/skills/nddev-builder/SKILL.md").read_text(
        encoding="utf-8"
    )
    required_references = [
        "config.md",
        "permissions-sandbox.md",
        "agents-subagents.md",
        "skills.md",
        "commands.md",
        "plugins-hooks.md",
        "mcp-boundary.md",
        "auth-boundary.md",
        "memory-context.md",
        "install-runtime.md",
        "migration-validation.md",
    ]
    for name in required_references:
        if f"references/{name}" not in entry:
            fail(f"entry skill does not route to {name}")
        require_file(f"setups/nddev-builder/skills/nddev-builder/references/{name}")
    for relative in nddev_kilo_cli.ADDITIONAL_SKILLS:
        text = require_file(f"setups/nddev-builder/{relative}").read_text(encoding="utf-8")
        if "description:" not in text:
            fail(f"{relative}: skill metadata is missing")
    for relative in nddev_kilo_cli.AGENT_FILES:
        text = require_file(f"setups/nddev-builder/{relative}").read_text(encoding="utf-8")
        if "mode:" not in text:
            fail(f"{relative}: agent mode metadata is missing")
    for relative in nddev_kilo_cli.COMMAND_FILES:
        text = require_file(f"setups/nddev-builder/{relative}").read_text(encoding="utf-8")
        if "agent: nddev-builder" not in text:
            fail(f"{relative}: command does not bind to nddev-builder")
    plugin = require_file("setups/nddev-builder/xdg-config/kilo/nddev-builder-plugin.js").read_text(
        encoding="utf-8"
    )
    forbidden = (
        "import ",
        "require(",
        "permission.ask",
        "permission:",
        "shell.env",
        "mcp:",
        "auth:",
        "provider:",
        "fetch(",
        "http://",
        "https://",
    )
    for token in forbidden:
        if token in plugin:
            fail(f"builder plugin contains forbidden token: {token}")
    if "experimental.session.compacting" not in plugin or 'id: "nddev-builder"' not in plugin:
        fail("builder plugin does not expose the expected id and compaction hook")


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
    nddev_kilo_cli.parse_args(["plan", "--target", "/tmp/nddev-kilo-check", "--json"])
    nddev_kilo_cli.parse_args(
        ["switch", "--profile", "safe", "--target", "/tmp/nddev-kilo-check", "--json"]
    )
    nddev_kilo_cli.parse_args(["migrate", "--profile", "safe", "--target", "/tmp/nddev-kilo-check"])
    nddev_kilo_cli.parse_args(["status", "--target", "/tmp/nddev-kilo-check", "--json"])
    nddev_kilo_cli.parse_args(["software-status", "--target", "/tmp/nddev-kilo-check", "--json"])


def validate_launch_guard() -> None:
    executable = "/tmp/nddev-kilo-check/bin/kilo"
    expected = {
        "full-auto": [executable, "run", "--auto", "prompt"],
        "safe": [executable, "run", "prompt"],
    }
    for profile_id, command in expected.items():
        observed = nddev_kilo_cli.launch_command_for_profile(executable, profile_id, ["prompt"])
        if observed != command:
            fail(f"{profile_id}: launch argv mismatch")
    for setup_id in LEGACY_SETUPS:
        try:
            nddev_kilo_cli.launch_command_for_setup(executable, setup_id, ["prompt"])
        except nddev_kilo_cli.ManagerError:
            continue
        fail(f"legacy setup launch helper accepted {setup_id}")
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

        original_run_npm_install = nddev_kilo_cli.run_npm_install

        def fake_run_npm_install(stage_root: Path, live_stage: Path) -> None:
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
            nddev_kilo_cli.materialize_hardlinked_regular_files(
                live_stage,
                max_file_bytes=64,
                max_tree_bytes=64,
            )

        nddev_kilo_cli.run_npm_install = fake_run_npm_install
        try:
            try:
                nddev_kilo_cli.install_or_update_cli(target, "install-cli")
            except nddev_kilo_cli.ManagerError as exc:
                if "64-byte limit" not in str(exc):
                    fail(f"hardlink materialization failed for the wrong reason: {exc}")
            else:
                fail("oversized hardlinked staged file was accepted")
        finally:
            nddev_kilo_cli.run_npm_install = original_run_npm_install

        if snapshot_tree(target) != before:
            fail("failed hardlink materialization changed the target tree")


def write_public_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE, parents=True, exist_ok=True)
    path.write_bytes(nddev_kilo_cli.canonical_json(value))
    path.chmod(nddev_kilo_cli.OWNER_FILE_MODE)


def scoped_package_path(root: Path, package: str) -> Path:
    scope, name = package.split("/", 1)
    return root / nddev_kilo_cli.SOFTWARE_GLOBAL_DIR_RELATIVE / "node_modules" / scope / name


def expected_optional_native_versions() -> dict[str, str]:
    supported, unsupported = nddev_kilo_cli.native_package_matrix()
    return {package: nddev_kilo_cli.KILO_CURRENT_VERSION for package in (*supported, *unsupported)}


def lock_record(package: str, record: dict[str, Any]) -> dict[str, Any]:
    dist = nddev_kilo_cli.native_record_dist(record, package)
    return {
        "version": record["version"],
        "resolved": dist["tarball"],
        "integrity": dist["integrity"],
        "optional": True,
        "os": record.get("os"),
        "cpu": record.get("cpu"),
        **({"libc": record["libc"]} if record.get("libc") is not None else {}),
    }


def valid_package_lock(native_packages: tuple[str, ...]) -> dict[str, Any]:
    baseline = nddev_kilo_cli.baseline()
    supported, unsupported = nddev_kilo_cli.native_package_matrix()
    matrix = {**supported, **unsupported}
    root_dist = baseline["npm"]["dist"]
    packages: dict[str, Any] = {
        "": {
            "name": "nddev-kilo-cli-lock",
            "version": "0.0.0",
            "dependencies": {nddev_kilo_cli.KILO_PACKAGE: nddev_kilo_cli.KILO_CURRENT_VERSION},
        },
        "node_modules/@kilocode/cli": {
            "version": nddev_kilo_cli.KILO_CURRENT_VERSION,
            "resolved": root_dist["tarball"],
            "integrity": root_dist["integrity"],
            "bin": {
                "kilo": "bin/kilo",
                "kilocode": "bin/kilo",
            },
            "optionalDependencies": expected_optional_native_versions(),
        },
    }
    for package in native_packages:
        packages[f"node_modules/{package}"] = lock_record(package, matrix[package])
    return {
        "name": "nddev-kilo-cli-lock",
        "version": "0.0.0",
        "lockfileVersion": 3,
        "requires": True,
        "packages": packages,
    }


def expect_manager_error(label: str, fn: Any) -> None:
    try:
        fn()
    except nddev_kilo_cli.ManagerError:
        return
    fail(f"{label} was accepted")


def validate_package_lock_regressions() -> None:
    supported, _unsupported = nddev_kilo_cli.native_package_matrix()
    package = next(iter(supported))
    with tempfile.TemporaryDirectory(prefix="nddev-kilo-public-lock-") as raw:
        root = Path(raw)
        root.chmod(nddev_kilo_cli.OWNER_DIR_MODE)
        write_public_json(
            root / nddev_kilo_cli.SOFTWARE_LOCK_RELATIVE,
            valid_package_lock((package,)),
        )
        nddev_kilo_cli.package_lock_records(root)

        lock = valid_package_lock((package,))
        lock["packages"][f"node_modules/{package}"]["resolved"] = "https://example.invalid/pkg.tgz"
        write_public_json(root / nddev_kilo_cli.SOFTWARE_LOCK_RELATIVE, lock)
        expect_manager_error(
            "package lock with non-registry resolved URL",
            lambda: nddev_kilo_cli.package_lock_records(root),
        )

        lock = valid_package_lock((package,))
        lock["packages"][f"node_modules/{package}"]["integrity"] = "sha512-invalid"
        write_public_json(root / nddev_kilo_cli.SOFTWARE_LOCK_RELATIVE, lock)
        expect_manager_error(
            "package lock with mutated integrity",
            lambda: nddev_kilo_cli.package_lock_records(root),
        )

        lock = valid_package_lock((package,))
        lock["packages"]["node_modules/left-pad"] = {
            "version": "1.0.0",
            "resolved": "https://registry.npmjs.org/left-pad/-/left-pad-1.0.0.tgz",
            "integrity": "sha512-invalid",
        }
        write_public_json(root / nddev_kilo_cli.SOFTWARE_LOCK_RELATIVE, lock)
        expect_manager_error(
            "package lock with unexpected package identity",
            lambda: nddev_kilo_cli.package_lock_records(root),
        )


def write_fake_native_package(root: Path, package: str, record: dict[str, Any], binary: bytes) -> None:
    package_root = scoped_package_path(root, package)
    bin_root = package_root / "bin"
    bin_root.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE, parents=True)
    metadata = {
        "name": package,
        "version": record["version"],
        "os": record.get("os"),
        "cpu": record.get("cpu"),
    }
    if record.get("libc") is not None:
        metadata["libc"] = record["libc"]
    write_public_json(package_root / "package.json", metadata)
    native = bin_root / nddev_kilo_cli.KILO_COMMAND
    native.write_bytes(binary)
    native.chmod(0o700)


def validate_wrong_platform_native_regression() -> None:
    supported, _unsupported = nddev_kilo_cli.native_package_matrix()
    allowed = nddev_kilo_cli.expected_native_records_for_host()
    wrong = next((package for package in supported if package not in allowed), None)
    if wrong is None:
        fail("wrong-platform regression has no unsupported supported-platform package")
    with tempfile.TemporaryDirectory(prefix="nddev-kilo-public-native-") as raw:
        root = Path(raw)
        root.chmod(nddev_kilo_cli.OWNER_DIR_MODE)
        binary = b"wrong-platform-native\n"
        write_public_json(
            root / nddev_kilo_cli.SOFTWARE_LOCK_RELATIVE,
            valid_package_lock((wrong,)),
        )
        native_root = root / nddev_kilo_cli.KILO_NATIVE_BIN_RELATIVE
        native_root.parent.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE, parents=True)
        native_root.write_bytes(binary)
        native_root.chmod(0o700)
        write_fake_native_package(root, wrong, supported[wrong], binary)
        expect_manager_error(
            "wrong-platform installed native package",
            lambda: nddev_kilo_cli.installed_native_packages(root),
        )


def validate_private_target_required() -> None:
    with tempfile.TemporaryDirectory(prefix="nddev-kilo-public-0777-") as raw:
        target = Path(raw) / "target"
        target.mkdir()
        target.chmod(0o777)
        try:
            nddev_kilo_cli.status_payload(target)
        except nddev_kilo_cli.ManagerError:
            return
        fail("0777 target was accepted")


def validate_lock_and_backup_precreation_guards() -> None:
    with tempfile.TemporaryDirectory(prefix="nddev-kilo-public-locks-") as raw:
        workspace = Path(raw)
        workspace.chmod(nddev_kilo_cli.OWNER_DIR_MODE)
        target = workspace / "target"
        target.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE)
        outside = workspace / "external-marker"
        outside.write_text("preserve\n", encoding="utf-8")
        outside.chmod(nddev_kilo_cli.OWNER_FILE_MODE)

        lock = nddev_kilo_cli.lock_path(target)
        os.symlink(outside, lock)
        try:
            with nddev_kilo_cli.target_lock(target):
                fail("symlink lock unexpectedly acquired")
        except nddev_kilo_cli.ManagerError:
            pass
        if outside.read_text(encoding="utf-8") != "preserve\n":
            fail("external marker changed through precreated lock path")
        lock.unlink()

        pool = nddev_kilo_cli.backup_pool(target)
        os.symlink(outside, pool)
        stamp = target / nddev_kilo_cli.STAMP_NAME
        stamp.write_bytes(
            nddev_kilo_cli.canonical_json(
                {
                    "schema_version": nddev_kilo_cli.STAMP_SCHEMA,
                    "product_name": nddev_kilo_cli.PRODUCT_NAME,
                    "build_version": nddev_kilo_cli.VERSION,
                    "setup_id": nddev_kilo_cli.DEFAULT_SETUP_ID,
                    "permission_profile": nddev_kilo_cli.DEFAULT_PROFILE_ID,
                    "canonical_target": str(target),
                    "managed_files": [],
                }
            )
        )
        stamp.chmod(nddev_kilo_cli.OWNER_FILE_MODE)
        try:
            nddev_kilo_cli.backup_current_state(target, nddev_kilo_cli.read_stamp(target) or {})
        except nddev_kilo_cli.ManagerError:
            pass
        else:
            fail("symlink backup pool was accepted")
        if outside.read_text(encoding="utf-8") != "preserve\n":
            fail("external marker changed through precreated backup path")
        pool.unlink()

        pool.mkdir(mode=0o777)
        pool.chmod(0o777)
        try:
            nddev_kilo_cli.backup_current_state(target, nddev_kilo_cli.read_stamp(target) or {})
        except nddev_kilo_cli.ManagerError:
            pass
        else:
            fail("world-writable backup pool was accepted")


def validate_sticky_tmp_target() -> None:
    candidates = [Path("/tmp"), Path(tempfile.gettempdir())]
    tmp = next((path for path in candidates if path.exists()), candidates[-1])
    info = tmp.lstat()
    if not stat.S_ISDIR(info.st_mode) or not (info.st_mode & stat.S_ISVTX):
        return
    target = Path(tempfile.mkdtemp(prefix="nddev-kilo-public-sticky-", dir=tmp))
    try:
        target.chmod(nddev_kilo_cli.OWNER_DIR_MODE)
        with nddev_kilo_cli.target_lock(target):
            pass
    finally:
        shutil.rmtree(target, ignore_errors=True)


def validate_fake_path_is_ignored() -> None:
    with tempfile.TemporaryDirectory(prefix="nddev-kilo-public-fakepath-") as raw:
        fake_dir = Path(raw)
        fake = fake_dir / "npm"
        fake.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        fake.chmod(0o700)
        original_path = os.environ.get("PATH")
        os.environ["PATH"] = str(fake_dir)
        try:
            try:
                npm, path_entries = nddev_kilo_cli.find_npm_executable()
            except nddev_kilo_cli.ManagerError:
                return
            if Path(npm) == fake:
                fail("fake npm from ambient PATH was selected")
            if str(fake_dir) in path_entries:
                fail("fake PATH entry was retained for install")
        finally:
            if original_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = original_path


def main() -> int:
    try:
        validate_versions()
        validate_contract()
        validate_baseline()
        validate_setups_and_profiles()
        validate_builder_toolkit()
        validate_workflows()
        validate_manager_parse_args()
        validate_launch_guard()
        validate_hardlink_materialization_bound()
        validate_package_lock_regressions()
        validate_wrong_platform_native_regression()
        validate_private_target_required()
        validate_lock_and_backup_precreation_guards()
        validate_sticky_tmp_target()
        validate_fake_path_is_ignored()
    except ValidationError as exc:
        print(f"public contract validation failed: {exc}", file=sys.stderr)
        return 1
    print("public contract validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
