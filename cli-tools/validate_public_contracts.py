#!/usr/bin/env python3
"""Validate the public nddev-kilo-cli-app release surface."""

from __future__ import annotations

import ast
import contextlib
import importlib.util
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

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
EXPECTED_CLEANUP_JOURNAL = dict(nddev_kilo_cli.CLEANUP_JOURNAL_CONTRACT)
FORBIDDEN_BOOTSTRAP_OVERRIDE_NAMES = nddev_kilo_cli.FORBIDDEN_BOOTSTRAP_LOCK_ENV_NAMES
EXPECTED_CHANGED_PATH_POLICY = "actual-byte-diff"
EXPECTED_PYTHON_REQUIRES = ">=3.9"
EXPECTED_SETUP_COMMANDS = [
    "list",
    "status",
    "plan",
    "install",
    "switch",
    "update",
    "migrate",
    "restore",
    "remove",
]
EXPECTED_SOFTWARE_COMMANDS = [
    "software-status",
    "install-cli",
    "update-cli",
    "remove-cli",
]
EXPECTED_TARGET_BOUND_REJECT_BEFORE = [
    "target",
    "setup",
    "bootstrap-lock",
    "internal-lock",
    "staging",
    "network",
    "launch",
]
EXPECTED_PLATFORM_SCOPE = {
    "supported_hosts": [
        {"id": "macos-arm64", "os": "darwin", "cpu": "arm64", "libc": None, "distribution": None},
        {"id": "macos-x64", "os": "darwin", "cpu": "x64", "libc": None, "distribution": None},
        {
            "id": "ubuntu-glibc-arm64",
            "os": "linux",
            "cpu": "arm64",
            "libc": "glibc",
            "distribution": "ubuntu",
            "ubuntu_version_floor": None,
            "ubuntu_version_floor_source": "no-official-floor",
        },
        {
            "id": "ubuntu-glibc-x64",
            "os": "linux",
            "cpu": "x64",
            "libc": "glibc",
            "distribution": "ubuntu",
            "ubuntu_version_floor": None,
            "ubuntu_version_floor_source": "no-official-floor",
        },
    ],
    "unsupported_categories": [
        "windows",
        "non-ubuntu-linux",
        "linux-musl",
        "unsupported-architecture",
    ],
    "reject_before": ["target", "lock", "staging", "network", "launch"],
    "vendor_package_preferences": {
        "macos-arm64": ["@kilocode/cli-darwin-arm64"],
        "macos-x64": ["@kilocode/cli-darwin-x64-baseline", "@kilocode/cli-darwin-x64"],
        "ubuntu-glibc-arm64": ["@kilocode/cli-linux-arm64"],
        "ubuntu-glibc-x64": ["@kilocode/cli-linux-x64-baseline", "@kilocode/cli-linux-x64"],
    },
    "vendor_optional_catalog": "references/kilo-cli-baseline.json:npm.native_packages.catalog",
}
EXPECTED_ARCHIVE_AUTHORITY = {
    "package_tarball": f"https://registry.npmjs.org/@kilocode/cli/-/cli-{KILO_VERSION}.tgz",
    "native_tarball": "host-selected-production-native-package",
    "verification": ["sha512-integrity", "sha1-shasum"],
    "materialization": "extract-verified-local-bytes-without-scripts",
}
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


