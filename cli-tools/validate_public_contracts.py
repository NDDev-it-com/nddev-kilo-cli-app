#!/usr/bin/env python3
"""Validate static public nddev-kilo-cli-app release artifacts."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = {
    "actionlint.yml",
    "codeql.yml",
    "dependency-review.yml",
    "release.yml",
    "scorecard.yml",
    "secret-scan.yml",
    "zizmor.yml",
}
REQUIRED_MANAGER_FUNCTIONS = {
    "parse_args",
    "run_verified_archive_install",
    "publish_bootstrap_anchor_file",
    "publish_cleanup_intent_file",
    "publish_cleanup_journal_file",
    "normalize_launch_child_args",
    "launch_command_for_profile",
    "launch_environment",
}
PRIVATE_PARTS = {"validation", ".agents", ".serena", "__pycache__", ".pytest_cache"}
SHARED_WORKFLOW_PIN = "2ccb80e96f5771b6a6b4eae63a4f47e232906dc7"
RELEASE_ARCHIVE_ROOTS = {
    ".claude", ".gds", ".github", "AGENTS.md", "CHANGELOG.md", "LICENSE",
    "README.md", "SECURITY.md", "VERSION", "build", "cli-tools", "config",
    "docs", "profiles", "references", "setups",
}
RELEASE_RUNTIME_ROOTS = {
    ".claude", "LICENSE", "README.md", "VERSION", "build", "cli-tools",
    "config", "docs", "profiles", "references", "setups",
}


class ValidationError(Exception):
    """Static public contract validation failure."""


def fail(message: str) -> None:
    raise ValidationError(message)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return argparse.ArgumentParser(description=__doc__).parse_args(argv)


def load_json(relative: str) -> dict[str, Any]:
    path = ROOT / relative

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                fail(f"{relative}: duplicate key {key}")
            result[key] = value
        return result

    with path.open(encoding="utf-8") as handle:
        value = json.load(handle, object_pairs_hook=reject_duplicates)
    if not isinstance(value, dict):
        fail(f"{relative} must contain a JSON object")
    return value


def require_file(relative: str) -> Path:
    path = ROOT / relative
    if not path.is_file() or path.is_symlink():
        fail(f"missing regular public file: {relative}")
    return path


def manager_constants() -> dict[str, Any]:
    source = require_file("cli-tools/nddev_kilo_cli.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    constants: dict[str, Any] = {}
    functions = {
        node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing = REQUIRED_MANAGER_FUNCTIONS - functions
    if missing:
        fail(f"manager functions are missing: {sorted(missing)}")
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            try:
                constants[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
    return constants


def validate_versions() -> None:
    version = require_file("VERSION").read_text(encoding="ascii").strip()
    build = load_json("build/version.json")
    manifest = load_json("build/manifest.json")
    contract = load_json("config/nddev-contract.json")
    baseline = load_json("references/kilo-cli-baseline.json")
    constants = manager_constants()
    if {version, build.get("build_version"), manifest.get("build_version")} != {version}:
        fail("public build versions are not synchronized")
    runtime = baseline.get("npm", {}).get("version")
    if {
        runtime,
        build.get("kilo_cli_current"),
        build.get("kilo_cli_min"),
        contract.get("runtime_compatibility", {}).get("current_version"),
        constants.get("KILO_CURRENT_VERSION"),
    } != {runtime}:
        fail("Kilo runtime versions are not synchronized")
    if build.get("kilo_cli_package_integrity") != constants.get("KILO_PACKAGE_INTEGRITY"):
        fail("Kilo package integrity drifted from manager")
    if build.get("kilo_cli_package_shasum") != constants.get("KILO_PACKAGE_SHASUM"):
        fail("Kilo package shasum drifted from manager")


def validate_native_setup() -> None:
    setup = load_json("setups/nddev-builder/setup.json")
    source_config = load_json("setups/nddev-builder/config.json")
    safe = load_json("profiles/safe/config.json")
    full_auto = load_json("profiles/full-auto/config.json")
    contract = load_json("config/nddev-contract.json")
    if setup.get("native_surfaces") != [
        "AGENTS.md",
        "agent",
        "skills.paths",
        "command files",
        "plugin",
        "permission",
        "sandbox",
    ]:
        fail("native setup surfaces mismatch")
    if source_config.get("plugin") != ["./nddev-builder-plugin.js"]:
        fail("builder must use the native local plugin spec")
    if source_config.get("default_agent") != "nddev-builder":
        fail("native default agent mismatch")
    if not source_config.get("skills", {}).get("paths"):
        fail("native skills.paths is missing")
    if safe.get("sandbox", {}).get("enabled") is not True:
        fail("safe profile must enable the native sandbox")
    if full_auto.get("permission") != "allow":
        fail("full-auto profile must use native allow permissions")
    builder = contract.get("builder", {})
    if builder.get("marketplace") is not None:
        fail("module must not synthesize a Kilo marketplace")
    plugin = builder.get("plugin", {})
    if plugin.get("config_spec") != "./nddev-builder-plugin.js":
        fail("public plugin contract is not the native local spec")
    for relative in setup.get("managed_files", []):
        if relative == "xdg-config/kilo/kilo.jsonc":
            continue
        require_file(f"setups/nddev-builder/{relative}")


def validate_archive_metadata() -> None:
    baseline = load_json("references/kilo-cli-baseline.json")
    npm = baseline.get("npm", {})
    dist = npm.get("dist", {})
    if not re.fullmatch(r"sha512-[A-Za-z0-9+/]+={0,2}", str(dist.get("integrity"))):
        fail("main npm integrity is invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", str(dist.get("shasum"))):
        fail("main npm shasum is invalid")
    native = npm.get("native_packages", {})
    catalog = native.get("catalog", {})
    production = native.get("production", {})
    if not isinstance(catalog, dict) or not isinstance(production, dict) or not production:
        fail("native package catalogs are incomplete")
    if not set(production).issubset(catalog):
        fail("production native packages must be drawn from the vendor catalog")
    for name, record in catalog.items():
        archive = record.get("dist", {}) if isinstance(record, dict) else {}
        if not re.fullmatch(r"sha512-[A-Za-z0-9+/]+={0,2}", str(archive.get("integrity"))):
            fail(f"{name}: native package integrity is invalid")
        if not re.fullmatch(r"[0-9a-f]{40}", str(archive.get("shasum"))):
            fail(f"{name}: native package shasum is invalid")


def validate_release_and_workflows() -> None:
    for workflow in WORKFLOWS:
        text = require_file(f".github/workflows/{workflow}").read_text(encoding="utf-8")
        if workflow != "release.yml" and SHARED_WORKFLOW_PIN not in text:
            fail(f"{workflow}: shared workflow pin mismatch")
    release = require_file(".github/workflows/release.yml").read_text(encoding="utf-8")
    for fragment in (
        "permissions: {}",
        'tags:\n      - "[0-9]+.[0-9]+.[0-9]+"',
        f"release-supply-chain.yml@{SHARED_WORKFLOW_PIN}",
        "version: ${{ github.ref_name }}",
        "package_name: nddev-kilo-cli-app",
        "archive_paths:",
        "runtime_paths:",
    ):
        if fragment not in release:
            fail(f"release workflow omits {fragment}")
    lines = release.splitlines()
    archive_index = next(i for i, line in enumerate(lines) if "archive_paths:" in line)
    runtime_index = next(i for i, line in enumerate(lines) if "runtime_paths:" in line)
    archive = set(" ".join(line.strip() for line in lines[archive_index + 1:runtime_index]).split())
    runtime = set(" ".join(line.strip() for line in lines[runtime_index + 1:] if line.startswith("        ")).split())
    if not RELEASE_ARCHIVE_ROOTS.issubset(archive):
        fail(f"release archive roots are incomplete: {sorted(RELEASE_ARCHIVE_ROOTS - archive)}")
    if not RELEASE_RUNTIME_ROOTS.issubset(runtime):
        fail(f"release runtime roots are incomplete: {sorted(RELEASE_RUNTIME_ROOTS - runtime)}")
    if not runtime.issubset(archive):
        fail("release runtime roots must be a subset of archive roots")
    for relative in archive:
        path = ROOT / relative
        if not path.exists() or path.is_symlink():
            fail(f"release root is missing or unsafe: {relative}")


def validate_runtime_integrity_sources() -> None:
    manager = require_file("cli-tools/nddev_kilo_cli.py").read_text(encoding="utf-8")
    forbidden = (
        "find_npm_executable",
        "generate_package_lock",
        "plugins/installed.json",
        "marketplace.json",
    )
    present = sorted(fragment for fragment in forbidden if fragment in manager)
    if present:
        fail(f"manager contains forbidden runtime-integrity fragments: {present}")
    required = (
        "fetch_registry_metadata",
        "verify_registry_metadata",
        "verified_archive_bytes",
        "extract_verified_npm_archive",
        "write_verified_package_lock",
        "selected_native_package_name_for_host",
        "O_NOFOLLOW",
        "cleanup_pending",
        "normalize_launch_child_args",
    )
    missing = sorted(fragment for fragment in required if fragment not in manager)
    if missing:
        fail(f"manager runtime-integrity fragments are missing: {missing}")


def validate_public_surface() -> None:
    bridge = require_file(".claude/CLAUDE.md")
    if bridge.read_bytes() != b"@../AGENTS.md\n":
        fail("Claude bridge must point to AGENTS.md")
    for path in ROOT.rglob("*"):
        if path.is_file() and PRIVATE_PARTS.intersection(path.relative_to(ROOT).parts):
            fail(f"private artifact is present in the public tree: {path}")


def validate_no_public_bootstrap_override_references() -> None:
    forbidden = {
        "NDDEV_KILO_BOOTSTRAP_LOCK_ROOT",
        "NDDEV_KILO_TEST_BOOTSTRAP_LOCK_ROOT",
        "KILO_BOOTSTRAP_LOCK_ROOT",
        "BOOTSTRAP_LOCK_ROOT_OVERRIDE",
    }
    for root in ("build", "config", "docs", "setups", "README.md"):
        path = ROOT / root
        paths = [path] if path.is_file() else [item for item in path.rglob("*") if item.is_file()]
        for item in paths:
            try:
                text = item.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            used = forbidden.intersection(text)
            if used:
                fail(f"{item.relative_to(ROOT)} documents unsupported overrides: {sorted(used)}")


def run_all_validations() -> None:
    validate_versions()
    validate_native_setup()
    validate_archive_metadata()
    validate_release_and_workflows()
    validate_runtime_integrity_sources()
    validate_public_surface()
    validate_no_public_bootstrap_override_references()


def main(argv: list[str] | None = None) -> int:
    parse_args(argv)
    try:
        run_all_validations()
    except (OSError, ValidationError, json.JSONDecodeError, SyntaxError) as exc:
        print(f"public contract validation failed: {exc}", file=sys.stderr)
        return 1
    print("public contract validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
