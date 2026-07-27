#!/usr/bin/env python3
"""Validate the public nddev-kilo-cli-app release surface."""

from __future__ import annotations

import importlib.util
import contextlib
import json
import multiprocessing
import os
import signal
import stat
import subprocess
import sys
import tempfile
import shutil
import threading
import time
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
REAL_BOOTSTRAP_LOCK_PARENT = nddev_kilo_cli.bootstrap_lock_parent
EXPECTED_BOOTSTRAP_LOCK = dict(nddev_kilo_cli.BOOTSTRAP_LOCK_CONTRACT)
FORBIDDEN_BOOTSTRAP_OVERRIDE_NAMES = nddev_kilo_cli.FORBIDDEN_BOOTSTRAP_LOCK_ENV_NAMES
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
    runtime = manifest.get("runtime", {})
    if runtime.get("lifecycle_lock") != "persistent-flock-held-through-child":
        fail("manifest launch lifecycle lock contract mismatch")
    if runtime.get("bootstrap_lock") != EXPECTED_BOOTSTRAP_LOCK:
        fail("manifest bootstrap lifecycle lock contract mismatch")
    if runtime.get("lock_file") != str(
        nddev_kilo_cli.lock_file_path(Path("/target")).relative_to("/target")
    ):
        fail("manifest launch lock file contract mismatch")
    if runtime.get("lock_file_mode") != "0600":
        fail("manifest launch lock file mode mismatch")
    if runtime.get("lock_parent_mode_while_held") != "0500":
        fail("manifest launch lock parent mode mismatch")
    if runtime.get("lock_nonblocking_flock") is not True:
        fail("manifest launch lock must use nonblocking flock")
    if runtime.get("pre_child_executable_revalidation") is not True:
        fail("manifest launch executable revalidation must be enabled")
    if runtime.get("denies_lifecycle_mutations_while_running") is not True:
        fail("manifest launch mutation denial contract mismatch")
    if runtime.get("executable_handoff") != {
        "kind": "write-protected-verified-path",
        "portable_fd_execution": False,
        "parent_mode_while_held": "0500",
        "protected_directory_scope": "lock-parent-and-software-launcher-artifacts-only",
        "runtime_directories_writable_while_running": True,
        "same_uid_chmod_resistant_without_sandbox": False,
    }:
        fail("manifest executable handoff contract mismatch")
    expected_runtime_directories = {
        "inside_target_only": True,
        "component_validation": "real-current-user-owned-0700",
        "reject_symlink_components": True,
        "writable_while_launch_running": True,
    }
    if runtime.get("runtime_directories") != expected_runtime_directories:
        fail("manifest runtime directory isolation contract mismatch")
    software = manifest.get("software_lifecycle", {})
    if software.get("installer", {}).get("argv") != list(nddev_kilo_cli.NPM_INSTALL_ARGV):
        fail("manifest npm installer argv mismatch")
    installer = software.get("installer", {})
    if installer.get("lifecycle_scripts") != "disabled":
        fail("manifest installer lifecycle scripts must be disabled")
    if installer.get("nested_npm_fallback_allowed") is not False:
        fail("manifest installer must deny nested npm fallback")
    if software.get("layout", {}).get("package_lock") != str(nddev_kilo_cli.SOFTWARE_LOCK_RELATIVE):
        fail("manifest package-lock path mismatch")
    if software.get("layout", {}).get("manifest") != str(nddev_kilo_cli.SOFTWARE_MANIFEST_RELATIVE):
        fail("manifest software layout mismatch")
    if software.get("layout", {}).get("entrypoint_kind") != "target-owned-native-wrapper":
        fail("manifest entrypoint kind mismatch")
    if software.get("layout", {}).get("native_executable_policy") != "selected-native-package-bin-kilo":
        fail("manifest native executable policy mismatch")
    if software.get("bounds", {}).get("max_paths") != nddev_kilo_cli.SOFTWARE_TREE_MAX_PATHS:
        fail("manifest software path bound mismatch")
    if "stage_version_probe_timeout_seconds" in software.get("bounds", {}):
        fail("manifest must not declare an install-time target binary probe")


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
    if runtime_launch.get("lifecycle_lock") != "persistent-flock-held-through-child":
        fail("runtime launch must hold the target lifecycle lock through child completion")
    if runtime_launch.get("bootstrap_lock") != EXPECTED_BOOTSTRAP_LOCK:
        fail("runtime launch bootstrap lifecycle lock contract mismatch")
    if runtime_launch.get("lock_file") != str(
        nddev_kilo_cli.lock_file_path(Path("/target")).relative_to("/target")
    ):
        fail("runtime launch lock file contract mismatch")
    if runtime_launch.get("lock_file_mode") != "0600":
        fail("runtime launch lock file mode mismatch")
    if runtime_launch.get("lock_parent_mode_while_held") != "0500":
        fail("runtime launch lock parent mode mismatch")
    if runtime_launch.get("lock_nonblocking_flock") is not True:
        fail("runtime launch must use nonblocking flock")
    if runtime_launch.get("pre_child_executable_revalidation") is not True:
        fail("runtime launch must revalidate the executable before child start")
    if runtime_launch.get("denies_lifecycle_mutations_while_running") is not True:
        fail("runtime launch must deny lifecycle mutations while running")
    handoff = runtime_launch.get("executable_handoff")
    if handoff != {
        "kind": "write-protected-verified-path",
        "portable_fd_execution": False,
        "parent_mode_while_held": "0500",
        "protected_directory_scope": "lock-parent-and-software-launcher-artifacts-only",
        "runtime_directories_writable_while_running": True,
        "same_uid_chmod_resistant_without_sandbox": False,
    }:
        fail("runtime launch executable handoff contract mismatch")
    if runtime_launch.get("runtime_directories") != {
        "inside_target_only": True,
        "component_validation": "real-current-user-owned-0700",
        "reject_symlink_components": True,
        "writable_while_launch_running": True,
    }:
        fail("runtime launch directory isolation contract mismatch")
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
    if "--ignore-scripts" not in software.get("install_argv", []):
        fail("software lifecycle install argv must disable lifecycle scripts")
    postinstall = software.get("official_postinstall")
    if not isinstance(postinstall, dict):
        fail("official npm postinstall must be recorded as structured evidence")
    if (
        postinstall.get("script") != "node ./postinstall.mjs"
        or postinstall.get("executed") is not False
        or postinstall.get("nested_npm_fallback_allowed") is not False
    ):
        fail("official npm postinstall boundary is invalid")
    if software.get("lifecycle_scripts") != "disabled":
        fail("software lifecycle scripts must be disabled")
    if software.get("status_executes_binary") is not False:
        fail("software-status must not execute the target binary")
    if software.get("install_executes_target_binary") is not False:
        fail("install must not execute target software")
    if "stage_version_probe" in software or "stage_version_probe_timeout_seconds" in software:
        fail("software lifecycle must not declare an install-time target binary probe")
    if software.get("entrypoint_kind") != "target-owned-native-wrapper":
        fail("software lifecycle entrypoint kind mismatch")
    if software.get("native_executable_policy") != "selected-native-package-bin-kilo":
        fail("software lifecycle native executable policy mismatch")
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
    postinstall = npm.get("postinstall_analysis", {})
    if postinstall.get("source") != "https://registry.npmjs.org/@kilocode/cli/-/cli-7.4.16.tgz":
        fail("postinstall analysis must point at the official npm tarball")
    if postinstall.get("managed_policy") != (
        "install with --ignore-scripts and bind directly to the selected native package bin/kilo"
    ):
        fail("postinstall analysis managed policy mismatch")
    for key in (
        "executes_node_code",
        "executes_native_binary",
        "copies_and_removes_files",
        "uses_sysctl_or_ldd",
        "nested_npm_install_fallback",
    ):
        if postinstall.get(key) is not True:
            fail(f"postinstall analysis missing official behavior: {key}")
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