def copy_json(value: Any) -> Any:
    return json.loads(json.dumps(value))


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
    if version.get("python_requires") != EXPECTED_PYTHON_REQUIRES:
        fail("build version python_requires must include macOS system Python 3.9")
    if getattr(nddev_kilo_cli, "PYTHON_REQUIRES", None) != EXPECTED_PYTHON_REQUIRES:
        fail("manager python_requires is not synchronized")
    if manifest.get("python_requires") != EXPECTED_PYTHON_REQUIRES:
        fail("manifest python_requires must include macOS system Python 3.9")
    if manifest.get("setups") != list(SETUPS):
        fail("manifest setup list is not synchronized")
    if manifest.get("permission_profiles") != list(PROFILES):
        fail("manifest permission profile list is not synchronized")
    if manifest.get("managed_state") != list(MANAGED_FILES):
        fail("manifest managed state list is not synchronized")
    if manifest.get("setup_lifecycle") != {
        "manager_commands": EXPECTED_SETUP_COMMANDS,
        "target_bound_reject_before": EXPECTED_TARGET_BOUND_REJECT_BEFORE,
        "cleanup_journal": EXPECTED_CLEANUP_JOURNAL,
        "plan_changed_paths": EXPECTED_CHANGED_PATH_POLICY,
        "mutation_changed_paths": EXPECTED_CHANGED_PATH_POLICY,
    }:
        fail("manifest setup lifecycle contract mismatch")
    runtime = manifest.get("runtime", {})
    if runtime.get("lifecycle_lock") != "persistent-flock-held-through-child":
        fail("manifest launch lifecycle lock contract mismatch")
    if (
        runtime.get("cleanup_pending_policy")
        != "drain-under-target-lock-before-launch-image-protection-child"
    ):
        fail("manifest launch cleanup-pending policy mismatch")
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
    if software.get("manager_commands") != EXPECTED_SOFTWARE_COMMANDS:
        fail("manifest software lifecycle command list mismatch")
    installer = software.get("installer", {})
    if installer.get("tool") != "verified-npm-archive-set":
        fail("manifest installer must use verified npm archive set materialization")
    if {
        key: installer.get(key) for key in EXPECTED_ARCHIVE_AUTHORITY
    } != EXPECTED_ARCHIVE_AUTHORITY:
        fail("manifest installer archive authority mismatch")
    if "argv" in installer:
        fail("manifest installer must not declare npm install argv")
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
    if (
        software.get("layout", {}).get("native_executable_policy")
        != "selected-native-package-bin-kilo"
    ):
        fail("manifest native executable policy mismatch")
    if software.get("bounds", {}).get("max_paths") != nddev_kilo_cli.SOFTWARE_TREE_MAX_PATHS:
        fail("manifest software path bound mismatch")
    if "stage_version_probe_timeout_seconds" in software.get("bounds", {}):
        fail("manifest must not declare an install-time target binary probe")
    if software.get("platform_scope") != EXPECTED_PLATFORM_SCOPE:
        fail("manifest platform scope must be macOS plus Ubuntu glibc only")