def validate_managed_source_exact_bytes() -> None:
    setup = nddev_kilo_cli.load_setup(nddev_kilo_cli.DEFAULT_SETUP_ID)
    for relative in nddev_kilo_cli.BUILDER_FILES:
        source = require_file(f"setups/nddev-builder/{relative}").read_bytes()
        if setup.files[relative] != source:
            fail(f"manager loaded different bytes than setup source: {relative}")


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
        focused_route = "../" + str(Path(relative).parent.name) + "/SKILL.md"
        if focused_route not in entry:
            fail(f"entry skill does not route to focused skill {relative}")
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
    release = require_file(".github/workflows/release.yml")
    release_text = release.read_text(encoding="utf-8")
    archive_paths = release_folded_paths(release_text, "archive_paths")
    runtime_paths = release_folded_paths(release_text, "runtime_paths")
    for label, paths in (("archive_paths", archive_paths), ("runtime_paths", runtime_paths)):
        if not paths:
            fail(f"release workflow omits {label}")
        for relative in paths:
            if not (ROOT / relative).exists():
                fail(f"release workflow {label} declares a missing root: {relative}")
    required_runtime_roots = {
        "README.md",
        "LICENSE",
        "VERSION",
        "build",
        "cli-tools",
        "config",
        "docs",
        nddev_kilo_cli.PROFILE_ROOT.name,
        "references",
        nddev_kilo_cli.CATALOG_ROOT.name,
    }
    for root in sorted(required_runtime_roots):
        if root not in runtime_paths:
            fail(f"release runtime_paths omit required runtime root: {root}")
        if root not in archive_paths:
            fail(f"release archive_paths omit required runtime root: {root}")


def release_path_is_safe(relative: str) -> bool:
    path = Path(relative)
    return not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts)


def release_required_source_files() -> set[str]:
    required = {
        f"{nddev_kilo_cli.CATALOG_ROOT.name}/{nddev_kilo_cli.DEFAULT_SETUP_ID}/setup.json",
        f"{nddev_kilo_cli.CATALOG_ROOT.name}/{nddev_kilo_cli.DEFAULT_SETUP_ID}/config.json",
    }
    for relative in nddev_kilo_cli.BUILDER_FILES:
        required.add(
            f"{nddev_kilo_cli.CATALOG_ROOT.name}/{nddev_kilo_cli.DEFAULT_SETUP_ID}/{relative}"
        )
    for profile_id in PROFILES:
        required.add(f"{nddev_kilo_cli.PROFILE_ROOT.name}/{profile_id}/profile.json")
        required.add(f"{nddev_kilo_cli.PROFILE_ROOT.name}/{profile_id}/config.json")
    return required


def release_paths_include(relative: str, roots: list[str]) -> bool:
    if not release_path_is_safe(relative):
        fail(f"required release source path is not safe: {relative}")
    path = Path(relative)
    for root in roots:
        if not release_path_is_safe(root):
            fail(f"release path is not a safe repository-relative path: {root}")
        root_path = Path(root)
        if path == root_path:
            return True
        try:
            path.relative_to(root_path)
        except ValueError:
            continue
        return True
    return False


def validate_release_archive_exact_bytes() -> None:
    release_text = require_file(".github/workflows/release.yml").read_text(encoding="utf-8")
    archive_paths = release_folded_paths(release_text, "archive_paths")
    runtime_paths = release_folded_paths(release_text, "runtime_paths")
    setup = nddev_kilo_cli.load_setup(nddev_kilo_cli.DEFAULT_SETUP_ID)
    for relative in release_required_source_files():
        require_file(relative)
        if not release_paths_include(relative, runtime_paths):
            fail(f"release runtime closure omits setup source file: {relative}")
        if not release_paths_include(relative, archive_paths):
            fail(f"release archive closure omits setup source file: {relative}")
    for relative in nddev_kilo_cli.BUILDER_FILES:
        source_relative = (
            f"{nddev_kilo_cli.CATALOG_ROOT.name}/"
            f"{nddev_kilo_cli.DEFAULT_SETUP_ID}/{relative}"
        )
        if setup.files[relative] != require_file(source_relative).read_bytes():
            fail(f"release source content is not the manager exact bytes: {source_relative}")


def release_folded_paths(text: str, key: str) -> list[str]:
    lines = text.splitlines()
    marker = f"      {key}: >-"
    for index, line in enumerate(lines):
        if line != marker:
            continue
        values: list[str] = []
        for candidate in lines[index + 1 :]:
            if not candidate.startswith("        "):
                break
            values.extend(candidate.split())
        return values
    return []


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
    with tempfile.TemporaryDirectory(prefix="nddev-kilo-public-env-") as raw:
        workspace = Path(raw)
        workspace.chmod(nddev_kilo_cli.OWNER_DIR_MODE)
        target = workspace / "target"
        target.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE)
        env = nddev_kilo_cli.launch_environment(target)
        for name in FORBIDDEN_BOOTSTRAP_OVERRIDE_NAMES:
            if name in env:
                fail(f"launch environment exposed bootstrap lock override name: {name}")
        if any(nddev_kilo_cli.BOOTSTRAP_LOCK_PREFIX in value for value in env.values()):
            fail("launch environment exposed the bootstrap lifecycle lock root")


def wait_until(label: str, predicate: Any, *, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    fail(f"timed out waiting for {label}")


def private_directory_mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def fake_launch_installation(
    target: Path,
    executable_content: bytes,
    *,
    native_content: bytes = b"fake-native\n",
) -> tuple[Path, Path, dict[str, Any]]:
    nddev_kilo_cli.mutate_setup(
        target,
        nddev_kilo_cli.DEFAULT_SETUP_ID,
        nddev_kilo_cli.DEFAULT_PROFILE_ID,
        "install",
    )
    nddev_kilo_cli.ensure_target_private_subdirectory(target, Path("bin"), "fake launch bin")
    executable = nddev_kilo_cli.kilo_executable(target)
    executable.write_bytes(executable_content)
    executable.chmod(0o700)
    native_relative = nddev_kilo_cli.native_package_binary_relative(
        "@kilocode/cli-linux-x64-baseline"
    )
    nddev_kilo_cli.ensure_target_private_subdirectory(
        target,
        native_relative.parent,
        "fake native package bin",
    )
    native = target / native_relative
    native.write_bytes(native_content)
    native.chmod(0o700)
    return (
        executable,
        native,
        {
            "installed": True,
            "current": True,
            "version": nddev_kilo_cli.KILO_CURRENT_VERSION,
            "executable": str(executable),
            "entrypoint_sha256": nddev_kilo_cli.sha256_bytes(executable.read_bytes()),
            "native_executable": str(native_relative),
            "native_executable_sha256": nddev_kilo_cli.sha256_bytes(native.read_bytes()),
        },
    )


def fake_current_software(
    canonical_target: Path,
    installation: dict[str, Any],
) -> Any:
    def fake_require_current_software(observed_target: Path) -> dict[str, Any]:
        if observed_target != canonical_target:
            fail("launch checked software for the wrong target")
        return dict(installation)

    return fake_require_current_software


def assert_launch_handoff_scope_is_immutable_only(
    target: Path,
    executable: Path,
    native: Path,
    installation: dict[str, Any],
) -> None:
    protected = set(nddev_kilo_cli.launch_handoff_directories(target, installation))
    required = {nddev_kilo_cli.lock_path(target), executable.parent, native.parent}
    if not required.issubset(protected):
        fail("launch handoff did not protect the required immutable directories")
    mutable_paths = {
        target,
        *nddev_kilo_cli.target_runtime_paths(target).values(),
        (target / nddev_kilo_cli.CONFIG).parent,
    }
    for mutable in mutable_paths:
        for directory in protected:
            if directory == mutable or directory in mutable.parents:
                fail(f"launch handoff protected a mutable runtime directory: {directory}")


def validate_launch_lock_scope_and_executable_revalidation() -> None:
    with tempfile.TemporaryDirectory(prefix="nddev-kilo-public-launch-") as raw:
        workspace = Path(raw)
        workspace.chmod(nddev_kilo_cli.OWNER_DIR_MODE)
        target = workspace / "target"
        target.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE)
        target = target.resolve(strict=False)
        canonical_target = target
        started = target / "child-started"
        stop = target / "child-stop"
        executable, _native, installation = fake_launch_installation(
            target,
            (
                "#!/bin/sh\n"
                "set -eu\n"
                f'printf started > "{started}"\n'
                f'while [ ! -f "{stop}" ]; do /bin/sleep 0.05; done\n'
            ).encode("utf-8"),
        )
        if nddev_kilo_cli.revalidate_launch_executable(target, installation) != str(executable):
            fail("launch executable revalidation returned the wrong executable")
        expect_manager_error(
            "launch executable with stale digest",
            lambda: nddev_kilo_cli.revalidate_launch_executable(
                target,
                {**installation, "entrypoint_sha256": "0" * 64},
            ),
        )
        expect_manager_error(
            "launch native executable with stale digest",
            lambda: nddev_kilo_cli.revalidate_launch_executable(
                target,
                {**installation, "native_executable_sha256": "0" * 64},
            ),
        )

        original_require_current_software = nddev_kilo_cli.require_current_software
        result: dict[str, Any] = {}

        def run_launch() -> None:
            try:
                result["code"] = nddev_kilo_cli.launch(target, [], timeout_seconds=10)
            except BaseException as exc:
                result["error"] = exc

        nddev_kilo_cli.require_current_software = fake_current_software(
            canonical_target,
            installation,
        )
        thread = threading.Thread(target=run_launch)
        thread.start()
        try:
            wait_until(
                "launch child start",
                lambda: started.exists() or bool(result.get("error")) or not thread.is_alive(),
            )
            if "error" in result:
                raise result["error"]
            if not thread.is_alive() and not started.exists():
                fail(f"launch child exited before start marker with code: {result.get('code')}")
            if not nddev_kilo_cli.lock_path(target).is_dir():
                fail("launch target lock was released while the child was running")
            if not nddev_kilo_cli.lock_file_path(target).is_file():
                fail("launch target lock file disappeared while the child was running")
            if private_directory_mode(nddev_kilo_cli.lock_path(target)) != 0o500:
                fail("launch target lock parent remained writable while the child was running")
            try:
                nddev_kilo_cli.mutate_setup(
                    target,
                    nddev_kilo_cli.DEFAULT_SETUP_ID,
                    "safe",
                    "switch",
                )
            except nddev_kilo_cli.ManagerError as exc:
                if "target is locked" not in str(exc):
                    fail(f"running launch denied mutation for the wrong reason: {exc}")
            else:
                fail("lifecycle mutation was accepted while launch child was running")
            stop.write_bytes(b"stop\n")
            stop.chmod(nddev_kilo_cli.OWNER_FILE_MODE)
            thread.join(timeout=5)
        finally:
            nddev_kilo_cli.require_current_software = original_require_current_software
            if thread.is_alive():
                stop.write_bytes(b"stop\n")
                thread.join(timeout=10)
        if thread.is_alive():
            fail("launch child thread did not finish")
        if "error" in result:
            raise result["error"]
        if result.get("code") != 0:
            fail(f"launch child returned unexpected code: {result.get('code')}")
        if not nddev_kilo_cli.lock_file_path(target).is_file():
            fail("persistent launch target lock file was removed after child exit")
        if private_directory_mode(nddev_kilo_cli.lock_path(target)) != 0o700:
            fail("launch target lock parent was not restored after child exit")


def validate_child_cannot_unlink_persistent_lock() -> None:
    with tempfile.TemporaryDirectory(prefix="nddev-kilo-public-flock-") as raw:
        workspace = Path(raw)
        workspace.chmod(nddev_kilo_cli.OWNER_DIR_MODE)
        target = workspace / "target"
        target.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE)
        target = target.resolve(strict=False)
        started = target / "child-started"
        result_path = target / "child-lock-result"
        stop = target / "child-stop"
        executable, _native, installation = fake_launch_installation(
            target,
            (
                "#!/bin/sh\n"
                "set -eu\n"
                f'printf started > "{started}"\n'
                f'if /bin/rm -f "{nddev_kilo_cli.lock_file_path(target)}" 2>/dev/null; then\n'
                f'  if [ -e "{nddev_kilo_cli.lock_file_path(target)}" ]; then\n'
                f'    printf denied > "{result_path}"\n'
                "  else\n"
                f'    printf removed > "{result_path}"\n'
                "  fi\n"
                "else\n"
                f'  printf denied > "{result_path}"\n'
                "fi\n"
                f'while [ ! -f "{stop}" ]; do /bin/sleep 0.05; done\n'
            ).encode("utf-8"),
        )
        del executable
        original_require_current_software = nddev_kilo_cli.require_current_software
        launch_result: dict[str, Any] = {}

        def run_launch() -> None:
            try:
                launch_result["code"] = nddev_kilo_cli.launch(target, [], timeout_seconds=10)
            except BaseException as exc:
                launch_result["error"] = exc

        nddev_kilo_cli.require_current_software = fake_current_software(target, installation)
        thread = threading.Thread(target=run_launch)
        thread.start()
        try:
            wait_until(
                "child lock unlink attempt",
                lambda: result_path.exists() or bool(launch_result.get("error")),
            )
            if "error" in launch_result:
                raise launch_result["error"]
            if result_path.read_text(encoding="utf-8") != "denied":
                fail("launch child removed the persistent lifecycle lock file")
            if not nddev_kilo_cli.lock_file_path(target).is_file():
                fail("persistent lifecycle lock file is missing while launch is running")
            try:
                nddev_kilo_cli.mutate_setup(
                    target,
                    nddev_kilo_cli.DEFAULT_SETUP_ID,
                    "safe",
                    "switch",
                )
            except nddev_kilo_cli.ManagerError as exc:
                if "target is locked" not in str(exc):
                    fail(f"persistent flock denied mutation for the wrong reason: {exc}")
            else:
                fail("lifecycle mutation was accepted after child lock unlink attempt")
            stop.write_bytes(b"stop\n")
            stop.chmod(nddev_kilo_cli.OWNER_FILE_MODE)
            thread.join(timeout=5)
        finally:
            nddev_kilo_cli.require_current_software = original_require_current_software
            if thread.is_alive():
                stop.write_bytes(b"stop\n")
                thread.join(timeout=10)
        if thread.is_alive():
            fail("launch child thread did not finish after lock unlink regression")
        if "error" in launch_result:
            raise launch_result["error"]
        if launch_result.get("code") != 0:
            fail(
                "lock unlink regression child returned unexpected code: "
                f"{launch_result.get('code')}"
            )
        if private_directory_mode(nddev_kilo_cli.lock_path(target)) != 0o700:
            fail("persistent lock parent mode was not restored after child exit")


def validate_external_lock_blocks_internal_lock_parent_rename() -> None:
    with tempfile.TemporaryDirectory(prefix="nddev-kilo-public-dual-lock-") as raw:
        workspace = Path(raw)
        workspace.chmod(nddev_kilo_cli.OWNER_DIR_MODE)
        target = workspace / "target"
        target.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE)
        target = target.resolve(strict=False)
        started = target / "child-started"
        renamed = target / ".nddev-kilo-cli.lock.renamed"
        rename_result = target / "child-rename-result"
        stop = target / "child-stop"
        executable, _native, installation = fake_launch_installation(
            target,
            (
                "#!/bin/sh\n"
                "set -eu\n"
                f'printf started > "{started}"\n'
                f'if /bin/mv "{nddev_kilo_cli.lock_path(target)}" "{renamed}" 2>/dev/null; then\n'
                f'  printf renamed > "{rename_result}"\n'
                "else\n"
                f'  printf denied > "{rename_result}"\n'
                "fi\n"
                f'while [ ! -f "{stop}" ]; do /bin/sleep 0.05; done\n'
            ).encode("utf-8"),
        )
        del executable
        original_require_current_software = nddev_kilo_cli.require_current_software
        launch_result: dict[str, Any] = {}

        def run_launch() -> None:
            try:
                launch_result["code"] = nddev_kilo_cli.launch(target, [], timeout_seconds=10)
            except BaseException as exc:
                launch_result["error"] = exc

        nddev_kilo_cli.require_current_software = fake_current_software(target, installation)
        thread = threading.Thread(target=run_launch)
        thread.start()
        try:
            wait_until(
                "child internal lock parent rename attempt",
                lambda: rename_result.exists() or bool(launch_result.get("error")),
            )
            if "error" in launch_result:
                raise launch_result["error"]
            if rename_result.read_text(encoding="utf-8") != "renamed":
                fail("internal lock parent rename regression did not exercise the rename path")
            for label, mutation in (
                (
                    "switch",
                    lambda: nddev_kilo_cli.mutate_setup(
                        target,
                        nddev_kilo_cli.DEFAULT_SETUP_ID,
                        "safe",
                        "switch",
                    ),
                ),
                ("remove", lambda: nddev_kilo_cli.remove_setup(target)),
                (
                    "install",
                    lambda: nddev_kilo_cli.mutate_setup(
                        target,
                        nddev_kilo_cli.DEFAULT_SETUP_ID,
                        nddev_kilo_cli.DEFAULT_PROFILE_ID,
                        "install",
                    ),
                ),
            ):
                try:
                    mutation()
                except nddev_kilo_cli.ManagerError as exc:
                    if "target is locked" not in str(exc):
                        fail(f"{label} after internal lock rename failed for the wrong reason: {exc}")
                else:
                    fail(f"{label} was accepted after child renamed the internal lock parent")
            stop.write_bytes(b"stop\n")
            stop.chmod(nddev_kilo_cli.OWNER_FILE_MODE)
            thread.join(timeout=5)
        finally:
            nddev_kilo_cli.require_current_software = original_require_current_software
            if thread.is_alive():
                stop.write_bytes(b"stop\n")
                thread.join(timeout=10)
            if renamed.exists() and not renamed.is_symlink():
                with contextlib.suppress(OSError):
                    renamed.chmod(nddev_kilo_cli.OWNER_DIR_MODE)
        if thread.is_alive():
            fail("launch child thread did not finish after internal lock rename regression")
        if "error" in launch_result:
            raise launch_result["error"]
        if launch_result.get("code") != 0:
            fail(
                "internal lock rename child returned unexpected code: "
                f"{launch_result.get('code')}"
            )
        if not nddev_kilo_cli.lock_path(target).is_dir():
            fail("canonical internal lock parent was not restored after child rename")
        if private_directory_mode(nddev_kilo_cli.lock_path(target)) != 0o700:
            fail("canonical internal lock parent mode was not restored after child rename")