def validate_contract() -> None:
    contract = load_json(require_file("config/nddev-contract.json"))
    if set(contract) != CONTRACT_KEYS:
        fail("public contract top-level keys are not exact")
    if contract.get("contract_version") != 2:
        fail("unexpected public contract version")
    runtime_compatibility = contract.get("runtime_compatibility", {})
    if runtime_compatibility.get("python_requires") != EXPECTED_PYTHON_REQUIRES:
        fail("public contract python_requires must include macOS system Python 3.9")
    runtime_launch = contract.get("runtime_launch", {})
    if runtime_launch.get("subcommand") != ["run"]:
        fail("runtime launch subcommand must be kilo run")
    if runtime_launch.get("auto_for_profiles") != ["full-auto"]:
        fail("runtime launch --auto must be limited to full-auto profile")
    if runtime_launch.get("lifecycle_lock") != "persistent-flock-held-through-child":
        fail("runtime launch must hold the target lifecycle lock through child completion")
    if (
        runtime_launch.get("cleanup_pending_policy")
        != "drain-under-target-lock-before-launch-image-protection-child"
    ):
        fail("runtime launch cleanup-pending policy mismatch")
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
    expected_lifecycle = [*EXPECTED_SETUP_COMMANDS, *EXPECTED_SOFTWARE_COMMANDS, "launch"]
    if setup_system.get("lifecycle") != expected_lifecycle:
        fail("setup lifecycle command list is not synchronized")
    if setup_system.get("target_bound_reject_before") != EXPECTED_TARGET_BOUND_REJECT_BEFORE:
        fail("setup/status target-bound reject-before contract is not synchronized")
    if setup_system.get("cleanup_journal") != EXPECTED_CLEANUP_JOURNAL:
        fail("cleanup journal contract is not synchronized")
    if setup_system.get("plan_changed_paths") != EXPECTED_CHANGED_PATH_POLICY:
        fail("plan changed-path policy is not synchronized")
    if setup_system.get("mutation_changed_paths") != EXPECTED_CHANGED_PATH_POLICY:
        fail("mutation changed-path policy is not synchronized")
    managed = contract.get("managed_state", {})
    if managed.get("managed_files") != list(MANAGED_FILES):
        fail("managed file list is not synchronized")
    if "plugin" not in managed.get("managed_config_keys", []):
        fail("plugin must be a managed config key")
    software = contract.get("software_lifecycle", {})
    if software.get("install_tool") != "verified-npm-archive-set":
        fail("software lifecycle must use verified npm archive set materialization")
    if software.get("archive_authority") != EXPECTED_ARCHIVE_AUTHORITY:
        fail("software lifecycle archive authority mismatch")
    if "install_argv" in software:
        fail("software lifecycle must not declare npm install argv")
    identity = nddev_kilo_cli.software_manifest_identity()
    if identity.get("install_method") != "verified-npm-archive-set-ignore-scripts":
        fail("software manifest identity must use verified archive install method")
    if identity.get("archive_authority") != EXPECTED_ARCHIVE_AUTHORITY:
        fail("software manifest identity archive authority mismatch")
    if "npm_install_argv" in identity or "npm_lock_argv" in identity:
        fail("software manifest identity must not record npm argv authority")
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
    if software.get("platform_scope") != EXPECTED_PLATFORM_SCOPE:
        fail("software lifecycle platform scope must be macOS plus Ubuntu glibc only")
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
    product = baseline.get("product", {})
    if product.get("managed_install_channel") != "target-owned verified npm archive extraction":
        fail("baseline managed install channel must be verified archive extraction")
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
        "verify pinned JS and selected native package tarballs before extracting local bytes "
        "without lifecycle scripts"
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
    catalog = native_packages.get("catalog", {})
    production = native_packages.get("production", {})
    if not isinstance(catalog, dict) or not isinstance(production, dict):
        fail("native package baseline must include catalog and production maps")
    expected_production = set(nddev_kilo_cli.PRODUCTION_NATIVE_PACKAGES)
    if set(production) != expected_production:
        fail("native package production map is not the macOS/Ubuntu glibc allowlist")
    if not set(production).issubset(catalog):
        fail("native package production map is not a subset of the vendor catalog")
    for package, record in catalog.items():
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
    for package, record in production.items():
        if record.get("os") == ["darwin"]:
            if record.get("cpu") not in (["arm64"], ["x64"]) or record.get("libc") is not None:
                fail(f"macOS production native package has invalid metadata: {package}")
        elif record.get("os") == ["linux"]:
            if record.get("cpu") not in (["arm64"], ["x64"]) or record.get("libc") is not None:
                fail(f"Ubuntu production native package must be glibc x64/arm64: {package}")
            if "musl" in package:
                fail(f"musl native package must not be production-selected: {package}")
        else:
            fail(f"production native package is not macOS or Ubuntu Linux: {package}")
    for package, record in catalog.items():
        if package in production:
            continue
        if record.get("os") not in (["linux"], ["win32"]):
            fail(f"non-production vendor catalog package has unexpected OS: {package}")
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


def validate_claude_bridge() -> None:
    bridge_root = ROOT / ".claude"
    try:
        bridge_root_info = bridge_root.lstat()
    except FileNotFoundError:
        fail(".claude directory is missing")
    if stat.S_ISLNK(bridge_root_info.st_mode) or not stat.S_ISDIR(bridge_root_info.st_mode):
        fail(".claude must be a real directory, not a symlink")
    if sorted(path.name for path in bridge_root.iterdir()) != ["CLAUDE.md"]:
        fail('.claude must contain exactly ["CLAUDE.md"]')

    bridge = bridge_root / "CLAUDE.md"
    try:
        bridge_info = bridge.lstat()
    except FileNotFoundError:
        fail(".claude/CLAUDE.md is missing")
    if stat.S_ISLNK(bridge_info.st_mode) or not stat.S_ISREG(bridge_info.st_mode):
        fail(".claude/CLAUDE.md must be a real regular file, not a symlink")
    if bridge.read_bytes() != b"@../AGENTS.md\n":
        fail(".claude/CLAUDE.md must contain exactly '@../AGENTS.md\\n'")

    agents = ROOT / "AGENTS.md"
    try:
        agents_info = agents.lstat()
    except FileNotFoundError:
        fail("AGENTS.md is missing")
    if stat.S_ISLNK(agents_info.st_mode) or not stat.S_ISREG(agents_info.st_mode):
        fail("AGENTS.md must be a real regular file, not a symlink")


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
        ".claude",
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
    return not path.is_absolute() and all(
        part not in {"", ".", "..", ".git"} for part in path.parts
    )


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
            f"{nddev_kilo_cli.CATALOG_ROOT.name}/{nddev_kilo_cli.DEFAULT_SETUP_ID}/{relative}"
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
    nddev_kilo_cli.parse_args(["update", "--target", "/tmp/nddev-kilo-check", "--json"])
    nddev_kilo_cli.parse_args(
        ["switch", "--profile", "safe", "--target", "/tmp/nddev-kilo-check", "--json"]
    )
    nddev_kilo_cli.parse_args(["migrate", "--profile", "safe", "--target", "/tmp/nddev-kilo-check"])
    nddev_kilo_cli.parse_args(["status", "--target", "/tmp/nddev-kilo-check", "--json"])
    nddev_kilo_cli.parse_args(["software-status", "--target", "/tmp/nddev-kilo-check", "--json"])
    for argv in (
        ["status", "--json"],
        ["switch", "--profile", "unsafe", "--target", "/tmp/nddev-kilo-check", "--json"],
    ):
        try:
            with (
                open(os.devnull, "w", encoding="utf-8") as devnull,
                contextlib.redirect_stderr(devnull),
            ):
                nddev_kilo_cli.parse_args(argv)
        except nddev_kilo_cli.ManagerError as exc:
            if "argument error:" not in str(exc):
                fail(f"argparse failure used unexpected error boundary for {argv}: {exc}")
        else:
            fail(f"argparse accepted invalid argv: {argv}")