def validate_launch_handoff_denies_ordinary_replace_unlink() -> None:
    with tempfile.TemporaryDirectory(prefix="nddev-kilo-public-handoff-") as raw:
        workspace = Path(raw)
        workspace.chmod(nddev_kilo_cli.OWNER_DIR_MODE)
        target = workspace / "target"
        target.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE)
        target = target.resolve(strict=False)
        started = target / "child-started"
        stop = target / "child-stop"
        runtime_paths = nddev_kilo_cli.target_runtime_paths(target)
        runtime_writes = {
            "HOME": runtime_paths["HOME"] / "nddev-runtime-home",
            "TMPDIR": runtime_paths["TMPDIR"] / "nddev-runtime-tmp",
            "XDG_CONFIG_HOME": runtime_paths["XDG_CONFIG_HOME"] / "nddev-runtime-config",
            "XDG_DATA_HOME": runtime_paths["XDG_DATA_HOME"] / "nddev-runtime-data",
            "XDG_STATE_HOME": runtime_paths["XDG_STATE_HOME"] / "nddev-runtime-state",
            "XDG_CACHE_HOME": runtime_paths["XDG_CACHE_HOME"] / "nddev-runtime-cache",
            "KILO_CONFIG_PARENT": (target / nddev_kilo_cli.CONFIG).parent
            / "nddev-runtime-session",
        }
        runtime_ready = runtime_paths["TMPDIR"] / "nddev-runtime-ready"
        executable, native, installation = fake_launch_installation(
            target,
            (
                "#!/bin/sh\n"
                "set -eu\n"
                'config_dir=${KILO_CONFIG%/*}\n'
                'printf home > "$HOME/nddev-runtime-home"\n'
                'printf tmp > "$TMPDIR/nddev-runtime-tmp"\n'
                'printf config > "$XDG_CONFIG_HOME/nddev-runtime-config"\n'
                'printf data > "$XDG_DATA_HOME/nddev-runtime-data"\n'
                'printf state > "$XDG_STATE_HOME/nddev-runtime-state"\n'
                'printf cache > "$XDG_CACHE_HOME/nddev-runtime-cache"\n'
                'printf session > "$config_dir/nddev-runtime-session"\n'
                'printf ready > "$TMPDIR/nddev-runtime-ready"\n'
                f'printf started > "{started}"\n'
                f'while [ ! -f "{stop}" ]; do /bin/sleep 0.05; done\n'
            ).encode("utf-8"),
        )
        assert_launch_handoff_scope_is_immutable_only(target, executable, native, installation)
        original_require_current_software = nddev_kilo_cli.require_current_software
        launch_result: dict[str, Any] = {}

        def run_launch() -> None:
            try:
                launch_result["code"] = nddev_kilo_cli.launch(target, [], timeout_seconds=10)
            except BaseException as exc:
                launch_result["error"] = exc

        nddev_kilo_cli.require_current_software = fake_current_software(target, installation)
        thread = threading.Thread(target=run_launch)
        thread.start()
        try:
            wait_until(
                "handoff launch child start",
                lambda: runtime_ready.exists()
                or bool(launch_result.get("error"))
                or not thread.is_alive(),
            )
            if "error" in launch_result:
                raise launch_result["error"]
            if not thread.is_alive() and not runtime_ready.exists():
                fail(f"handoff child exited before runtime writes with code: {launch_result.get('code')}")
            for label, path in runtime_writes.items():
                if not path.is_file():
                    fail(f"launch child could not write runtime state in {label}")
            if private_directory_mode(executable.parent) != 0o500:
                fail("launch wrapper parent remained writable while child was running")
            if private_directory_mode(native.parent) != 0o500:
                fail("launch native binary parent remained writable while child was running")
            if private_directory_mode(nddev_kilo_cli.lock_path(target)) != 0o500:
                fail("launch lock parent remained writable while child was running")
            for protected in (executable, native):
                before = protected.read_bytes()
                try:
                    protected.unlink()
                except OSError:
                    pass
                else:
                    fail(f"ordinary unlink succeeded for launch-protected path: {protected}")
                replacement = target / f"replacement-{protected.name}"
                replacement.write_bytes(b"replacement\n")
                replacement.chmod(0o700)
                try:
                    os.replace(replacement, protected)
                except OSError:
                    pass
                else:
                    fail(f"ordinary replace succeeded for launch-protected path: {protected}")
                if protected.read_bytes() != before:
                    fail(f"launch-protected path changed during handoff: {protected}")
            try:
                nddev_kilo_cli.lock_file_path(target).unlink()
            except OSError:
                pass
            else:
                fail("ordinary unlink succeeded for persistent lock file")
            lock_replacement = target / "replacement-lock"
            lock_replacement.write_bytes(b"replacement\n")
            lock_replacement.chmod(nddev_kilo_cli.OWNER_FILE_MODE)
            try:
                os.replace(lock_replacement, nddev_kilo_cli.lock_file_path(target))
            except OSError:
                pass
            else:
                fail("ordinary replace succeeded for persistent lock file")
            if not nddev_kilo_cli.lock_file_path(target).is_file():
                fail("persistent lock file changed during launch handoff")
            stop.write_bytes(b"stop\n")
            stop.chmod(nddev_kilo_cli.OWNER_FILE_MODE)
            thread.join(timeout=5)
        finally:
            nddev_kilo_cli.require_current_software = original_require_current_software
            if thread.is_alive():
                stop.write_bytes(b"stop\n")
                thread.join(timeout=10)
        if thread.is_alive():
            fail("launch child thread did not finish after handoff regression")
        if "error" in launch_result:
            raise launch_result["error"]
        if launch_result.get("code") != 0:
            fail(
                "handoff regression child returned unexpected code: "
                f"{launch_result.get('code')}"
            )
        if private_directory_mode(executable.parent) != 0o700:
            fail("launch wrapper parent mode was not restored after child exit")
        if private_directory_mode(native.parent) != 0o700:
            fail("launch native binary parent mode was not restored after child exit")


def validate_stale_launch_protection_recovery() -> None:
    with tempfile.TemporaryDirectory(prefix="nddev-kilo-public-stale-") as raw:
        workspace = Path(raw)
        workspace.chmod(nddev_kilo_cli.OWNER_DIR_MODE)
        target = workspace / "target"
        target.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE)
        nddev_kilo_cli.ensure_target_private_subdirectory(target, Path("bin"), "stale bin")
        install_root = target / nddev_kilo_cli.SOFTWARE_PREFIX_RELATIVE
        nddev_kilo_cli.ensure_target_private_subdirectory(
            target,
            nddev_kilo_cli.SOFTWARE_PREFIX_RELATIVE,
            "stale install root",
        )
        chmod_targets = [target / "bin", install_root]
        for directory in chmod_targets:
            nddev_kilo_cli.chmod_directory_no_follow(
                directory,
                nddev_kilo_cli.LOCK_HELD_PARENT_MODE,
                "stale launch-protected test directory",
            )
        with nddev_kilo_cli.target_lock(target):
            for directory in chmod_targets:
                if private_directory_mode(directory) != nddev_kilo_cli.OWNER_DIR_MODE:
                    fail("target lock did not recover stale launch-protected directory mode")
        for directory in chmod_targets:
            if private_directory_mode(directory) != nddev_kilo_cli.OWNER_DIR_MODE:
                fail("stale launch-protected directory mode was not restored after lock")

def validate_runtime_paths_reject_symlinks_before_child() -> None:
    original_require_active_clean_installed = nddev_kilo_cli.require_active_clean_installed
    original_require_current_software = nddev_kilo_cli.require_current_software
    for env_name, relative in nddev_kilo_cli.target_runtime_relative_paths().items():
        prefixes = [Path(*relative.parts[:index]) for index in range(1, len(relative.parts) + 1)]
        for prefix in prefixes:
            with tempfile.TemporaryDirectory(prefix="nddev-kilo-public-runtime-") as raw:
                workspace = Path(raw)
                workspace.chmod(nddev_kilo_cli.OWNER_DIR_MODE)
                target = workspace / "target"
                target.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE)
                target = target.resolve(strict=False)
                canonical_target = target
                outside = workspace / "outside"
                outside.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE)
                started = workspace / "child-started"
                executable = nddev_kilo_cli.kilo_executable(target)
                executable.parent.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE, parents=True)
                executable.write_bytes(
                    (
                        "#!/bin/sh\n"
                        "set -eu\n"
                        f'printf started > "{started}"\n'
                    ).encode("utf-8")
                )
                executable.chmod(0o700)
                native_relative = nddev_kilo_cli.native_package_binary_relative(
                    "@kilocode/cli-linux-x64-baseline"
                )
                native = target / native_relative
                native.parent.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE, parents=True)
                native.write_bytes(b"fake-native\n")
                native.chmod(0o700)
                link = target / prefix
                link.parent.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE, parents=True, exist_ok=True)
                os.symlink(outside, link)

                def fake_require_active_clean_installed(observed_target: Path) -> dict[str, Any]:
                    if observed_target != canonical_target:
                        fail("runtime path regression checked the wrong target stamp")
                    return {
                        "schema_version": nddev_kilo_cli.STAMP_SCHEMA,
                        "setup_id": nddev_kilo_cli.DEFAULT_SETUP_ID,
                        "permission_profile": nddev_kilo_cli.DEFAULT_PROFILE_ID,
                    }

                def fake_require_current_software(observed_target: Path) -> dict[str, Any]:
                    if observed_target != canonical_target:
                        fail("runtime path regression checked the wrong target software")
                    return {
                        "installed": True,
                        "current": True,
                        "version": nddev_kilo_cli.KILO_CURRENT_VERSION,
                        "executable": str(executable),
                        "entrypoint_sha256": nddev_kilo_cli.sha256_bytes(executable.read_bytes()),
                        "native_executable": str(native_relative),
                        "native_executable_sha256": nddev_kilo_cli.sha256_bytes(native.read_bytes()),
                    }

                nddev_kilo_cli.require_active_clean_installed = fake_require_active_clean_installed
                nddev_kilo_cli.require_current_software = fake_require_current_software
                try:
                    try:
                        nddev_kilo_cli.launch(target, [], timeout_seconds=1)
                    except nddev_kilo_cli.ManagerError:
                        pass
                    else:
                        fail(f"runtime {env_name} symlink component was accepted: {prefix}")
                finally:
                    nddev_kilo_cli.require_active_clean_installed = (
                        original_require_active_clean_installed
                    )
                    nddev_kilo_cli.require_current_software = original_require_current_software
                if started.exists():
                    fail(f"launch child started despite runtime {env_name} symlink: {prefix}")


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


def without_lifecycle_lock(
    records: list[tuple[str, str, int, bytes | str | None]],
) -> list[tuple[str, str, int, bytes | str | None]]:
    lock_root = nddev_kilo_cli.LOCK_RELATIVE.as_posix()
    return [
        record
        for record in records
        if record[0] != lock_root and not record[0].startswith(f"{lock_root}/")
    ]


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

        if without_lifecycle_lock(snapshot_tree(target)) != without_lifecycle_lock(before):
            fail("failed hardlink materialization changed the target tree")


def write_public_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE, parents=True, exist_ok=True)
    path.write_bytes(nddev_kilo_cli.canonical_json(value))
    path.chmod(nddev_kilo_cli.OWNER_FILE_MODE)


def validate_remove_exhausts_managed_files() -> None:
    with tempfile.TemporaryDirectory(prefix="nddev-kilo-public-remove-") as raw:
        workspace = Path(raw)
        workspace.chmod(nddev_kilo_cli.OWNER_DIR_MODE)
        target = workspace / "target"
        target.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE)
        unmanaged_file = target / "unmanaged.txt"
        unmanaged_file.write_bytes(b"preserve me\n")
        unmanaged_file.chmod(nddev_kilo_cli.OWNER_FILE_MODE)
        write_public_json(target / nddev_kilo_cli.CONFIG, {"unmanaged": {"preserve": True}})

        nddev_kilo_cli.mutate_setup(
            target,
            nddev_kilo_cli.DEFAULT_SETUP_ID,
            nddev_kilo_cli.DEFAULT_PROFILE_ID,
            "install",
        )
        desired = nddev_kilo_cli.desired_for_remove(target)
        if set(desired) != {*MANAGED_FILES, nddev_kilo_cli.STAMP_NAME}:
            fail("remove desired state is not built from the complete managed file set")
        for relative in MANAGED_FILES:
            content = desired.get(relative)
            if relative == nddev_kilo_cli.CONFIG:
                if content is None:
                    fail("remove dropped parseable unmanaged config keys")
                continue
            if content is not None:
                fail(f"remove did not delete builder-owned path: {relative}")

        before_failure = snapshot_tree(target)
        original_replace_managed_state = nddev_kilo_cli.replace_managed_state

        def partial_remove_then_fail(
            observed_target: Path,
            desired_state: dict[str, bytes | None],
            expected: dict[str, str] | None = None,
            **kwargs: Any,
        ) -> None:
            del expected, kwargs
            for relative, content in desired_state.items():
                if relative != nddev_kilo_cli.CONFIG and content is None:
                    with contextlib.suppress(FileNotFoundError):
                        (observed_target / nddev_kilo_cli.safe_relative_path(relative)).unlink()
                    break
            raise RuntimeError("forced remove failure")

        nddev_kilo_cli.replace_managed_state = partial_remove_then_fail
        try:
            try:
                nddev_kilo_cli.remove_setup(target)
            except RuntimeError:
                pass
            else:
                fail("forced remove failure was accepted")
        finally:
            nddev_kilo_cli.replace_managed_state = original_replace_managed_state
        if snapshot_tree(target) != before_failure:
            fail("failed remove did not roll back every removed managed path")

        nddev_kilo_cli.remove_setup(target)
        for relative in MANAGED_FILES:
            path = target / nddev_kilo_cli.safe_relative_path(relative)
            if relative == nddev_kilo_cli.CONFIG:
                observed = load_json(path)
                if observed != {"unmanaged": {"preserve": True}}:
                    fail("remove did not preserve unmanaged config keys")
                continue
            if path.exists() or path.is_symlink():
                fail(f"remove left builder-owned path behind: {relative}")
        if nddev_kilo_cli.stamp_path(target).exists():
            fail("remove left the managed stamp behind")
        if unmanaged_file.read_bytes() != b"preserve me\n":
            fail("remove changed an unmanaged file")
        nddev_kilo_cli.mutate_setup(
            target,
            nddev_kilo_cli.DEFAULT_SETUP_ID,
            nddev_kilo_cli.DEFAULT_PROFILE_ID,
            "install",
        )


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


@contextlib.contextmanager
def patched_bootstrap_lock_parent(parent: Path) -> Any:
    original = nddev_kilo_cli.bootstrap_lock_parent
    nddev_kilo_cli.bootstrap_lock_parent = lambda: parent
    try:
        yield
    finally:
        nddev_kilo_cli.bootstrap_lock_parent = original


def bootstrap_product_root(parent: Path) -> Path:
    return parent / f"{nddev_kilo_cli.BOOTSTRAP_LOCK_PREFIX}-{os.geteuid()}"


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def real_bootstrap_root_path() -> Path:
    return bootstrap_product_root(REAL_BOOTSTRAP_LOCK_PARENT())