def validate_python_portability() -> None:
    manager_source = MANAGER_PATH.read_text(encoding="utf-8")
    for path in (
        MANAGER_PATH,
        ROOT / "cli-tools" / "validate_public_contracts.py",
    ):
        source = path.read_text(encoding="utf-8")
        try:
            ast.parse(source, filename=str(path), feature_version=(3, 9))
        except SyntaxError as exc:
            fail(f"{path.relative_to(ROOT)} is not Python 3.9 syntax-compatible: {exc}")
    if sys.version_info < (3, 9):
        fail("validator runtime requires Python 3.9 or newer")
    tree = ast.parse(manager_source, filename=str(MANAGER_PATH), feature_version=(3, 9))

    def dotted_name(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = dotted_name(node.value)
            if parent is None:
                return node.attr
            return f"{parent}.{node.attr}"
        return None

    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    forbidden_calls = {
        "os.replace",
        "os.rename",
        "os.ftruncate",
        "Path.rename",
        "Path.replace",
        "Path.open",
        "open",
    }
    for function_name in (
        "publish_bootstrap_anchor_file",
        "publish_cleanup_intent_file",
        "publish_cleanup_journal_file",
    ):
        publish = functions.get(function_name)
        if publish is None:
            fail(f"manager is missing {function_name}")
        calls = [dotted_name(node.func) for node in ast.walk(publish) if isinstance(node, ast.Call)]
        if "os.link" not in calls:
            fail(f"{function_name} must use an atomic no-replace link primitive")
        for node in ast.walk(publish):
            if isinstance(node, ast.Call):
                name = dotted_name(node.func)
                if name in forbidden_calls:
                    fail(f"{function_name} uses forbidden call: {name}")
                if isinstance(node.func, ast.Attribute) and node.func.attr in {
                    "rename",
                    "replace",
                    "truncate",
                }:
                    fail(f"{function_name} must not rename, replace, or truncate anchors")
            if isinstance(node, ast.Attribute) and node.attr == "O_TRUNC":
                fail(f"{function_name} must not open with O_TRUNC")
    read_lock = functions.get("bootstrap_read_lifecycle_lock")
    if read_lock is None:
        fail("manager is missing bootstrap_read_lifecycle_lock")
    read_calls = [
        dotted_name(node.func) for node in ast.walk(read_lock) if isinstance(node, ast.Call)
    ]
    if "recover_bootstrap_publication_alias" in read_calls:
        fail("read-only bootstrap lifecycle lock must not recover publication aliases")
    cleanup_graph = functions.get("validate_cleanup_graph_record_schema")
    if cleanup_graph is None:
        fail("manager is missing validate_cleanup_graph_record_schema")
    cleanup_graph_source = ast.get_source_segment(manager_source, cleanup_graph) or ""
    for expected in (
        "len(graph) > CLEANUP_MAX_PATHS",
        "total_file_size > CLEANUP_MAX_BYTES",
    ):
        if expected not in cleanup_graph_source:
            fail("cleanup journal graph validation must enforce declared bounds")
    read_journal = functions.get("read_cleanup_bound_json_file")
    if read_journal is None:
        fail("manager is missing read_cleanup_bound_json_file")
    read_journal_source = ast.get_source_segment(manager_source, read_journal) or ""
    if "CLEANUP_JOURNAL_MAX_BYTES" not in read_journal_source:
        fail("cleanup journal reader must use the journal serialized-byte bound")
    if "METADATA_MAX_BYTES" in read_journal_source:
        fail("cleanup journal reader must not use the generic metadata byte bound")
    if "except FileNotFoundError" not in read_journal_source:
        fail("cleanup journal reader must convert missing files to ManagerError")
    if "recover_cleanup_prepare_root_only" not in functions:
        fail("manager must recover exact prepare-only cleanup roots under mutation")
    publish_journal = functions.get("publish_cleanup_journal_file")
    if publish_journal is None:
        fail("manager is missing publish_cleanup_journal_file")
    publish_journal_calls = [
        dotted_name(node.func) for node in ast.walk(publish_journal) if isinstance(node, ast.Call)
    ]
    if "ensure_cleanup_journal_serialized_bound" not in publish_journal_calls:
        fail("cleanup journal writer must enforce the serialized-byte bound")
    publish_intent = functions.get("publish_cleanup_intent_file")
    if publish_intent is None:
        fail("manager is missing publish_cleanup_intent_file")
    publish_intent_calls = [
        dotted_name(node.func) for node in ast.walk(publish_intent) if isinstance(node, ast.Call)
    ]
    if "ensure_cleanup_journal_serialized_bound" not in publish_intent_calls:
        fail("cleanup intent writer must enforce the serialized-byte bound")
    promote_cleanup = functions.get("promote_cleanup_tombstone")
    if promote_cleanup is None:
        fail("manager is missing promote_cleanup_tombstone")
    promote_source = ast.get_source_segment(manager_source, promote_cleanup) or ""
    promote_calls = [
        dotted_name(node.func) for node in ast.walk(promote_cleanup) if isinstance(node, ast.Call)
    ]
    if "projected_cleanup_graph_records" not in promote_calls:
        fail("cleanup journal builder must preflight projected serialized size")
    if "cleanup_journal_content" not in promote_calls:
        fail("cleanup journal builder must share the serialized-byte bound")
    for required_call in (
        "cleanup_intent_content",
        "publish_cleanup_intent_file",
        "validate_cleanup_intent",
    ):
        if required_call not in promote_calls:
            fail("cleanup tombstone promotion must publish durable intent before moves")
    for function_name in (
        "cleanup_intent_source_binding",
        "cleanup_intent_source_from_binding",
    ):
        if function_name not in functions:
            fail("cleanup intent must bind sources by fixed anchor and relative path")
    if '"source": str(source)' in manager_source:
        fail("cleanup intent must not serialize unbound absolute source paths")
    intent_publish_index = promote_source.find("publish_cleanup_intent_file")
    tombstone_mkdir_index = promote_source.find("tombstone_root.mkdir")
    projected_bound_index = promote_source.find("projected_cleanup_graph_records")
    first_move_index = promote_source.find("os.replace(source, destination)")
    if intent_publish_index < 0 or first_move_index < 0 or intent_publish_index > first_move_index:
        fail("cleanup tombstone promotion must publish durable intent before source moves")
    if tombstone_mkdir_index < 0 or tombstone_mkdir_index < intent_publish_index:
        fail("cleanup tombstone root must not be visible before durable intent")
    if not (tombstone_mkdir_index < projected_bound_index < first_move_index):
        fail("cleanup journal builder must preflight projected size after tombstone creation")
    if "allow_intent_residue=True" not in promote_source:
        fail("cleanup journal final validation must tolerate exact intent residue")
    launch_locked = functions.get("_launch_locked")
    if launch_locked is None:
        fail("manager is missing _launch_locked")
    launch_source = ast.get_source_segment(manager_source, launch_locked) or ""
    drain_index = launch_source.find("drain_cleanup_before_mutation")
    if drain_index < 0:
        fail("launch must drain cleanup-pending state under target lock")
    for later in (
        "require_active_clean_installed",
        "require_current_software",
        "clean_launch_env",
        "protect_launch_handoff_paths",
        "subprocess.Popen",
    ):
        later_index = launch_source.find(later)
        if later_index >= 0 and drain_index > later_index:
            fail("launch must drain cleanup-pending before launch image/protection/child work")


def validate_bootstrap_publication_eexist_preserves_destination() -> None:
    with tempfile.TemporaryDirectory(prefix="nddev-kilo-public-anchor-eexist-") as raw:
        root = Path(raw)
        root.chmod(nddev_kilo_cli.OWNER_DIR_MODE)
        final = root / "global.lock"
        original_content = b"preexisting anchor\n"
        final.write_bytes(original_content)
        final.chmod(nddev_kilo_cli.OWNER_FILE_MODE)
        before = final.lstat()
        published = nddev_kilo_cli.publish_bootstrap_anchor_file(
            root,
            final,
            b"replacement anchor\n",
            "public EEXIST anchor",
        )
        if published:
            fail("bootstrap anchor publication replaced a pre-existing destination")
        after = final.lstat()
        if (
            final.read_bytes() != original_content
            or stat.S_IMODE(after.st_mode) != stat.S_IMODE(before.st_mode)
            or nddev_kilo_cli.identity_of(after) != nddev_kilo_cli.identity_of(before)
            or after.st_nlink != before.st_nlink
        ):
            fail("EEXIST publication changed the pre-existing destination anchor")
        residue = sorted(path.name for path in root.iterdir() if path.name != final.name)
        if residue:
            fail("EEXIST publication left temporary anchor residue: " + ", ".join(residue))

    with tempfile.TemporaryDirectory(prefix="nddev-kilo-public-journal-eexist-") as raw:
        root = Path(raw)
        root.chmod(nddev_kilo_cli.OWNER_DIR_MODE)
        final = root / nddev_kilo_cli.CLEANUP_JOURNAL_NAME
        original_content = b"preexisting journal\n"
        final.write_bytes(original_content)
        final.chmod(nddev_kilo_cli.OWNER_FILE_MODE)
        before = final.lstat()
        expect_manager_error(
            "cleanup journal EEXIST publication",
            lambda: nddev_kilo_cli.publish_cleanup_journal_file(
                root,
                b"replacement journal\n",
            ),
        )
        after = final.lstat()
        if (
            final.read_bytes() != original_content
            or stat.S_IMODE(after.st_mode) != stat.S_IMODE(before.st_mode)
            or nddev_kilo_cli.identity_of(after) != nddev_kilo_cli.identity_of(before)
            or after.st_nlink != before.st_nlink
        ):
            fail("cleanup journal EEXIST publication changed the pre-existing final")
        residue = sorted(path.name for path in root.iterdir() if path.name != final.name)
        if residue:
            fail("cleanup journal EEXIST publication left temp residue: " + ", ".join(residue))

    with tempfile.TemporaryDirectory(prefix="nddev-kilo-public-intent-eexist-") as raw:
        root = Path(raw)
        root.chmod(nddev_kilo_cli.OWNER_DIR_MODE)
        final = root / nddev_kilo_cli.CLEANUP_INTENT_NAME
        original_content = b"preexisting intent\n"
        final.write_bytes(original_content)
        final.chmod(nddev_kilo_cli.OWNER_FILE_MODE)
        before = final.lstat()
        expect_manager_error(
            "cleanup intent EEXIST publication",
            lambda: nddev_kilo_cli.publish_cleanup_intent_file(
                root,
                b"replacement intent\n",
            ),
        )
        after = final.lstat()
        if (
            final.read_bytes() != original_content
            or stat.S_IMODE(after.st_mode) != stat.S_IMODE(before.st_mode)
            or nddev_kilo_cli.identity_of(after) != nddev_kilo_cli.identity_of(before)
            or after.st_nlink != before.st_nlink
        ):
            fail("cleanup intent EEXIST publication changed the pre-existing final")
        residue = sorted(path.name for path in root.iterdir() if path.name != final.name)
        if residue:
            fail("cleanup intent EEXIST publication left temp residue: " + ", ".join(residue))


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


def expect_manager_error(label: str, fn: Any) -> None:
    try:
        fn()
    except nddev_kilo_cli.ManagerError:
        return
    fail(f"{label} was accepted")


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


def validate_fake_path_is_ignored() -> None:
    if hasattr(nddev_kilo_cli, "find_npm_executable"):
        fail("public manager must not retain an npm executable selector")
    source = MANAGER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MANAGER_PATH))
    install_function = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "run_verified_archive_install"
        ),
        None,
    )
    if install_function is None:
        fail("public manager omits verified archive install function")
    called: set[str] = set()
    for node in ast.walk(install_function):
        if isinstance(node, ast.Call):
            function = node.func
            if isinstance(function, ast.Name):
                called.add(function.id)
            elif isinstance(function, ast.Attribute):
                called.add(function.attr)
    forbidden = {
        "bounded_process",
        "generate_package_lock",
        "install_stage_environment",
        "find_npm_executable",
        "Popen",
        "run",
    }
    if called & forbidden:
        fail("verified archive install calls forbidden npm/process materializer primitives")
    required = {
        "fetch_registry_metadata",
        "verify_registry_metadata",
        "verified_archive_bytes",
        "extract_verified_npm_archive",
        "write_verified_package_lock",
        "selected_native_package_name_for_host",
    }
    if not required.issubset(called):
        fail("verified archive install omits required archive authority checks")


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
        paths = (
            [root] if root.is_file() else sorted(path for path in root.rglob("*") if path.is_file())
        )
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


def run_all_validations() -> None:
    validate_versions()
    validate_contract()
    validate_baseline()
    validate_setups_and_profiles()
    validate_managed_source_exact_bytes()
    validate_builder_toolkit()
    validate_claude_bridge()
    validate_workflows()
    validate_release_archive_exact_bytes()
    validate_manager_parse_args()
    validate_python_portability()
    validate_bootstrap_publication_eexist_preserves_destination()
    validate_launch_guard()
    validate_public_bootstrap_override_denial()
    validate_fake_path_is_ignored()
    validate_no_public_bootstrap_override_references()


def main() -> int:
    try:
        run_all_validations()
    except ValidationError as exc:
        print(f"public contract validation failed: {exc}", file=sys.stderr)
        return 1
    print("public contract validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