def snapshot_bootstrap_root_shallow(
    root: Path,
) -> list[dict[str, Any]]:
    def record_for(path: Path, relative: str, info: os.stat_result) -> dict[str, Any]:
        record: dict[str, Any] = {
            "relative": relative,
            "kind": "other",
            "mode": stat.S_IMODE(info.st_mode),
            "dev": info.st_dev,
            "ino": info.st_ino,
            "uid": getattr(info, "st_uid", None),
            "gid": getattr(info, "st_gid", None),
            "nlink": info.st_nlink,
            "size": info.st_size,
        }
        if stat.S_ISLNK(info.st_mode):
            record["kind"] = "symlink"
            record["link_target"] = os.readlink(path)
        elif stat.S_ISDIR(info.st_mode):
            record["kind"] = "directory"
        elif stat.S_ISREG(info.st_mode):
            record["kind"] = "file"
            if info.st_size <= nddev_kilo_cli.BOOTSTRAP_LOCK_MAX_BYTES:
                record["sha256"] = nddev_kilo_cli.sha256_bytes(path.read_bytes())
            else:
                record["sha256"] = None
        return record

    try:
        info = root.lstat()
    except FileNotFoundError:
        return [{"relative": ".", "kind": "absent"}]
    if not stat.S_ISDIR(info.st_mode):
        return [record_for(root, ".", info)]
    records = [record_for(root, ".", info)]
    entries = sorted(root.iterdir(), key=lambda item: item.name)
    if len(entries) > 256:
        fail("real bootstrap lock root contains too many entries for bounded validation")
    for path in entries:
        records.append(record_for(path, path.name, path.lstat()))
    return records


def snapshot_real_bootstrap_state() -> list[dict[str, Any]]:
    return snapshot_bootstrap_root_shallow(real_bootstrap_root_path())


def validate_real_bootstrap_state_unchanged(
    before: list[dict[str, Any]],
) -> None:
    after = snapshot_real_bootstrap_state()
    if after != before:
        fail("public validation changed the real fixed bootstrap lock root")


def validate_public_bootstrap_override_denial() -> None:
    original = {name: os.environ.get(name) for name in FORBIDDEN_BOOTSTRAP_OVERRIDE_NAMES}
    try:
        for name in FORBIDDEN_BOOTSTRAP_OVERRIDE_NAMES:
            for other in FORBIDDEN_BOOTSTRAP_OVERRIDE_NAMES:
                os.environ.pop(other, None)
            os.environ[name] = "/tmp/forbidden-nddev-kilo-bootstrap-override"
            expect_manager_error(
                f"bootstrap lock override environment {name}",
                lambda: REAL_BOOTSTRAP_LOCK_PARENT(),
            )
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def validate_bootstrap_injection_is_active(parent: Path) -> None:
    root = nddev_kilo_cli.bootstrap_lock_root()
    if not is_relative_to(root, parent):
        fail("public validator bootstrap lock injection is not active")
    if root == real_bootstrap_root_path():
        fail("public validator bootstrap lock injection resolved to the real product root")


def validate_bootstrap_lock_precreation_guards() -> None:
    with tempfile.TemporaryDirectory(prefix="nddev-kilo-public-bootstrap-") as raw:
        workspace = Path(raw)
        workspace.chmod(nddev_kilo_cli.OWNER_DIR_MODE)
        parent = workspace / "bootstrap-parent"
        parent.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE)
        target_parent = workspace / "targets"
        target_parent.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE)
        target = nddev_kilo_cli.canonical_target_for_bootstrap_lock(target_parent / "target")
        outside = workspace / "outside"
        outside.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE)
        marker = outside / "marker"
        marker.write_bytes(b"preserve\n")
        marker.chmod(nddev_kilo_cli.OWNER_FILE_MODE)

        def acquire_bootstrap() -> None:
            with nddev_kilo_cli.bootstrap_lifecycle_lock(target):
                pass

        with patched_bootstrap_lock_parent(parent):
            root = nddev_kilo_cli.bootstrap_lock_root()
            os.symlink(outside, root)
            expect_manager_error("symlink bootstrap lock root", acquire_bootstrap)
            if marker.read_bytes() != b"preserve\n":
                fail("external marker changed through precreated bootstrap root")
            root.unlink()

            root.mkdir(mode=0o777)
            root.chmod(0o777)
            expect_manager_error("world-writable bootstrap lock root", acquire_bootstrap)
            root.chmod(nddev_kilo_cli.OWNER_DIR_MODE)
            shutil.rmtree(root)

            root.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE)
            os.symlink(marker, nddev_kilo_cli.bootstrap_lock_file_path(target))
            expect_manager_error("symlink bootstrap lock file", acquire_bootstrap)
            if marker.read_bytes() != b"preserve\n":
                fail("external marker changed through precreated bootstrap lock file")
            nddev_kilo_cli.bootstrap_lock_file_path(target).unlink()

            lock_file = nddev_kilo_cli.bootstrap_lock_file_path(target)
            lock_file.write_bytes(b"unsafe\n")
            lock_file.chmod(0o644)
            expect_manager_error("world-readable bootstrap lock file", acquire_bootstrap)
            lock_file.unlink()

            lock_file.write_bytes(b"{not-json\n")
            lock_file.chmod(nddev_kilo_cli.OWNER_FILE_MODE)
            expect_manager_error("malformed bootstrap lock binding", acquire_bootstrap)
            lock_file.unlink()

            lock_file.write_bytes(b"[]\n")
            lock_file.chmod(nddev_kilo_cli.OWNER_FILE_MODE)
            expect_manager_error("non-object bootstrap lock binding", acquire_bootstrap)
            lock_file.unlink()

            write_public_json(
                lock_file,
                {
                    "schema_version": nddev_kilo_cli.BOOTSTRAP_LOCK_SCHEMA,
                    "product_name": nddev_kilo_cli.PRODUCT_NAME,
                    "target": str(target),
                    "target_sha256": nddev_kilo_cli.target_binding_sha256(target),
                    "extra": True,
                },
            )
            expect_manager_error("extra-key bootstrap lock binding", acquire_bootstrap)
            lock_file.unlink()

            write_public_json(
                lock_file,
                {
                    "schema_version": nddev_kilo_cli.BOOTSTRAP_LOCK_SCHEMA,
                    "product_name": nddev_kilo_cli.PRODUCT_NAME,
                    "target": str(
                        nddev_kilo_cli.canonical_target_for_bootstrap_lock(target_parent / "other")
                    ),
                    "target_sha256": nddev_kilo_cli.target_binding_sha256(
                        nddev_kilo_cli.canonical_target_for_bootstrap_lock(target_parent / "other")
                    ),
                },
            )
            expect_manager_error("mismatched bootstrap lock binding", acquire_bootstrap)
            lock_file.unlink()

            write_public_json(
                lock_file,
                {
                    "schema_version": nddev_kilo_cli.BOOTSTRAP_LOCK_SCHEMA + 1,
                    "product_name": nddev_kilo_cli.PRODUCT_NAME,
                    "target": str(target),
                    "target_sha256": nddev_kilo_cli.target_binding_sha256(target),
                },
            )
            expect_manager_error("unknown bootstrap lock schema", acquire_bootstrap)
            lock_file.unlink()

            with nddev_kilo_cli.bootstrap_lifecycle_lock(target) as locked_target:
                if locked_target != target:
                    fail("bootstrap lock returned the wrong canonical target")
            lock_info = lock_file.lstat()
            if private_directory_mode(root) != nddev_kilo_cli.OWNER_DIR_MODE:
                fail("bootstrap lock root mode is not 0700 after successful acquire")
            if stat.S_IMODE(lock_info.st_mode) != nddev_kilo_cli.OWNER_FILE_MODE:
                fail("bootstrap lock file mode is not 0600 after successful acquire")
            if lock_info.st_nlink != 1:
                fail("bootstrap lock file has unexpected hard-link aliases")
            with nddev_kilo_cli.bootstrap_lifecycle_lock(target):
                pass
            if lock_file.lstat().st_ino != lock_info.st_ino:
                fail("bootstrap lock file inode changed across normal release/reacquire")
            record = load_json(lock_file)
            expected = {
                "schema_version": nddev_kilo_cli.BOOTSTRAP_LOCK_SCHEMA,
                "product_name": nddev_kilo_cli.PRODUCT_NAME,
                "target": str(target),
                "target_sha256": nddev_kilo_cli.target_binding_sha256(target),
            }
            if record != expected:
                fail("bootstrap lock binding is not the canonical expected object")


def wait_for_process(pid: int, label: str, *, seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        observed, status = os.waitpid(pid, os.WNOHANG)
        if observed == 0:
            time.sleep(0.02)
            continue
        if os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0:
            return
        fail(f"{label} exited unsuccessfully: status {status}")
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)
    with contextlib.suppress(ChildProcessError):
        os.waitpid(pid, 0)
    fail(f"{label} did not exit before timeout")


def terminate_process(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        with contextlib.suppress(ChildProcessError):
            observed, _status = os.waitpid(pid, os.WNOHANG)
            if observed != 0:
                return
        time.sleep(0.02)
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)
    with contextlib.suppress(ChildProcessError):
        os.waitpid(pid, 0)


def fork_validation_child(label: str, error_dir: Path, fn: Any) -> int:
    if not hasattr(os, "fork"):
        fail("bootstrap lock handover regression requires POSIX fork")
    pid = os.fork()
    if pid == 0:
        try:
            fn()
        except BaseException as exc:
            with contextlib.suppress(Exception):
                (error_dir / f"{label}.error").write_text(
                    f"{type(exc).__name__}: {exc}\n",
                    encoding="utf-8",
                )
            os._exit(1)
        os._exit(0)
    return pid


def validate_bootstrap_lock_persistent_inode_handover() -> None:
    if "fork" not in multiprocessing.get_all_start_methods():
        fail("bootstrap lock handover regression requires fork-capable multiprocessing")
    with tempfile.TemporaryDirectory(prefix="nddev-kilo-public-handover-") as raw:
        workspace = Path(raw)
        workspace.chmod(nddev_kilo_cli.OWNER_DIR_MODE)
        target_parent = workspace / "targets"
        target_parent.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE)
        target = nddev_kilo_cli.canonical_target_for_bootstrap_lock(target_parent / "target")
        with nddev_kilo_cli.bootstrap_lifecycle_lock(target):
            pass
        lock_file = nddev_kilo_cli.bootstrap_lock_file_path(target)
        original_inode = lock_file.lstat().st_ino
        a_ready = workspace / "a-ready"
        a_release = workspace / "a-release"
        b_holding = workspace / "b-holding"
        b_release = workspace / "b-release"
        c_denied = workspace / "c-denied"

        def process_a() -> None:
            with nddev_kilo_cli.bootstrap_lifecycle_lock(target):
                if lock_file.lstat().st_ino != original_inode:
                    raise RuntimeError("process A observed a replaced bootstrap lock inode")
                a_ready.write_text("ready\n", encoding="utf-8")
                while not a_release.exists():
                    time.sleep(0.02)

        def process_b() -> None:
            while not a_ready.exists():
                time.sleep(0.02)
            while True:
                try:
                    with nddev_kilo_cli.bootstrap_lifecycle_lock(target):
                        if lock_file.lstat().st_ino != original_inode:
                            raise RuntimeError("process B observed a replaced bootstrap lock inode")
                        b_holding.write_text("holding\n", encoding="utf-8")
                        while not b_release.exists():
                            time.sleep(0.02)
                        return
                except nddev_kilo_cli.ManagerError as exc:
                    if "target is locked" not in str(exc):
                        raise
                    time.sleep(0.02)

        def process_c() -> None:
            try:
                with nddev_kilo_cli.bootstrap_lifecycle_lock(target):
                    raise RuntimeError("process C acquired the lock while process B held it")
            except nddev_kilo_cli.ManagerError as exc:
                if "target is locked" not in str(exc):
                    raise
                c_denied.write_text("denied\n", encoding="utf-8")

        pids: list[tuple[int, str]] = []
        try:
            pid_a = fork_validation_child("a", workspace, process_a)
            pids.append((pid_a, "process A"))
            wait_until("process A bootstrap lock", a_ready.exists)
            pid_b = fork_validation_child("b", workspace, process_b)
            pids.append((pid_b, "process B"))
            time.sleep(0.1)
            if b_holding.exists():
                fail("process B acquired the bootstrap lock before process A released it")
            a_release.write_text("release\n", encoding="utf-8")
            wait_until("process B bootstrap lock", b_holding.exists)
            wait_for_process(pid_a, "process A")
            pids = [(pid, label) for pid, label in pids if pid != pid_a]
            pid_c = fork_validation_child("c", workspace, process_c)
            pids.append((pid_c, "process C"))
            wait_for_process(pid_c, "process C")
            pids = [(pid, label) for pid, label in pids if pid != pid_c]
            if c_denied.read_text(encoding="utf-8") != "denied\n":
                fail("process C did not record bootstrap lock denial")
            b_release.write_text("release\n", encoding="utf-8")
            wait_for_process(pid_b, "process B")
            pids = [(pid, label) for pid, label in pids if pid != pid_b]
        finally:
            for pid, label in pids:
                del label
                terminate_process(pid)
        if lock_file.lstat().st_ino != original_inode:
            fail("bootstrap lock file inode changed after 3-process handover")
        record = load_json(lock_file)
        if record.get("target") != str(target):
            fail("bootstrap lock persistent binding target mismatch after handover")


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


def write_fake_cli_package(root: Path) -> None:
    package_root = (
        root
        / nddev_kilo_cli.SOFTWARE_GLOBAL_DIR_RELATIVE
        / "node_modules"
        / "@kilocode"
        / "cli"
    )
    bin_root = package_root / "bin"
    bin_root.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE, parents=True)
    write_public_json(
        package_root / "package.json",
        {
            "name": nddev_kilo_cli.KILO_PACKAGE,
            "version": nddev_kilo_cli.KILO_CURRENT_VERSION,
            "bin": {
                "kilo": "./bin/kilo",
                "kilocode": "./bin/kilo",
            },
            "scripts": {"postinstall": "node ./postinstall.mjs"},
            "optionalDependencies": expected_optional_native_versions(),
        },
    )
    package_bin = bin_root / nddev_kilo_cli.KILO_COMMAND
    package_bin.write_bytes(b"vendor package bin, not executed by nddev\n")
    package_bin.chmod(0o700)


def write_fake_native_package(
    root: Path,
    package: str,
    record: dict[str, Any],
    binary: bytes,
    *,
    tree_sitter: bool = False,
) -> None:
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
    if tree_sitter:
        tree_sitter_root = bin_root / "tree-sitter"
        tree_sitter_root.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE)
        wasm = tree_sitter_root / "tree-sitter.wasm"
        wasm.write_bytes(b"fake wasm\n")
        wasm.chmod(nddev_kilo_cli.OWNER_FILE_MODE)


def with_host_platform(host: dict[str, str | None], fn: Any) -> Any:
    original_host_native_platform = nddev_kilo_cli.host_native_platform
    nddev_kilo_cli.host_native_platform = lambda: dict(host)
    try:
        return fn()
    finally:
        nddev_kilo_cli.host_native_platform = original_host_native_platform


def validate_native_selection_and_wrapper_regressions() -> None:
    cases = (
        ({"os": "darwin", "cpu": "arm64", "libc": None}, "@kilocode/cli-darwin-arm64"),
        ({"os": "darwin", "cpu": "x64", "libc": None}, "@kilocode/cli-darwin-x64-baseline"),
        ({"os": "linux", "cpu": "arm64", "libc": None}, "@kilocode/cli-linux-arm64"),
        ({"os": "linux", "cpu": "arm64", "libc": "musl"}, "@kilocode/cli-linux-arm64-musl"),
        ({"os": "linux", "cpu": "x64", "libc": None}, "@kilocode/cli-linux-x64-baseline"),
        (
            {"os": "linux", "cpu": "x64", "libc": "musl"},
            "@kilocode/cli-linux-x64-baseline-musl",
        ),
    )
    supported, _unsupported = nddev_kilo_cli.native_package_matrix()
    for host, expected in cases:
        with tempfile.TemporaryDirectory(prefix="nddev-kilo-public-select-") as raw:
            root = Path(raw)
            root.chmod(nddev_kilo_cli.OWNER_DIR_MODE)
            write_public_json(
                root / nddev_kilo_cli.SOFTWARE_LOCK_RELATIVE,
                valid_package_lock((expected,)),
            )
            binary = f"native for {expected}\n".encode("utf-8")
            write_fake_native_package(
                root,
                expected,
                supported[expected],
                binary,
                tree_sitter=(host["os"] == "linux" and host["cpu"] == "x64"),
            )

            def inspect_selection() -> None:
                if nddev_kilo_cli.selected_native_package_name_for_host(host) != expected:
                    fail(f"selected native package mismatch for {host}")
                provenance = nddev_kilo_cli.installed_native_packages(root)
                selected = provenance["selected"]
                if selected["name"] != expected:
                    fail(f"installed native package selection mismatch for {host}")
                if selected["binary"] != str(nddev_kilo_cli.native_package_binary_relative(expected)):
                    fail(f"selected native binary path mismatch for {host}")
                nddev_kilo_cli.materialize_stage_entrypoint(root, provenance)
                wrapper = (root / "bin" / nddev_kilo_cli.KILO_COMMAND).read_text(
                    encoding="utf-8"
                )
                if ".kilo" in wrapper:
                    fail("native wrapper depends on vendor postinstall .kilo")
                if selected["binary"] not in wrapper:
                    fail("native wrapper does not execute the selected package binary")
                resources = selected["runtime_resources"]
                if resources.get("tree_sitter", {}).get("present") is True:
                    if nddev_kilo_cli.NATIVE_TREE_SITTER_ENV not in wrapper:
                        fail("native wrapper omits valid tree-sitter resource env")

            with_host_platform(host, inspect_selection)


def validate_npm_install_ignores_lifecycle_scripts() -> None:
    supported, _unsupported = nddev_kilo_cli.native_package_matrix()
    host = nddev_kilo_cli.host_native_platform()
    selected = nddev_kilo_cli.selected_native_package_name_for_host(host)
    commands: list[tuple[list[str], dict[str, str], Path]] = []
    with tempfile.TemporaryDirectory(prefix="nddev-kilo-public-npm-") as raw:
        stage_root = Path(raw)
        stage_root.chmod(nddev_kilo_cli.OWNER_DIR_MODE)
        live_stage = stage_root / "live"
        live_stage.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE)

        def fake_find_npm_executable() -> tuple[str, tuple[str, ...]]:
            return "/usr/bin/npm", ("/usr/bin",)

        def fake_fetch_registry_metadata() -> dict[str, Any]:
            baseline = nddev_kilo_cli.baseline()
            npm = baseline["npm"]
            return {
                "name": nddev_kilo_cli.KILO_PACKAGE,
                "version": nddev_kilo_cli.KILO_CURRENT_VERSION,
                "dist": npm["dist"],
                "scripts": npm["scripts"],
                "optionalDependencies": expected_optional_native_versions(),
            }

        def fake_bounded_process(
            command: list[str],
            *,
            cwd: Path,
            env: dict[str, str],
            timeout: int,
        ) -> subprocess.CompletedProcess[str]:
            del timeout
            commands.append((list(command), dict(env), cwd))
            if "--ignore-scripts" not in command:
                fail("npm command omitted --ignore-scripts")
            if env.get("NPM_CONFIG_IGNORE_SCRIPTS") != "true":
                fail("npm command omitted NPM_CONFIG_IGNORE_SCRIPTS=true")
            if env.get("npm_config_ignore_scripts") != "true":
                fail("npm command omitted npm_config_ignore_scripts=true")
            if "--package-lock-only" in command:
                write_public_json(cwd / "package-lock.json", valid_package_lock((selected,)))
            elif "--global" in command:
                write_fake_cli_package(live_stage)
                write_fake_native_package(
                    live_stage,
                    selected,
                    supported[selected],
                    b"fake selected native\n",
                    tree_sitter=True,
                )
            else:
                fail(f"unexpected npm command in script-free install: {command}")
            return subprocess.CompletedProcess(command, 0, "", "")

        original_find_npm_executable = nddev_kilo_cli.find_npm_executable
        original_fetch_registry_metadata = nddev_kilo_cli.fetch_registry_metadata
        original_bounded_process = nddev_kilo_cli.bounded_process
        nddev_kilo_cli.find_npm_executable = fake_find_npm_executable
        nddev_kilo_cli.fetch_registry_metadata = fake_fetch_registry_metadata
        nddev_kilo_cli.bounded_process = fake_bounded_process
        try:
            nddev_kilo_cli.run_npm_install(stage_root, live_stage)
        finally:
            nddev_kilo_cli.find_npm_executable = original_find_npm_executable
            nddev_kilo_cli.fetch_registry_metadata = original_fetch_registry_metadata
            nddev_kilo_cli.bounded_process = original_bounded_process

        if len(commands) != 2:
            fail("script-free install executed an unexpected number of npm commands")
        for command, _env, _cwd in commands:
            if "--no-save" in command:
                fail("script-free install attempted vendor nested npm fallback")
        npmrc = (stage_root / "npmrc").read_text(encoding="utf-8")
        if "ignore-scripts=true" not in npmrc:
            fail("sanitized npmrc does not disable lifecycle scripts")
        for relative in nddev_kilo_cli.VENDOR_POSTINSTALL_RESOURCE_RELATIVES:
            if (live_stage / relative).exists() or (live_stage / relative).is_symlink():
                fail(f"vendor postinstall artifact was materialized: {relative}")
        wrapper = (live_stage / "bin" / nddev_kilo_cli.KILO_COMMAND).read_text(encoding="utf-8")
        if ".kilo" in wrapper:
            fail("script-free install wrapper depends on vendor postinstall .kilo")
        if str(nddev_kilo_cli.native_package_binary_relative(selected)) not in wrapper:
            fail("script-free install wrapper does not use the selected native package")


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

        def acquire_target_lock() -> None:
            with nddev_kilo_cli.target_lock(target):
                pass

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

        lock.mkdir(mode=0o777)
        lock.chmod(0o777)
        expect_manager_error(
            "world-writable lock parent",
            acquire_target_lock,
        )
        shutil.rmtree(lock)

        lock.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE)
        os.symlink(outside, nddev_kilo_cli.lock_file_path(target))
        expect_manager_error(
            "symlink lock file",
            acquire_target_lock,
        )
        if outside.read_text(encoding="utf-8") != "preserve\n":
            fail("external marker changed through precreated lock file")
        nddev_kilo_cli.lock_file_path(target).unlink()
        lock.rmdir()

        lock.mkdir(mode=nddev_kilo_cli.OWNER_DIR_MODE)
        nddev_kilo_cli.lock_file_path(target).write_bytes(b"unsafe\n")
        nddev_kilo_cli.lock_file_path(target).chmod(0o644)
        expect_manager_error(
            "world-readable lock file",
            acquire_target_lock,
        )
        nddev_kilo_cli.lock_file_path(target).unlink()
        lock.rmdir()

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


def validate_no_public_bootstrap_override_references() -> None:
    scanned_roots = (
        ROOT / ".github",
        ROOT / "build",
        ROOT / "config",
        ROOT / "docs",
        ROOT / "setups",
        ROOT / "README.md",
    )
    for root in scanned_roots:
        paths = [root] if root.is_file() else sorted(path for path in root.rglob("*") if path.is_file())
        for path in paths:
            if path.stat().st_size > nddev_kilo_cli.METADATA_MAX_BYTES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for name in FORBIDDEN_BOOTSTRAP_OVERRIDE_NAMES:
                if name in text:
                    fail(f"public artifact documents unsupported bootstrap override: {path}")


def run_all_validations(injected_bootstrap_parent: Path) -> None:
    with patched_bootstrap_lock_parent(injected_bootstrap_parent):
        validate_bootstrap_injection_is_active(injected_bootstrap_parent)
        validate_versions()
        validate_contract()
        validate_baseline()
        validate_setups_and_profiles()
        validate_managed_source_exact_bytes()
        validate_builder_toolkit()
        validate_workflows()
        validate_release_archive_exact_bytes()
        validate_manager_parse_args()
        validate_launch_guard()
        validate_bootstrap_lock_precreation_guards()
        validate_bootstrap_lock_persistent_inode_handover()
        validate_launch_lock_scope_and_executable_revalidation()
        validate_child_cannot_unlink_persistent_lock()
        validate_external_lock_blocks_internal_lock_parent_rename()
        validate_launch_handoff_denies_ordinary_replace_unlink()
        validate_stale_launch_protection_recovery()
        validate_runtime_paths_reject_symlinks_before_child()
        validate_hardlink_materialization_bound()
        validate_package_lock_regressions()
        validate_native_selection_and_wrapper_regressions()
        validate_npm_install_ignores_lifecycle_scripts()
        validate_wrong_platform_native_regression()
        validate_private_target_required()
        validate_lock_and_backup_precreation_guards()
        validate_sticky_tmp_target()
        validate_fake_path_is_ignored()
        validate_no_public_bootstrap_override_references()


def main() -> int:
    real_bootstrap_before: list[dict[str, Any]] | None = None
    try:
        validate_public_bootstrap_override_denial()
        real_bootstrap_before = snapshot_real_bootstrap_state()
        with tempfile.TemporaryDirectory(prefix="nddev-kilo-public-bootstrap-root-") as raw:
            injected = Path(raw)
            injected.chmod(nddev_kilo_cli.OWNER_DIR_MODE)
            run_all_validations(injected)
        validate_real_bootstrap_state_unchanged(real_bootstrap_before)
    except ValidationError as exc:
        if real_bootstrap_before is not None:
            with contextlib.suppress(ValidationError):
                validate_real_bootstrap_state_unchanged(real_bootstrap_before)
        print(f"public contract validation failed: {exc}", file=sys.stderr)
        return 1
    print("public contract validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
