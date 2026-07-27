#!/usr/bin/env python3
"""Target-explicit setup manager for Kilo Code CLI.

The manager owns only the selected Kilo configuration keys, the native NDDev
builder projection, target-bound metadata, and target-bound backups under an
explicit absolute target. It never infers or mutates the caller's live
``~/.config/kilo`` state.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import errno
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows is intentionally unsupported.
    fcntl = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[1]
CATALOG_ROOT = ROOT / "setups"
PROFILE_ROOT = ROOT / "profiles"
BASELINE_PATH = ROOT / "references" / "kilo-cli-baseline.json"
VERSION = (ROOT / "VERSION").read_text(encoding="ascii").strip()
PRODUCT_NAME = "nddev-kilo-cli-app"
STAMP_NAME = "NDDEV-KILO-CLI-SETUP.json"
BACKUP_NAME = "NDDEV-KILO-CLI-BACKUP.json"
STAMP_SCHEMA = 2
LEGACY_STAMP_SCHEMA = 1
BACKUP_SCHEMA = 1
MAX_BACKUPS = 10
OWNER_FILE_MODE = 0o600
OWNER_DIR_MODE = 0o700
MANAGED_PAYLOAD_MAX_BYTES = 1024 * 1024
METADATA_MAX_BYTES = 256 * 1024
SOURCE_CONFIG = "config.json"
SOURCE_PROFILE_CONFIG = "config.json"
ACTIVE_SETUP_IDS = ("nddev-builder",)
DEFAULT_SETUP_ID = "nddev-builder"
PROFILE_IDS = ("full-auto", "safe")
DEFAULT_PROFILE_ID = "full-auto"
LEGACY_SETUP_IDS = ("safe", "balanced", "full-auto")
CONFIG = "xdg-config/kilo/kilo.jsonc"
AGENTS_FILE = "AGENTS.md"
BUILDER_INSTRUCTIONS = "instructions/nddev-builder.md"
BUILDER_SKILL = "skills/nddev-builder/SKILL.md"
BUILDER_SKILL_REFERENCES = (
    "skills/nddev-builder/references/config.md",
    "skills/nddev-builder/references/permissions-sandbox.md",
    "skills/nddev-builder/references/agents-subagents.md",
    "skills/nddev-builder/references/skills.md",
    "skills/nddev-builder/references/commands.md",
    "skills/nddev-builder/references/plugins-hooks.md",
    "skills/nddev-builder/references/mcp-boundary.md",
    "skills/nddev-builder/references/auth-boundary.md",
    "skills/nddev-builder/references/memory-context.md",
    "skills/nddev-builder/references/install-runtime.md",
    "skills/nddev-builder/references/migration-validation.md",
)
ADDITIONAL_SKILLS = (
    "skills/nddev-kilo-config/SKILL.md",
    "skills/nddev-kilo-permissions/SKILL.md",
    "skills/nddev-kilo-agents/SKILL.md",
    "skills/nddev-kilo-plugins/SKILL.md",
    "skills/nddev-kilo-runtime/SKILL.md",
)
AGENT_FILES = (
    "xdg-config/kilo/agent/nddev-builder.md",
    "xdg-config/kilo/agent/nddev-reviewer.md",
)
COMMAND_FILES = (
    "xdg-config/kilo/command/nddev-builder.md",
    "xdg-config/kilo/command/nddev-check.md",
    "xdg-config/kilo/command/nddev-migrate.md",
)
BUILDER_PLUGIN = "xdg-config/kilo/nddev-builder-plugin.js"
BUILDER_FILES = (
    AGENTS_FILE,
    BUILDER_INSTRUCTIONS,
    BUILDER_SKILL,
    *BUILDER_SKILL_REFERENCES,
    *ADDITIONAL_SKILLS,
    *AGENT_FILES,
    *COMMAND_FILES,
    BUILDER_PLUGIN,
)
MANAGED_FILES = (CONFIG, *BUILDER_FILES)
CONFIG_MANAGED_KEYS = (
    "permission",
    "sandbox",
    "default_agent",
    "agent",
    "skills",
    "command",
    "instructions",
    "plugin",
    "experimental",
)
BUILDER_PROJECTION = "native-agent-skill-command-plugin-config"
KILO_CURRENT_VERSION = "7.4.16"
KILO_PACKAGE = "@kilocode/cli"
KILO_COMMAND = "kilo"
KILO_PACKAGE_SPEC = f"{KILO_PACKAGE}@{KILO_CURRENT_VERSION}"
NPM_REGISTRY = "https://registry.npmjs.org/"
KILO_PACKAGE_METADATA_URL = "https://registry.npmjs.org/@kilocode%2fcli/7.4.16"
ALLOWED_SYSTEM_SYMLINK_ANCESTORS = {
    Path("/tmp"): Path("/private/tmp"),
    Path("/var"): Path("/private/var"),
}
KILO_PACKAGE_INTEGRITY = (
    "sha512-sOvq0HW6CZebCvGyUn0fTFj/1mX4nplpuoCJuqe6QjDUliLNYRC6ky9Rjl8kdT/"
    "nk0OGNO1kRaygEyc7+Htr2Q=="
)
KILO_PACKAGE_SHASUM = "c39f0f94f1cae2aeed28b4a5b5a952a5efab2b1d"
NPM_INSTALL_ARGV = (
    "install",
    "--global",
    "--ignore-scripts",
    "--save-exact",
    "--audit=false",
    "--fund=false",
    f"--registry={NPM_REGISTRY}",
    KILO_PACKAGE_SPEC,
)
NPM_LOCK_ARGV = (
    "install",
    "--package-lock-only",
    "--ignore-scripts",
    "--save-exact",
    "--audit=false",
    "--fund=false",
    f"--registry={NPM_REGISTRY}",
    KILO_PACKAGE_SPEC,
)
CONTROLLED_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
TRUSTED_NPM_CANDIDATES = (
    "/opt/homebrew/bin/npm",
    "/usr/local/bin/npm",
    "/usr/bin/npm",
)
PROCESS_OUTPUT_MAX_BYTES = 256 * 1024
PROCESS_TIMEOUT_SECONDS = 180
SOFTWARE_TREE_MAX_BYTES = 1024 * 1024 * 1024
SOFTWARE_TREE_MAX_PATHS = 120000
SOFTWARE_PREFIX_RELATIVE = Path("install") / "npm-prefix"
SOFTWARE_GLOBAL_DIR_RELATIVE = SOFTWARE_PREFIX_RELATIVE / "lib"
SOFTWARE_LOCK_RELATIVE = Path("software") / "package-lock.json"
BACKUP_POOL_RELATIVE = Path(".nddev-kilo-cli-backups")
LOCK_RELATIVE = Path(".nddev-kilo-cli.lock")
LOCK_FILE_NAME = "lock"
LOCK_OWNER_NAME = "owner.json"
LOCK_HELD_PARENT_MODE = 0o500
KILO_PACKAGE_BIN_RELATIVE = (
    SOFTWARE_GLOBAL_DIR_RELATIVE / "node_modules" / "@kilocode" / "cli" / "bin" / "kilo"
)
VENDOR_POSTINSTALL_BINARY_RELATIVE = KILO_PACKAGE_BIN_RELATIVE.parent / ".kilo"
VENDOR_POSTINSTALL_RESOURCE_RELATIVES = (
    VENDOR_POSTINSTALL_BINARY_RELATIVE,
    KILO_PACKAGE_BIN_RELATIVE.parent / "tree-sitter",
    KILO_PACKAGE_BIN_RELATIVE.parent / "console",
    KILO_PACKAGE_BIN_RELATIVE.parent / "bwrap",
    KILO_PACKAGE_BIN_RELATIVE.parent / "licenses",
    KILO_PACKAGE_BIN_RELATIVE.parent / "kilo-sandbox-mutation-worker.js",
)
NATIVE_TREE_SITTER_ENV = "KILO_TREE_SITTER_WASM_DIR"
SOFTWARE_MANIFEST_RELATIVE = Path("software") / "kilo-cli.json"
SOFTWARE_REPLACE_PATHS = (
    Path("bin") / KILO_COMMAND,
    SOFTWARE_PREFIX_RELATIVE,
    SOFTWARE_LOCK_RELATIVE,
    SOFTWARE_MANIFEST_RELATIVE,
)
SOFTWARE_PARENT_PATHS = tuple(
    sorted(
        {relative.parent for relative in SOFTWARE_REPLACE_PATHS if relative.parent != Path(".")},
        key=str,
    )
)
SECRET_ENV_PREFIXES = (
    "AWS_",
    "AMAZON_",
    "ANTHROPIC_",
    "AZURE_",
    "COHERE_",
    "GEMINI_",
    "GOOGLE_",
    "GROQ_",
    "KILOCODE_",
    "KILO_",
    "MISTRAL_",
    "OPENAI_",
    "OPENROUTER_",
    "PERPLEXITY_",
    "TOGETHER_",
    "VERTEX_",
    "XAI_",
    "npm_config_",
)
SECRET_ENV_NAMES = {
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    "AWS_SHARED_CREDENTIALS_FILE",
    "AWS_CONFIG_FILE",
    "ANTHROPIC_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "KILO_API_KEY",
    "KILO_CONFIG",
    "KILO_CONFIG_CONTENT",
    "KILO_ORG_ID",
    "KILO_PROVIDER",
    "KILOCODE_API_KEY",
    "NODE_AUTH_TOKEN",
    "NPM_TOKEN",
    "npm_config_userconfig",
    "NPM_CONFIG_USERCONFIG",
    "OPENAI_API_KEY",
}
FORBIDDEN_LAUNCH_ARGS = {
    "--agent",
    "--attach",
    "--auto",
    "--command",
    "--config",
    "--config-file",
    "--cwd",
    "--dangerously-skip-permissions",
    "--dir",
    "--directory",
    "--file",
    "--home",
    "--model",
    "--no-auto",
    "--no-dangerously-skip-permissions",
    "--no-sandbox",
    "--permission",
    "--permission-mode",
    "--permissions",
    "--project",
    "--root",
    "--sandbox",
    "--session",
    "--share",
    "--workdir",
    "--working-directory",
    "--workspace",
    "--workspace-root",
}
FORBIDDEN_LAUNCH_SHORT_ARGS = {"-c", "-f", "-m", "-p", "-s", "-u"}


class ManagerError(Exception):
    """A structured, user-facing lifecycle failure."""


class ConcurrentTargetChange(ManagerError):
    """A fail-closed target race or identity change."""


@dataclass(frozen=True)
class Setup:
    setup_id: str
    description: str
    builder_enabled: bool
    managed_files: tuple[str, ...]
    config: dict[str, Any]
    files: dict[str, bytes]


@dataclass(frozen=True)
class Profile:
    profile_id: str
    description: str
    launch_auto: bool
    config: dict[str, Any]


@dataclass
class DirectoryTransaction:
    created: list[Path]

    def cleanup(self) -> None:
        for path in reversed(self.created):
            with contextlib.suppress(OSError):
                path.rmdir()


def fail(message: str) -> NoReturn:
    raise ManagerError(message)


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def identity_of(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def owner_of(info: os.stat_result) -> int | None:
    return info.st_uid if hasattr(info, "st_uid") else None


def is_owner_private_directory(info: os.stat_result) -> bool:
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != OWNER_DIR_MODE:
        return False
    if hasattr(os, "geteuid") and owner_of(info) != os.geteuid():
        return False
    return True


def is_sticky_directory(info: os.stat_result) -> bool:
    return stat.S_ISDIR(info.st_mode) and bool(info.st_mode & stat.S_ISVTX)


def group_or_world_writable(info: os.stat_result) -> bool:
    return bool(stat.S_IMODE(info.st_mode) & 0o022)


def chmod_directory_no_follow(path: Path, mode: int, label: str) -> os.stat_result:
    if not hasattr(os, "O_NOFOLLOW"):
        fail(f"{label} mode changes require O_NOFOLLOW support: {path}")
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ManagerError(f"{label} must be a real directory: {path}") from exc
    try:
        opened = os.fstat(descriptor)
        if stat.S_ISLNK(opened.st_mode) or not stat.S_ISDIR(opened.st_mode):
            fail(f"{label} must be a real directory: {path}")
        os.fchmod(descriptor, mode)
        final = os.fstat(descriptor)
        if identity_of(opened) != identity_of(final):
            raise ConcurrentTargetChange(f"{label} changed while its mode was set: {path}")
        return final
    finally:
        os.close(descriptor)


def require_safe_target_ancestor(path: Path, label: str) -> None:
    info = require_directory(path, label)
    if group_or_world_writable(info) and not is_sticky_directory(info):
        fail(f"{label} must not be group/world-writable unless it is sticky")


def reject_unsafe_target_ancestors(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:-1]:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(info.st_mode):
            fail(f"target path must not contain symlink ancestors: {current}")
        if not stat.S_ISDIR(info.st_mode):
            fail(f"target parent must be a real directory: {current}")
        if group_or_world_writable(info) and not is_sticky_directory(info):
            fail(f"target ancestor must not be group/world-writable unless sticky: {current}")


def is_owner_only_file(info: os.stat_result) -> bool:
    if stat.S_IMODE(info.st_mode) != OWNER_FILE_MODE:
        return False
    if hasattr(os, "geteuid") and owner_of(info) != os.geteuid():
        return False
    return True


def safe_relative_path(relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        fail(f"managed path is not safe: {relative}")
    return path


def reject_relative_symlink_ancestors(root: Path, relative: str) -> None:
    current = root
    for part in safe_relative_path(relative).parts[:-1]:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            fail(f"managed parent must be a real directory: {current}")


def reject_absolute_symlink_ancestors(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(info.st_mode):
            allowed = ALLOWED_SYSTEM_SYMLINK_ANCESTORS.get(current)
            if allowed is not None:
                try:
                    if current.resolve(strict=True) == allowed:
                        continue
                except FileNotFoundError:
                    pass
            fail(f"target path must not contain symlink ancestors: {current}")
        if current != path and not stat.S_ISDIR(info.st_mode):
            fail(f"target parent must be a real directory: {current}")


def ensure_directory_chain(path: Path, transaction: DirectoryTransaction, label: str) -> None:
    missing: list[Path] = []
    current = path
    while True:
        try:
            info = current.lstat()
        except FileNotFoundError:
            missing.append(current)
            parent = current.parent
            if parent == current:
                fail(f"{label} parent is missing")
            current = parent
            continue
        if stat.S_ISLNK(info.st_mode):
            fail(f"{label} must not contain symlink ancestors: {current}")
        if not stat.S_ISDIR(info.st_mode):
            fail(f"{label} must be a real directory: {current}")
        break
    for directory in reversed(missing):
        directory.mkdir(mode=OWNER_DIR_MODE)
        directory.chmod(OWNER_DIR_MODE)
        transaction.created.append(directory)


def require_directory(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError:
        fail(f"{label} is missing")
    if stat.S_ISLNK(info.st_mode):
        fail(f"{label} must not be a symlink")
    if not stat.S_ISDIR(info.st_mode):
        fail(f"{label} must be a real directory")
    return info


def require_regular_file(
    path: Path,
    label: str,
    *,
    owner_only: bool = False,
    max_bytes: int | None = None,
) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError:
        fail(f"{label} is missing")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        fail(f"{label} must be a regular non-symlink file")
    if info.st_nlink != 1:
        fail(f"{label} must not have hard-link aliases")
    if owner_only and not is_owner_only_file(info):
        fail(f"{label} must be owned by the current user with mode 0600")
    if max_bytes is not None and info.st_size > max_bytes:
        fail(f"{label} exceeds the {max_bytes}-byte size limit")
    return info


def read_regular_file(
    path: Path,
    label: str,
    *,
    owner_only: bool = False,
    max_bytes: int = MANAGED_PAYLOAD_MAX_BYTES,
) -> bytes:
    before = require_regular_file(path, label, owner_only=owner_only, max_bytes=max_bytes)
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if identity_of(opened) != identity_of(before):
            raise ConcurrentTargetChange(f"{label} changed while it was opened")
        if owner_only and not is_owner_only_file(opened):
            fail(f"{label} must be owned by the current user with mode 0600")
        if opened.st_size > max_bytes:
            fail(f"{label} exceeds the {max_bytes}-byte size limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                fail(f"{label} exceeds the {max_bytes}-byte size limit")
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    final = require_regular_file(path, label, owner_only=owner_only, max_bytes=max_bytes)
    if identity_of(final) != identity_of(before) or identity_of(after) != identity_of(before):
        raise ConcurrentTargetChange(f"{label} changed while it was read")
    return b"".join(chunks)


def parse_json_object(content: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail(f"cannot read valid JSON from {label}: {exc}")
    if not isinstance(value, dict):
        fail(f"{label} must contain a JSON object")
    return value


def read_json_file(
    path: Path,
    label: str,
    *,
    owner_only: bool = False,
    max_bytes: int = METADATA_MAX_BYTES,
) -> dict[str, Any]:
    return parse_json_object(
        read_regular_file(path, label, owner_only=owner_only, max_bytes=max_bytes),
        label,
    )


def baseline() -> dict[str, Any]:
    value = read_json_file(BASELINE_PATH, "Kilo CLI baseline", max_bytes=METADATA_MAX_BYTES)
    if value.get("schema_version") != 2:
        fail("Kilo CLI baseline schema is unsupported")
    npm = value.get("npm")
    if not isinstance(npm, dict):
        fail("Kilo CLI baseline omits npm records")
    if npm.get("package") != KILO_PACKAGE or npm.get("version") != KILO_CURRENT_VERSION:
        fail("Kilo CLI baseline package identity is not synchronized")
    return value


def native_package_matrix() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    native = baseline().get("npm", {}).get("native_packages")
    if not isinstance(native, dict):
        fail("Kilo CLI baseline omits native package records")
    supported = native.get("supported")
    unsupported = native.get("unsupported")
    if not isinstance(supported, dict) or not isinstance(unsupported, dict):
        fail("Kilo CLI baseline native package records are invalid")
    return supported, unsupported


def package_name_from_lock_path(path: str) -> str | None:
    prefix = "node_modules/"
    if not path.startswith(prefix):
        return None
    return path[len(prefix) :]


def native_record_dist(record: dict[str, Any], package: str) -> dict[str, str]:
    dist = record.get("dist")
    if not isinstance(dist, dict):
        fail(f"Kilo CLI baseline native package omits dist: {package}")
    integrity = dist.get("integrity")
    shasum = dist.get("shasum")
    tarball = dist.get("tarball")
    if not all(isinstance(value, str) and value for value in (integrity, shasum, tarball)):
        fail(f"Kilo CLI baseline native package dist is incomplete: {package}")
    return {"integrity": integrity, "shasum": shasum, "tarball": tarball}


def normalize_os_name(value: str | None = None) -> str:
    raw = (value or platform.system()).lower()
    if raw == "darwin":
        return "darwin"
    if raw == "linux":
        return "linux"
    if raw in {"windows", "win32", "cygwin"}:
        return "win32"
    return raw


def normalize_arch(value: str | None = None) -> str:
    raw = (value or platform.machine()).lower()
    if raw in {"x86_64", "amd64"}:
        return "x64"
    if raw in {"aarch64", "arm64"}:
        return "arm64"
    return raw


def detect_linux_libc() -> str | None:
    if normalize_os_name() != "linux":
        return None
    if Path("/etc/alpine-release").exists():
        return "musl"
    libc_name, _libc_version = platform.libc_ver()
    if libc_name.lower() == "musl":
        return "musl"
    return None


def host_native_platform() -> dict[str, str | None]:
    return {
        "os": normalize_os_name(),
        "cpu": normalize_arch(),
        "libc": detect_linux_libc(),
    }


def native_record_matches_platform(record: dict[str, Any], host: dict[str, str | None]) -> bool:
    os_values = record.get("os")
    cpu_values = record.get("cpu")
    libc_values = record.get("libc")
    if os_values != [host["os"]] or cpu_values != [host["cpu"]]:
        return False
    if host["os"] == "linux":
        if host["libc"] == "musl":
            return libc_values == ["musl"]
        return libc_values is None
    return libc_values is None


def expected_native_records_for_host() -> dict[str, dict[str, Any]]:
    supported, _unsupported = native_package_matrix()
    host = host_native_platform()
    if host["os"] not in {"darwin", "linux"} or host["cpu"] not in {"x64", "arm64"}:
        fail(
            "Kilo CLI native package installation is supported only on macOS and Ubuntu-compatible Linux"
        )
    matches = {
        package: record
        for package, record in supported.items()
        if isinstance(record, dict) and native_record_matches_platform(record, host)
    }
    if not matches:
        fail(
            "Kilo CLI baseline has no supported native package for "
            f"{host['os']}/{host['cpu']}/{host['libc'] or 'glibc'}"
        )
    return matches


def native_package_preference_order(host: dict[str, str | None]) -> tuple[str, ...]:
    os_name = host["os"]
    cpu = host["cpu"]
    libc = host["libc"]
    if os_name == "darwin":
        if cpu == "arm64":
            return ("@kilocode/cli-darwin-arm64",)
        if cpu == "x64":
            return ("@kilocode/cli-darwin-x64-baseline", "@kilocode/cli-darwin-x64")
    if os_name == "linux":
        if cpu == "arm64":
            if libc == "musl":
                return ("@kilocode/cli-linux-arm64-musl", "@kilocode/cli-linux-arm64")
            return ("@kilocode/cli-linux-arm64",)
        if cpu == "x64":
            if libc == "musl":
                return (
                    "@kilocode/cli-linux-x64-baseline-musl",
                    "@kilocode/cli-linux-x64-musl",
                )
            return ("@kilocode/cli-linux-x64-baseline", "@kilocode/cli-linux-x64")
    fail(
        "Kilo CLI native package installation is supported only on macOS and Ubuntu-compatible Linux"
    )


def selected_native_package_name_for_host(host: dict[str, str | None]) -> str:
    supported, _unsupported = native_package_matrix()
    for package in native_package_preference_order(host):
        if package in supported:
            return package
    fail(
        "Kilo CLI baseline has no selected native package for "
        f"{host['os']}/{host['cpu']}/{host['libc'] or 'glibc'}"
    )


def native_package_binary_relative(package: str) -> Path:
    scope, name = package.split("/", 1)
    return SOFTWARE_GLOBAL_DIR_RELATIVE / "node_modules" / scope / name / "bin" / KILO_COMMAND


def setup_ids() -> list[str]:
    return [setup_id for setup_id in ACTIVE_SETUP_IDS if (CATALOG_ROOT / setup_id).is_dir()]


def profile_ids() -> list[str]:
    return [profile_id for profile_id in PROFILE_IDS if (PROFILE_ROOT / profile_id).is_dir()]


def legacy_setup_id(setup_id: str) -> bool:
    return setup_id in LEGACY_SETUP_IDS


def active_setup_id(setup_id: str) -> bool:
    return setup_id in setup_ids()


def require_active_setup_id(setup_id: str) -> None:
    if not active_setup_id(setup_id):
        fail(f"unknown active setup id: {setup_id}")


def require_profile_id(profile_id: str) -> None:
    if profile_id not in profile_ids():
        fail(f"unknown permission profile: {profile_id}")


def load_setup(setup_id: str) -> Setup:
    require_active_setup_id(setup_id)
    setup_root = CATALOG_ROOT / setup_id
    metadata = read_json_file(setup_root / "setup.json", f"setup {setup_id} metadata")
    if metadata.get("id") != setup_id:
        fail(f"setup metadata id mismatch for {setup_id}")
    managed_files = tuple(metadata.get("managed_files", []))
    if managed_files != MANAGED_FILES:
        fail(f"setup {setup_id} has an unexpected managed file list")
    config = read_json_file(setup_root / SOURCE_CONFIG, f"setup {setup_id} config")
    files = {CONFIG: canonical_json(config)}
    for relative in BUILDER_FILES:
        path = setup_root / safe_relative_path(relative)
        files[relative] = read_regular_file(path, f"setup {setup_id} {relative}")
    return Setup(
        setup_id=setup_id,
        description=str(metadata.get("description", "")),
        builder_enabled=bool(metadata.get("builder_enabled")),
        managed_files=managed_files,
        config=config,
        files=files,
    )


def load_profile(profile_id: str) -> Profile:
    require_profile_id(profile_id)
    profile_root = PROFILE_ROOT / profile_id
    metadata = read_json_file(profile_root / "profile.json", f"profile {profile_id} metadata")
    if metadata.get("id") != profile_id:
        fail(f"profile metadata id mismatch for {profile_id}")
    config = read_json_file(profile_root / SOURCE_PROFILE_CONFIG, f"profile {profile_id} config")
    return Profile(
        profile_id=profile_id,
        description=str(metadata.get("description", "")),
        launch_auto=bool(metadata.get("launch_auto")),
        config=config,
    )


def resolve_target(raw: str | Path, *, create: bool = False) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        fail("target must be an absolute path")
    try:
        original_info = path.lstat()
    except FileNotFoundError:
        original_info = None
    if original_info is not None and stat.S_ISLNK(original_info.st_mode):
        fail("target must not be a symlink")
    reject_absolute_symlink_ancestors(path)
    path = path.resolve(strict=False)
    reject_absolute_symlink_ancestors(path)
    reject_unsafe_target_ancestors(path)
    if create:
        transaction = DirectoryTransaction([])
        try:
            ensure_directory_chain(path.parent, transaction, "target parent")
            require_safe_target_ancestor(path.parent, "target parent")
            ensure_private_directory(path, create=True, transaction=transaction)
        except BaseException:
            transaction.cleanup()
            raise
    else:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return path
        if stat.S_ISLNK(info.st_mode):
            fail("target must not be a symlink")
        if not stat.S_ISDIR(info.st_mode):
            fail("target must be a real directory")
        if not is_owner_private_directory(info):
            fail("target must be private to the current user with mode 0700")
    require_directory(path, "target")
    return path


def ensure_private_directory(
    path: Path,
    *,
    create: bool,
    transaction: DirectoryTransaction | None = None,
) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            return False
        require_directory(path.parent, f"{path} parent")
        path.mkdir(mode=OWNER_DIR_MODE)
        path.chmod(OWNER_DIR_MODE)
        if transaction is not None:
            transaction.created.append(path)
        return True
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        fail(f"{path} must be a real directory")
    if not is_owner_private_directory(info):
        fail(f"{path} must be private to the current user with mode 0700")
    return True


def require_private_target_directory_for_software(target: Path, *, allow_missing: bool) -> bool:
    try:
        info = target.lstat()
    except FileNotFoundError:
        if allow_missing:
            return False
        fail("software target is missing")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        fail("software target must be a real directory")
    if not is_owner_private_directory(info):
        fail("software target must be private to the current user with mode 0700")
    return True


def backup_pool(target: Path) -> Path:
    return target / BACKUP_POOL_RELATIVE


def legacy_backup_pool(target: Path) -> Path:
    return target.parent / f".{target.name}.nddev-kilo-cli-backups"


def backup_slot_directory(target: Path, slot: int) -> Path:
    for pool in (backup_pool(target), legacy_backup_pool(target)):
        slot_dir = pool / str(slot)
        if not path_exists_no_follow(slot_dir):
            continue
        pool_info = require_directory(pool, "backup pool")
        if not is_owner_private_directory(pool_info):
            fail("backup pool must be private to the current user with mode 0700")
        slot_info = require_directory(slot_dir, f"backup slot {slot}")
        if not is_owner_private_directory(slot_info):
            fail(f"backup slot {slot} must be private to the current user with mode 0700")
        return slot_dir
    fail(f"backup slot is missing: {slot}")


def lock_path(target: Path) -> Path:
    return target / LOCK_RELATIVE


def lock_file_path(target: Path) -> Path:
    return lock_path(target) / LOCK_FILE_NAME


def lock_owner_path(target: Path) -> Path:
    return lock_path(target) / LOCK_OWNER_NAME


def restore_stale_launch_protection_modes(target: Path) -> None:
    candidates = {target / relative for relative in SOFTWARE_PARENT_PATHS}
    install_root = target / SOFTWARE_PREFIX_RELATIVE
    if path_exists_no_follow(install_root):
        candidates.add(install_root)
        for path in install_root.rglob("*"):
            if len(candidates) > SOFTWARE_TREE_MAX_PATHS:
                fail("stale launch protection recovery exceeded the software path limit")
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
                candidates.add(path)
    for path in sorted(candidates, key=lambda item: len(item.parts)):
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            continue
        if not stat.S_ISDIR(info.st_mode):
            continue
        if stat.S_IMODE(info.st_mode) != LOCK_HELD_PARENT_MODE:
            continue
        if hasattr(os, "geteuid") and owner_of(info) != os.geteuid():
            fail(f"stale launch-protected directory must be owned by the current user: {path}")
        chmod_directory_no_follow(path, OWNER_DIR_MODE, "stale launch-protected directory")


def require_lock_directory(lock: Path) -> os.stat_result:
    info = require_directory(lock, "target lock parent")
    mode = stat.S_IMODE(info.st_mode)
    if mode not in {OWNER_DIR_MODE, LOCK_HELD_PARENT_MODE}:
        fail("target lock parent must be private to the current user with mode 0700 or 0500")
    if hasattr(os, "geteuid") and owner_of(info) != os.geteuid():
        fail("target lock parent must be owned by the current user")
    return info


def ensure_lock_directory(target: Path) -> None:
    lock = lock_path(target)
    try:
        info = lock.lstat()
    except FileNotFoundError:
        lock.mkdir(mode=OWNER_DIR_MODE)
        final = chmod_directory_no_follow(lock, OWNER_DIR_MODE, "target lock parent")
        if not is_owner_private_directory(final):
            fail("target lock parent must be private to the current user with mode 0700")
        return
    if stat.S_ISLNK(info.st_mode):
        fail("target lock parent must not be a symlink")
    if not stat.S_ISDIR(info.st_mode):
        fail("target lock parent must be a real directory")
    require_lock_directory(lock)


def ensure_lock_file(target: Path) -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        fail("target lifecycle lock file requires O_NOFOLLOW support")
    lock = lock_path(target)
    lock_file = lock_file_path(target)
    ensure_lock_directory(target)
    try:
        info = lock_file.lstat()
    except FileNotFoundError:
        lock_info = require_lock_directory(lock)
        if stat.S_IMODE(lock_info.st_mode) != OWNER_DIR_MODE:
            chmod_directory_no_follow(lock, OWNER_DIR_MODE, "target lock parent")
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        flags |= os.O_NOFOLLOW
        descriptor = os.open(lock_file, flags, OWNER_FILE_MODE)
        os.fchmod(descriptor, OWNER_FILE_MODE)
        return descriptor
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        fail("target lock file must be a regular file")
    if info.st_nlink != 1:
        fail("target lock file must not have hard-link aliases")
    if not is_owner_only_file(info):
        fail("target lock file must be owned by the current user with mode 0600")
    flags = os.O_RDWR
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_file, flags)
    opened = os.fstat(descriptor)
    if identity_of(opened) != identity_of(info):
        os.close(descriptor)
        raise ConcurrentTargetChange("target lock file changed while it was opened")
    return descriptor


def write_lock_owner(target: Path, *, held: bool) -> None:
    lock = lock_path(target)
    owner = lock_owner_path(target)
    lock_info = require_lock_directory(lock)
    if stat.S_IMODE(lock_info.st_mode) != OWNER_DIR_MODE:
        chmod_directory_no_follow(lock, OWNER_DIR_MODE, "target lock parent")
    atomic_write(
        target,
        str(owner.relative_to(target)),
        canonical_json(
            {
                "schema_version": 2,
                "pid": os.getpid(),
                "target": str(target),
                "lock_file": str(lock_file_path(target).relative_to(target)),
                "held": held,
            }
        ),
    )


def cleanup_lock_artifacts_for_created_target(target: Path, transaction: DirectoryTransaction) -> None:
    if target not in transaction.created:
        return
    lock = lock_path(target)
    try:
        info = lock.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return
    if hasattr(os, "geteuid") and owner_of(info) != os.geteuid():
        return
    with contextlib.suppress(OSError):
        chmod_directory_no_follow(lock, OWNER_DIR_MODE, "created target lock parent")
    for path in (lock_owner_path(target), lock_file_path(target)):
        try:
            child_info = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(child_info.st_mode) or not stat.S_ISREG(child_info.st_mode):
            continue
        if hasattr(os, "geteuid") and owner_of(child_info) != os.geteuid():
            continue
        with contextlib.suppress(OSError):
            path.unlink()
    with contextlib.suppress(OSError):
        lock.rmdir()


@contextlib.contextmanager
def target_lock(target: Path, *, create_parent: bool = False) -> Iterator[DirectoryTransaction]:
    if fcntl is None:
        fail("target lifecycle locks require fcntl.flock on this platform")
    transaction = DirectoryTransaction([])
    if create_parent:
        ensure_directory_chain(target.parent, transaction, "canonical target parent")
        require_safe_target_ancestor(target.parent, "canonical target parent")
        ensure_private_directory(target, create=True, transaction=transaction)
    else:
        if not ensure_private_directory(target, create=False, transaction=transaction):
            fail("target is missing")
    lock = lock_path(target)
    descriptor = -1
    acquired = False
    try:
        descriptor = ensure_lock_file(target)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
            os.close(descriptor)
            descriptor = -1
            fail(f"target is locked: {lock_file_path(target)}")
        acquired = True
        lock_file_info = os.fstat(descriptor)
        current_lock_file = lock_file_path(target).lstat()
        if identity_of(lock_file_info) != identity_of(current_lock_file):
            raise ConcurrentTargetChange("target lock file changed after it was locked")
        write_lock_owner(target, held=True)
        restore_stale_launch_protection_modes(target)
        chmod_directory_no_follow(lock, LOCK_HELD_PARENT_MODE, "target lock parent")
    except BaseException:
        cleanup_lock_artifacts_for_created_target(target, transaction)
        transaction.cleanup()
        if descriptor >= 0:
            if acquired:
                with contextlib.suppress(OSError):
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
        raise
    failed = False
    try:
        yield transaction
    except BaseException:
        failed = True
        raise
    finally:
        try:
            if acquired and descriptor >= 0:
                chmod_directory_no_follow(lock, OWNER_DIR_MODE, "target lock parent")
                write_lock_owner(target, held=False)
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError as exc:
            raise ManagerError(f"target lock cleanup failed: {lock}") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if failed:
            cleanup_lock_artifacts_for_created_target(target, transaction)
            transaction.cleanup()


def stamp_path(target: Path) -> Path:
    return target / STAMP_NAME


def current_config(target: Path) -> dict[str, Any]:
    path = target / CONFIG
    try:
        path.lstat()
    except FileNotFoundError:
        return {}
    reject_relative_symlink_ancestors(target, CONFIG)
    return read_json_file(path, "target config", max_bytes=MANAGED_PAYLOAD_MAX_BYTES)


def extract_managed_config(config: dict[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(config[key]) for key in CONFIG_MANAGED_KEYS if key in config}


def merge_config(existing: dict[str, Any], managed: dict[str, Any]) -> dict[str, Any]:
    merged = {
        key: copy.deepcopy(value)
        for key, value in existing.items()
        if key not in CONFIG_MANAGED_KEYS
    }
    for key, value in managed.items():
        merged[key] = copy.deepcopy(value)
    return merged


def managed_config_fragment(setup: Setup, profile: Profile, target: Path) -> dict[str, Any]:
    fragment = extract_managed_config(setup.config)
    for key, value in extract_managed_config(profile.config).items():
        fragment[key] = copy.deepcopy(value)
    skills = copy.deepcopy(fragment.get("skills", {}))
    skills["paths"] = [str((target / "skills").resolve(strict=False))]
    fragment["skills"] = skills
    fragment["instructions"] = [str((target / BUILDER_INSTRUCTIONS).resolve(strict=False))]
    fragment["plugin"] = ["./nddev-builder-plugin.js"]
    return fragment


def digest_for_content(relative: str, content: bytes) -> str:
    if relative == CONFIG:
        config = parse_json_object(content, "managed config")
        return sha256_bytes(canonical_json(extract_managed_config(config)))
    return sha256_bytes(content)


def stamp_for_desired(
    target: Path,
    setup_id: str,
    profile_id: str,
    desired: dict[str, bytes | None],
) -> dict[str, Any]:
    managed_records = []
    for relative in sorted(relative for relative in desired if relative != STAMP_NAME):
        content = desired.get(relative)
        if content is None:
            fail(f"desired state omits managed file: {relative}")
        managed_records.append({"path": relative, "sha256": digest_for_content(relative, content)})
    return {
        "schema_version": STAMP_SCHEMA,
        "product_name": PRODUCT_NAME,
        "build_version": VERSION,
        "setup_id": setup_id,
        "permission_profile": profile_id,
        "canonical_target": str(target),
        "managed_files": managed_records,
        "builder": {
            "enabled": True,
            "projection": BUILDER_PROJECTION,
            "agent": "nddev-builder",
            "skill": "nddev-builder",
            "plugin": "nddev-builder",
            "files": list(BUILDER_FILES),
        },
        "runtime": {
            "command": "kilo",
            "config_env": "KILO_CONFIG",
            "package": KILO_PACKAGE,
            "version": KILO_CURRENT_VERSION,
        },
    }


def desired_for_setup(target: Path, setup_id: str, profile_id: str) -> dict[str, bytes | None]:
    setup = load_setup(setup_id)
    profile = load_profile(profile_id)
    managed = managed_config_fragment(setup, profile, target)
    merged = merge_config(current_config(target), managed)
    desired: dict[str, bytes | None] = {CONFIG: canonical_json(merged)}
    for relative in BUILDER_FILES:
        desired[relative] = setup.files[relative]
    desired[STAMP_NAME] = canonical_json(
        stamp_for_desired(target, setup_id, profile_id, desired)
    )
    return desired


def read_stamp(target: Path) -> dict[str, Any] | None:
    path = stamp_path(target)
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    reject_relative_symlink_ancestors(target, STAMP_NAME)
    stamp = read_json_file(path, "target stamp", owner_only=True)
    if stamp.get("schema_version") not in {STAMP_SCHEMA, LEGACY_STAMP_SCHEMA}:
        fail("target stamp schema is unsupported")
    if stamp.get("product_name") != PRODUCT_NAME:
        fail("target stamp belongs to another product")
    if stamp.get("canonical_target") != str(target):
        fail("target stamp is bound to a different target")
    if not isinstance(stamp.get("managed_files"), list):
        fail("target stamp has invalid managed file records")
    return stamp


def stamp_setup_id(stamp: dict[str, Any]) -> str:
    return str(stamp.get("setup_id", ""))


def stamp_profile_id(stamp: dict[str, Any]) -> str | None:
    profile = stamp.get("permission_profile")
    return str(profile) if isinstance(profile, str) and profile else None


def stamp_is_active(stamp: dict[str, Any]) -> bool:
    return stamp.get("schema_version") == STAMP_SCHEMA and active_setup_id(stamp_setup_id(stamp))


def stamp_is_legacy(stamp: dict[str, Any]) -> bool:
    setup_id = stamp_setup_id(stamp)
    return stamp.get("schema_version") == LEGACY_STAMP_SCHEMA or legacy_setup_id(setup_id)


def require_active_clean_installed(target: Path) -> dict[str, Any]:
    stamp = require_clean_installed(target)
    if not stamp_is_active(stamp):
        fail("managed Kilo setup is legacy and must be migrated or removed before launch")
    profile_id = stamp_profile_id(stamp)
    if profile_id not in profile_ids():
        fail("managed Kilo setup has an unsupported permission profile")
    return stamp


def current_digest(target: Path, relative: str) -> str | None:
    reject_relative_symlink_ancestors(target, relative)
    path = target / safe_relative_path(relative)
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    content = read_regular_file(
        path,
        f"managed file {relative}",
        owner_only=True,
        max_bytes=MANAGED_PAYLOAD_MAX_BYTES,
    )
    return digest_for_content(relative, content)


def drift_paths(target: Path, stamp: dict[str, Any]) -> list[str]:
    drift = []
    for record in stamp["managed_files"]:
        if not isinstance(record, dict):
            fail("target stamp has invalid managed file record")
        relative = str(record.get("path", ""))
        expected = str(record.get("sha256", ""))
        if relative not in MANAGED_FILES:
            fail(f"target stamp contains unknown managed path: {relative}")
        if current_digest(target, relative) != expected:
            drift.append(relative)
    return drift


def require_clean_installed(target: Path) -> dict[str, Any]:
    stamp = read_stamp(target)
    if stamp is None:
        fail("no managed Kilo setup is installed at target")
    drift = drift_paths(target, stamp)
    if drift:
        fail("managed state drift detected: " + ", ".join(drift))
    return stamp


def preflight_unmanaged_target(target: Path) -> None:
    if read_stamp(target) is not None:
        return
    for relative in BUILDER_FILES:
        path = target / safe_relative_path(relative)
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        fail(f"unmanaged target already contains managed path: {relative}")
    config = current_config(target)
    conflicts = sorted(key for key in CONFIG_MANAGED_KEYS if key in config)
    if conflicts:
        fail("unmanaged target already contains managed Kilo config keys: " + ", ".join(conflicts))


def cleanup_empty_parents(target: Path, relative: str) -> None:
    current = (target / safe_relative_path(relative)).parent
    while current != target and target in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def ensure_parent(target: Path, relative: str) -> Path:
    reject_relative_symlink_ancestors(target, relative)
    relative_path = safe_relative_path(relative)
    path = target / relative_path
    parent_relative = relative_path.parent
    if parent_relative == Path("."):
        target_info = require_directory(target, "target")
        if not is_owner_private_directory(target_info):
            fail("target must be private to the current user with mode 0700")
    else:
        ensure_target_private_subdirectory(
            target,
            parent_relative,
            f"managed parent {path.parent}",
        )
    reject_relative_symlink_ancestors(target, relative)
    return path


def preflight_destination(target: Path, relative: str) -> None:
    reject_relative_symlink_ancestors(target, relative)
    path = target / safe_relative_path(relative)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        fail(f"managed file {relative} must be a regular non-symlink file")
    if info.st_nlink != 1:
        fail(f"managed file {relative} must not have hard-link aliases")


def atomic_write(target: Path, relative: str, content: bytes) -> None:
    path = ensure_parent(target, relative)
    parent_info = require_directory(path.parent, f"managed parent {path.parent}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, OWNER_FILE_MODE)
        if identity_of(
            require_directory(path.parent, f"managed parent {path.parent}")
        ) != identity_of(parent_info):
            raise ConcurrentTargetChange(f"managed parent changed while writing {relative}")
        os.replace(temporary, path)
        os.chmod(path, OWNER_FILE_MODE)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def replace_managed_state(
    target: Path,
    desired: dict[str, bytes | None],
    expected: dict[str, str] | None = None,
    **_kwargs: Any,
) -> None:
    require_directory(target, "target")
    if expected is not None:
        for relative, expected_digest in expected.items():
            content = snapshot_paths(target, {relative})[relative]
            actual = "<missing>" if content is None else digest_for_content(relative, content)
            if actual != expected_digest:
                raise ConcurrentTargetChange(f"managed path changed before replacement: {relative}")
    for relative in desired:
        preflight_destination(target, relative)
    ordered = [relative for relative in desired if relative != STAMP_NAME]
    if STAMP_NAME in desired:
        ordered.append(STAMP_NAME)
    for relative in ordered:
        content = desired[relative]
        path = target / safe_relative_path(relative)
        if content is None:
            preflight_destination(target, relative)
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            cleanup_empty_parents(target, relative)
        else:
            preflight_destination(target, relative)
            atomic_write(target, relative, content)


def snapshot_paths(target: Path, relatives: set[str]) -> dict[str, bytes | None]:
    snapshot: dict[str, bytes | None] = {}
    for relative in sorted(relatives):
        reject_relative_symlink_ancestors(target, relative)
        path = target / safe_relative_path(relative)
        try:
            path.lstat()
        except FileNotFoundError:
            snapshot[relative] = None
            continue
        snapshot[relative] = read_regular_file(
            path, f"snapshot {relative}", max_bytes=MANAGED_PAYLOAD_MAX_BYTES
        )
    return snapshot


def snapshot_digests(snapshot: dict[str, bytes | None]) -> dict[str, str]:
    return {
        relative: "<missing>" if content is None else digest_for_content(relative, content)
        for relative, content in snapshot.items()
    }


def restore_snapshot(target: Path, snapshot: dict[str, bytes | None]) -> None:
    for relative, content in snapshot.items():
        path = target / safe_relative_path(relative)
        if content is None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            cleanup_empty_parents(target, relative)
        else:
            atomic_write(target, relative, content)


def choose_backup_slot(pool: Path) -> int:
    for slot in range(MAX_BACKUPS):
        if not (pool / str(slot)).exists():
            return slot
    oldest_slot = min(
        range(MAX_BACKUPS),
        key=lambda slot: (pool / str(slot)).stat().st_mtime_ns,
    )
    return oldest_slot


def write_backup_file(slot_dir: Path, relative: str, content: bytes) -> None:
    path = slot_dir / safe_relative_path(relative)
    path.parent.mkdir(mode=OWNER_DIR_MODE, parents=True, exist_ok=True)
    path.write_bytes(content)
    os.chmod(path, OWNER_FILE_MODE)


def backup_current_state(target: Path, stamp: dict[str, Any]) -> int:
    pool = backup_pool(target)
    if path_exists_no_follow(pool):
        pool_info = require_directory(pool, "backup pool")
        if not is_owner_private_directory(pool_info):
            fail("backup pool must be private to the current user with mode 0700")
    else:
        pool.mkdir(mode=OWNER_DIR_MODE)
        pool.chmod(OWNER_DIR_MODE)
    pool_info = require_directory(pool, "backup pool")
    if not is_owner_private_directory(pool_info):
        fail("backup pool must be private to the current user with mode 0700")
    slot = choose_backup_slot(pool)
    slot_dir = pool / str(slot)
    if slot_dir.exists():
        info = slot_dir.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            fail(f"backup slot is not a real directory: {slot}")
        shutil.rmtree(slot_dir)
    temporary = pool / f".tmp-{slot}-{os.getpid()}"
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(mode=OWNER_DIR_MODE)
    stamp_content = read_regular_file(stamp_path(target), "target stamp")
    records = stamp["managed_files"]
    for record in records:
        relative = str(record["path"])
        content = read_regular_file(
            target / safe_relative_path(relative),
            f"backup source {relative}",
            max_bytes=MANAGED_PAYLOAD_MAX_BYTES,
        )
        write_backup_file(temporary, relative, content)
    envelope = {
        "schema_version": BACKUP_SCHEMA,
        "product_name": PRODUCT_NAME,
        "build_version": VERSION,
        "slot": slot,
        "canonical_target": str(target),
        "source_setup_id": stamp["setup_id"],
        "managed_files": records,
        "stamp_sha256": sha256_bytes(stamp_content),
    }
    write_backup_file(temporary, BACKUP_NAME, canonical_json(envelope))
    os.replace(temporary, slot_dir)
    return slot


def load_backup(target: Path, slot: int) -> dict[str, Any]:
    if slot < 0 or slot >= MAX_BACKUPS:
        fail(f"backup slot must be between 0 and {MAX_BACKUPS - 1}")
    slot_dir = backup_slot_directory(target, slot)
    envelope = read_json_file(slot_dir / BACKUP_NAME, f"backup {slot} envelope")
    if envelope.get("schema_version") != BACKUP_SCHEMA:
        fail("backup schema is unsupported")
    if envelope.get("product_name") != PRODUCT_NAME:
        fail("backup belongs to another product")
    if envelope.get("canonical_target") != str(target):
        fail("backup is bound to a different target")
    return envelope


def desired_for_backup(target: Path, slot: int) -> tuple[str, dict[str, bytes | None]]:
    envelope = load_backup(target, slot)
    slot_dir = backup_slot_directory(target, slot)
    setup_id = str(envelope["source_setup_id"])
    if not active_setup_id(setup_id) and not legacy_setup_id(setup_id):
        fail(f"backup references unknown setup id: {setup_id}")
    desired: dict[str, bytes | None] = {}
    for record in envelope["managed_files"]:
        relative = str(record["path"])
        content = read_regular_file(
            slot_dir / safe_relative_path(relative),
            f"backup {slot} {relative}",
            max_bytes=MANAGED_PAYLOAD_MAX_BYTES,
        )
        if digest_for_content(relative, content) != record["sha256"]:
            fail(f"backup {slot} {relative} digest mismatch")
        if relative == CONFIG:
            backup_config = parse_json_object(content, f"backup {slot} config")
            managed = extract_managed_config(backup_config)
            desired[CONFIG] = canonical_json(merge_config(current_config(target), managed))
        else:
            desired[relative] = content
    profile_id = DEFAULT_PROFILE_ID
    if active_setup_id(setup_id):
        current_stamp = read_stamp(target)
        profile_id = stamp_profile_id(current_stamp or {}) or DEFAULT_PROFILE_ID
        desired[STAMP_NAME] = canonical_json(
            stamp_for_desired(target, setup_id, profile_id, desired)
        )
    else:
        legacy_stamp = {
            "schema_version": LEGACY_STAMP_SCHEMA,
            "product_name": PRODUCT_NAME,
            "build_version": VERSION,
            "setup_id": setup_id,
            "canonical_target": str(target),
            "managed_files": envelope["managed_files"],
            "legacy": True,
            "launchable": False,
            "runtime": {
                "command": "kilo",
                "config_env": "KILO_CONFIG",
                "package": KILO_PACKAGE,
                "version": KILO_CURRENT_VERSION,
            },
        }
        desired[STAMP_NAME] = canonical_json(legacy_stamp)
    return setup_id, desired


def desired_for_remove(target: Path) -> dict[str, bytes | None]:
    config = current_config(target)
    unmanaged = {
        key: copy.deepcopy(value) for key, value in config.items() if key not in CONFIG_MANAGED_KEYS
    }
    desired: dict[str, bytes | None] = {relative: None for relative in MANAGED_FILES}
    desired[CONFIG] = canonical_json(unmanaged) if unmanaged else None
    desired[STAMP_NAME] = None
    return desired


def mutate_setup(target: Path, setup_id: str, profile_id: str, operation: str) -> dict[str, Any]:
    require_active_setup_id(setup_id)
    require_profile_id(profile_id)
    target = resolve_target(target, create=False)
    backup_slot: int | None = None
    with target_lock(target, create_parent=(operation == "install")) as transaction:
        ensure_private_directory(target, create=(operation == "install"), transaction=transaction)
        if operation == "install":
            stamp = read_stamp(target)
            if stamp is None:
                preflight_unmanaged_target(target)
            else:
                require_clean_installed(target)
                if not stamp_is_active(stamp):
                    fail("legacy managed state must be migrated or removed before install")
                if stamp_setup_id(stamp) != setup_id or stamp_profile_id(stamp) != profile_id:
                    backup_slot = backup_current_state(target, stamp)
        elif operation == "switch":
            stamp = require_clean_installed(target)
            if not stamp_is_active(stamp):
                fail("legacy managed state must be migrated or removed before switch")
            if stamp_setup_id(stamp) != setup_id or stamp_profile_id(stamp) != profile_id:
                backup_slot = backup_current_state(target, stamp)
        else:
            fail(f"unsupported mutation operation: {operation}")
        desired = desired_for_setup(target, setup_id, profile_id)
        snapshot = snapshot_paths(target, set(desired))
        try:
            replace_managed_state(target, desired, snapshot_digests(snapshot))
        except Exception:
            restore_snapshot(target, snapshot)
            raise
    return {
        "operation": operation,
        "setup_id": setup_id,
        "permission_profile": profile_id,
        "target": str(target),
        "backup_slot": backup_slot,
        "builder": {"enabled": True, "projection": BUILDER_PROJECTION},
    }


def infer_legacy_profile(stamp: dict[str, Any], requested_profile: str | None) -> str:
    if requested_profile is not None:
        require_profile_id(requested_profile)
        return requested_profile
    setup_id = stamp_setup_id(stamp)
    if setup_id in {"safe", "full-auto"}:
        return setup_id
    fail("legacy balanced state has no native profile mapping; pass --profile safe or --profile full-auto")


def migrate_setup(target: Path, profile_id: str | None) -> dict[str, Any]:
    target = resolve_target(target, create=False)
    backup_slot: int | None = None
    with target_lock(target):
        stamp = require_clean_installed(target)
        if stamp_is_active(stamp):
            profile = profile_id or stamp_profile_id(stamp) or DEFAULT_PROFILE_ID
        elif stamp_is_legacy(stamp):
            profile = infer_legacy_profile(stamp, profile_id)
            backup_slot = backup_current_state(target, stamp)
        else:
            fail("managed Kilo setup is not migratable")
        desired = desired_for_setup(target, DEFAULT_SETUP_ID, profile)
        snapshot = snapshot_paths(target, set(desired))
        try:
            replace_managed_state(target, desired, snapshot_digests(snapshot))
        except Exception:
            restore_snapshot(target, snapshot)
            raise
    return {
        "operation": "migrate",
        "setup_id": DEFAULT_SETUP_ID,
        "permission_profile": profile,
        "target": str(target),
        "backup_slot": backup_slot,
        "builder": {"enabled": True, "projection": BUILDER_PROJECTION},
    }


def restore_setup(target: Path, slot: int) -> dict[str, Any]:
    target = resolve_target(target, create=False)
    with target_lock(target):
        require_private_target_directory_for_software(target, allow_missing=False)
        setup_id, desired = desired_for_backup(target, slot)
        snapshot = snapshot_paths(target, set(desired))
        try:
            replace_managed_state(target, desired, snapshot_digests(snapshot))
        except Exception:
            restore_snapshot(target, snapshot)
            raise
    return {
        "operation": "restore",
        "setup_id": setup_id,
        "target": str(target),
        "backup_slot": slot,
        "builder": {"enabled": True, "projection": BUILDER_PROJECTION},
    }


def remove_setup(target: Path) -> dict[str, Any]:
    target = resolve_target(target, create=False)
    with target_lock(target):
        stamp = require_clean_installed(target)
        desired = desired_for_remove(target)
        snapshot = snapshot_paths(target, set(desired))
        try:
            replace_managed_state(target, desired, snapshot_digests(snapshot))
        except Exception:
            restore_snapshot(target, snapshot)
            raise
    return {
        "operation": "remove",
        "removed_setup_id": stamp["setup_id"],
        "target": str(target),
        "builder": {"enabled": False, "projection": BUILDER_PROJECTION},
    }


def status_payload(target: Path) -> dict[str, Any]:
    target = resolve_target(target, create=False)
    software = software_status(target)
    stamp = read_stamp(target)
    if stamp is None:
        return {
            "installed": False,
            "target": str(target),
            "builder": {"enabled": False, "projection": BUILDER_PROJECTION},
            "software": software,
        }
    drift = drift_paths(target, stamp)
    active = stamp_is_active(stamp)
    legacy = stamp_is_legacy(stamp)
    return {
        "installed": True,
        "target": str(target),
        "setup_id": stamp_setup_id(stamp),
        "permission_profile": stamp_profile_id(stamp),
        "build_version": stamp["build_version"],
        "drift": drift,
        "legacy": legacy,
        "launchable": active and not drift,
        "builder": stamp.get("builder", {"enabled": False, "projection": BUILDER_PROJECTION}),
        "backup_pool": str(backup_pool(target)),
        "software": software,
    }


def plan_payload(target: Path, setup_id: str, profile_id: str) -> dict[str, Any]:
    load_setup(setup_id)
    load_profile(profile_id)
    target = resolve_target(target, create=False)
    stamp = read_stamp(target) if target.exists() else None
    operation = "install" if stamp is None else "switch"
    return {
        "operation": operation,
        "setup_id": setup_id,
        "permission_profile": profile_id,
        "target": str(target),
        "mutates": False,
        "managed_files": list(MANAGED_FILES),
        "builder": {"enabled": True, "projection": BUILDER_PROJECTION},
    }


def list_payload() -> dict[str, Any]:
    setups = []
    for setup_id in setup_ids():
        setup = load_setup(setup_id)
        setups.append(
            {
                "id": setup.setup_id,
                "description": setup.description,
                "default_permission_profile": DEFAULT_PROFILE_ID,
                "builder_enabled": setup.builder_enabled,
                "managed_files": list(setup.managed_files),
            }
        )
    profiles = [
        {
            "id": profile.profile_id,
            "description": profile.description,
            "launch_auto": profile.launch_auto,
        }
        for profile in (load_profile(profile_id) for profile_id in profile_ids())
    ]
    return {
        "product_name": PRODUCT_NAME,
        "build_version": VERSION,
        "default_setup": DEFAULT_SETUP_ID,
        "default_permission_profile": DEFAULT_PROFILE_ID,
        "setups": setups,
        "permission_profiles": profiles,
        "legacy_setup_ids": list(LEGACY_SETUP_IDS),
    }


def path_exists_no_follow(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def kilo_executable(target: Path) -> Path:
    return target / "bin" / KILO_COMMAND


def software_manifest_path(target: Path) -> Path:
    return target / SOFTWARE_MANIFEST_RELATIVE


def safe_child_base_environment(
    *, include_path: bool, path_entries: tuple[str, ...] = ()
) -> dict[str, str]:
    env: dict[str, str] = {}
    if include_path:
        entries = [entry for entry in path_entries if entry]
        entries.extend(CONTROLLED_PATH.split(":"))
        deduped = list(dict.fromkeys(entries))
        env["PATH"] = os.pathsep.join(deduped)
    for name in ("LANG", "LC_ALL", "LC_CTYPE", "TERM", "COLORTERM", "NO_COLOR", "CI"):
        value = os.environ.get(name)
        if value:
            env[name] = value
    return env


def install_stage_environment(
    stage_root: Path, live_stage: Path, *, path_entries: tuple[str, ...]
) -> dict[str, str]:
    home = stage_root / "home"
    tmp = stage_root / "tmp"
    xdg_config = stage_root / "xdg-config"
    xdg_data = stage_root / "xdg-data"
    xdg_state = stage_root / "xdg-state"
    xdg_cache = stage_root / "xdg-cache"
    npm_cache = stage_root / "npm-cache"
    npm_prefix = live_stage / SOFTWARE_PREFIX_RELATIVE
    npm_userconfig = stage_root / "npmrc"
    npm_globalconfig = stage_root / "global-npmrc"
    for directory in (home, tmp, xdg_config, xdg_data, xdg_state, xdg_cache, npm_cache, npm_prefix):
        directory.mkdir(mode=OWNER_DIR_MODE, parents=True, exist_ok=True)
        directory.chmod(OWNER_DIR_MODE)
    npm_userconfig.write_text(
        f"registry={NPM_REGISTRY}\naudit=false\nfund=false\nignore-scripts=true\n",
        encoding="utf-8",
    )
    npm_userconfig.chmod(OWNER_FILE_MODE)
    npm_globalconfig.write_text("", encoding="utf-8")
    npm_globalconfig.chmod(OWNER_FILE_MODE)
    env = safe_child_base_environment(include_path=True, path_entries=path_entries)
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "TMPDIR": str(tmp),
            "XDG_CONFIG_HOME": str(xdg_config),
            "XDG_DATA_HOME": str(xdg_data),
            "XDG_STATE_HOME": str(xdg_state),
            "XDG_CACHE_HOME": str(xdg_cache),
            "npm_config_cache": str(npm_cache),
            "npm_config_prefix": str(npm_prefix),
            "npm_config_userconfig": str(npm_userconfig),
            "npm_config_globalconfig": str(npm_globalconfig),
            "npm_config_update_notifier": "false",
            "npm_config_ignore_scripts": "true",
            "npm_config_audit": "false",
            "npm_config_fund": "false",
            "NPM_CONFIG_CACHE": str(npm_cache),
            "NPM_CONFIG_PREFIX": str(npm_prefix),
            "NPM_CONFIG_USERCONFIG": str(npm_userconfig),
            "NPM_CONFIG_GLOBALCONFIG": str(npm_globalconfig),
            "NPM_CONFIG_IGNORE_SCRIPTS": "true",
        }
    )
    return env


def native_wrapper_bytes(native_relative: Path, runtime_resources: dict[str, Any]) -> bytes:
    tree_sitter = runtime_resources.get("tree_sitter")
    tree_sitter_block = ""
    if isinstance(tree_sitter, dict) and tree_sitter.get("present") is True:
        tree_sitter_path = tree_sitter.get("path")
        if not isinstance(tree_sitter_path, str):
            fail("Kilo CLI tree-sitter resource contract is invalid")
        tree_sitter_block = (
            f'tree_sitter_dir="$self_dir/../{tree_sitter_path}"\n'
            'if [ -f "$tree_sitter_dir/tree-sitter.wasm" ]; then\n'
            f"  export {NATIVE_TREE_SITTER_ENV}=\"$tree_sitter_dir\"\n"
            "fi\n"
        )
    return (
        "#!/bin/sh\n"
        "set -eu\n"
        'case "$0" in\n'
        "  */*) self_dir=${0%/*} ;;\n"
        "  *) self_dir=. ;;\n"
        "esac\n"
        f'native_bin="$self_dir/../{native_relative}"\n'
        f"{tree_sitter_block}"
        'exec "$native_bin" "$@"\n'
    ).encode("utf-8")


def target_runtime_relative_paths() -> dict[str, Path]:
    return {
        "HOME": Path("home"),
        "TMPDIR": Path("tmp"),
        "XDG_CONFIG_HOME": Path("xdg-config"),
        "XDG_DATA_HOME": Path("xdg-data"),
        "XDG_STATE_HOME": Path("xdg-state"),
        "XDG_CACHE_HOME": Path("xdg-cache"),
    }


def target_runtime_paths(target: Path) -> dict[str, Path]:
    return {name: target / relative for name, relative in target_runtime_relative_paths().items()}


def ensure_target_private_subdirectory(target: Path, relative: Path, label: str) -> Path:
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        fail(f"{label} path is not target-relative")
    target_info = require_directory(target, "target")
    if not is_owner_private_directory(target_info):
        fail("target must be private to the current user with mode 0700")
    current = target
    for part in relative.parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=OWNER_DIR_MODE)
            final = chmod_directory_no_follow(current, OWNER_DIR_MODE, label)
            if not is_owner_private_directory(final):
                fail(f"{label} must be private to the current user with mode 0700: {current}")
            continue
        if stat.S_ISLNK(info.st_mode):
            fail(f"{label} must not contain symlink components: {current}")
        if not stat.S_ISDIR(info.st_mode):
            fail(f"{label} must be a real directory: {current}")
        if not is_owner_private_directory(info):
            fail(f"{label} must be private to the current user with mode 0700: {current}")
    return current


def ensure_runtime_directories(target: Path) -> dict[str, Path]:
    return {
        name: ensure_target_private_subdirectory(target, relative, f"runtime {name}")
        for name, relative in target_runtime_relative_paths().items()
    }


def launch_environment(target: Path) -> dict[str, str]:
    paths = ensure_runtime_directories(target)
    env = safe_child_base_environment(include_path=False)
    for key, path in paths.items():
        env[key] = str(path)
    env["KILO_CONFIG"] = str((target / CONFIG).resolve(strict=False))
    return env


@dataclass
class LaunchDirectoryProtection:
    modes: list[tuple[Path, int]]

    def restore(self) -> None:
        for path, mode in reversed(self.modes):
            chmod_directory_no_follow(path, mode, "launch-protected directory")


def append_directory_chain(target: Path, relative: Path, directories: list[Path]) -> None:
    for parent in reversed(relative.parents):
        if parent == Path("."):
            continue
        directories.append(target / parent)


def launch_handoff_directories(target: Path, installation: dict[str, Any]) -> list[Path]:
    native = installation.get("native_executable")
    if not isinstance(native, str) or Path(native).is_absolute():
        fail("Kilo CLI native executable provenance is invalid; run update-cli")
    native_relative = safe_relative_path(native)
    directories: list[Path] = [lock_path(target)]
    append_directory_chain(target, Path("bin") / KILO_COMMAND, directories)
    append_directory_chain(target, native_relative, directories)
    resources = installation.get("runtime_resource_contract")
    if isinstance(resources, dict):
        for value in resources.values():
            if not isinstance(value, dict) or value.get("present") is not True:
                continue
            path = value.get("path")
            if not isinstance(path, str):
                continue
            resource_relative = safe_relative_path(path)
            append_directory_chain(target, resource_relative / "resource-placeholder", directories)
            directories.append(target / resource_relative)
    unique: dict[Path, None] = {}
    for directory in directories:
        unique.setdefault(directory, None)
    return sorted(unique, key=lambda item: len(item.relative_to(target).parts))


def protect_launch_handoff_paths(
    target: Path, installation: dict[str, Any]
) -> LaunchDirectoryProtection:
    protected: list[tuple[Path, int]] = []
    try:
        for directory in launch_handoff_directories(target, installation):
            info = require_directory(directory, "launch handoff directory")
            mode = stat.S_IMODE(info.st_mode)
            if mode not in {OWNER_DIR_MODE, LOCK_HELD_PARENT_MODE}:
                fail(
                    "launch handoff directory must be private to the current user "
                    f"with mode 0700 or 0500: {directory}"
                )
            if hasattr(os, "geteuid") and owner_of(info) != os.geteuid():
                fail(f"launch handoff directory must be owned by the current user: {directory}")
            protected.append((directory, mode))
            if mode != LOCK_HELD_PARENT_MODE:
                chmod_directory_no_follow(
                    directory,
                    LOCK_HELD_PARENT_MODE,
                    "launch handoff directory",
                )
        return LaunchDirectoryProtection(protected)
    except BaseException:
        LaunchDirectoryProtection(protected).restore()
        raise


def reject_unsafe_tool_ancestors(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:-1]:
        current = current / part
        info = require_directory(current, f"tool ancestor {current}")
        if group_or_world_writable(info) and not is_sticky_directory(info):
            fail(f"tool ancestor must not be group/world-writable unless sticky: {current}")


def trusted_executable(path: Path, label: str) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    resolved = path
    if stat.S_ISLNK(info.st_mode):
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError:
            return False
    try:
        reject_unsafe_tool_ancestors(path)
        reject_unsafe_tool_ancestors(resolved)
        resolved_info = resolved.lstat()
    except ManagerError:
        raise
    except OSError:
        return False
    if stat.S_ISLNK(resolved_info.st_mode) or not stat.S_ISREG(resolved_info.st_mode):
        return False
    if group_or_world_writable(resolved_info):
        fail(f"{label} must not be group/world-writable: {resolved}")
    return os.access(resolved, os.X_OK)


def find_npm_executable() -> tuple[str, tuple[str, ...]]:
    for raw in TRUSTED_NPM_CANDIDATES:
        path = Path(raw)
        if trusted_executable(path, "npm executable"):
            return str(path), (str(path.parent),)
    fail("trusted npm executable not found in supported absolute locations")


def bounded_process(
    command: list[str], *, cwd: Path, env: dict[str, str], timeout: int
) -> subprocess.CompletedProcess[str]:
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=(os.name == "posix"),
        )
    except FileNotFoundError:
        fail(f"process executable not found: {command[0]}")
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, 15)
        else:
            process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.communicate(timeout=5)
        if process.poll() is None:
            if os.name == "posix":
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, 9)
            else:
                process.kill()
            process.communicate()
        fail(f"process timed out after {timeout} seconds: {command[0]}")
    completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if (
        len(completed.stdout) > PROCESS_OUTPUT_MAX_BYTES
        or len(completed.stderr) > PROCESS_OUTPUT_MAX_BYTES
    ):
        fail(f"process output exceeded {PROCESS_OUTPUT_MAX_BYTES}-byte limit: {command[0]}")
    return completed


def chmod_private_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts)):
        if path.is_symlink():
            continue
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            path.chmod(OWNER_DIR_MODE)
        elif stat.S_ISREG(info.st_mode):
            if stat.S_IMODE(info.st_mode) & stat.S_IXUSR:
                path.chmod(0o700)
            else:
                path.chmod(OWNER_FILE_MODE)


def staged_tree_paths_for_materialization(root: Path, *, max_paths: int) -> list[Path]:
    paths: list[Path] = []
    for path in root.rglob("*"):
        paths.append(path)
        if len(paths) > max_paths:
            fail(f"staged software tree exceeds the {max_paths}-path limit before copying")
    return sorted(paths, key=lambda item: len(item.parts))


def validate_staged_tree_bounds_for_materialization(
    paths: list[Path],
    *,
    max_file_bytes: int,
    max_tree_bytes: int,
) -> None:
    total_bytes = 0
    for path in paths:
        if path.is_symlink():
            continue
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            continue
        if info.st_size > max_file_bytes:
            fail(f"staged software file exceeds the {max_file_bytes}-byte limit: {path}")
        total_bytes += info.st_size
        if total_bytes > max_tree_bytes:
            fail(
                f"staged software tree exceeds the {max_tree_bytes}-byte limit before copying: {path}"
            )


def materialize_hardlinked_regular_file(
    path: Path,
    info: os.stat_result,
    *,
    byte_counter: dict[str, int],
    max_file_bytes: int,
    max_tree_bytes: int,
) -> None:
    if info.st_size > max_file_bytes:
        fail(f"staged software file exceeds the {max_file_bytes}-byte limit: {path}")
    if byte_counter["value"] + info.st_size > max_tree_bytes:
        fail(f"staged software tree exceeds the {max_tree_bytes}-byte limit before copying: {path}")
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    temporary = path.with_name(f".{path.name}.nddev-copy.{os.getpid()}.{time.time_ns()}")
    target_descriptor = -1
    copied = 0
    try:
        opened = os.fstat(descriptor)
        if identity_of(opened) != identity_of(info):
            raise ConcurrentTargetChange(f"staged software file changed while opened: {path}")
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink == 1:
            raise ConcurrentTargetChange(f"staged software hardlink changed while opened: {path}")
        if hasattr(os, "geteuid") and owner_of(opened) != os.geteuid():
            fail(f"staged software file must be owned by the current user: {path}")
        mode = stat.S_IMODE(opened.st_mode)
        if mode not in {OWNER_FILE_MODE, 0o700}:
            fail(f"staged software file must be private before materializing hardlink: {path}")
        if opened.st_size > max_file_bytes:
            fail(f"staged software file exceeds the {max_file_bytes}-byte limit: {path}")
        if byte_counter["value"] + opened.st_size > max_tree_bytes:
            fail(
                f"staged software tree exceeds the {max_tree_bytes}-byte limit before copying: {path}"
            )
        target_descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            OWNER_FILE_MODE,
        )
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            copied += len(chunk)
            if copied > max_file_bytes:
                fail(
                    f"staged software file exceeds the {max_file_bytes}-byte limit while copying: {path}"
                )
            byte_counter["value"] += len(chunk)
            if byte_counter["value"] > max_tree_bytes:
                fail(
                    f"staged software tree exceeds the {max_tree_bytes}-byte limit while copying: {path}"
                )
            offset = 0
            while offset < len(chunk):
                written = os.write(target_descriptor, chunk[offset:])
                if written <= 0:
                    fail(f"staged software copy made no forward progress: {path}")
                offset += written
        os.close(target_descriptor)
        target_descriptor = -1
        after = os.fstat(descriptor)
        if identity_of(after) != identity_of(opened) or after.st_size != opened.st_size:
            raise ConcurrentTargetChange(
                f"staged software file changed while it was copied: {path}"
            )
        if copied != opened.st_size:
            raise ConcurrentTargetChange(f"staged software file changed size while copied: {path}")
        temporary.chmod(mode)
        os.replace(temporary, path)
        require_regular_file(
            path, f"materialized staged software file {path}", max_bytes=max_file_bytes
        )
        if path.lstat().st_nlink != 1:
            fail(f"staged software hardlink materialization failed: {path}")
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
        raise
    finally:
        if target_descriptor >= 0:
            os.close(target_descriptor)
        os.close(descriptor)


def materialize_hardlinked_regular_files(
    root: Path,
    *,
    max_file_bytes: int = SOFTWARE_TREE_MAX_BYTES,
    max_tree_bytes: int = SOFTWARE_TREE_MAX_BYTES,
) -> None:
    paths = staged_tree_paths_for_materialization(root, max_paths=SOFTWARE_TREE_MAX_PATHS)
    validate_staged_tree_bounds_for_materialization(
        paths,
        max_file_bytes=max_file_bytes,
        max_tree_bytes=max_tree_bytes,
    )
    byte_counter = {"value": 0}
    for path in paths:
        if path.is_symlink():
            continue
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink == 1:
            continue
        if hasattr(os, "geteuid") and owner_of(info) != os.geteuid():
            fail(f"staged software file must be owned by the current user: {path}")
        mode = stat.S_IMODE(info.st_mode)
        if mode not in {OWNER_FILE_MODE, 0o700}:
            fail(f"staged software file must be private before materializing hardlink: {path}")
        materialize_hardlinked_regular_file(
            path,
            info,
            byte_counter=byte_counter,
            max_file_bytes=max_file_bytes,
            max_tree_bytes=max_tree_bytes,
        )


def resolve_target_owned_symlink(path: Path, root: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        fail(f"{label} symlink is broken")
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        fail(f"{label} symlink must stay inside the target")
    return resolved


def digest_regular_file(path: Path, label: str, byte_counter: dict[str, int]) -> str:
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        fail(f"{label} must be a regular file")
    if before.st_nlink != 1:
        fail(f"{label} must not have hard-link aliases")
    if hasattr(os, "geteuid") and owner_of(before) != os.geteuid():
        fail(f"{label} must be owned by the current user")
    mode = stat.S_IMODE(before.st_mode)
    if mode not in {OWNER_FILE_MODE, 0o700}:
        fail(f"{label} must be private to the current user")
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    digest = hashlib.sha256()
    try:
        opened = os.fstat(descriptor)
        if identity_of(opened) != identity_of(before):
            raise ConcurrentTargetChange(f"{label} changed while it was opened")
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            byte_counter["value"] += len(chunk)
            if byte_counter["value"] > SOFTWARE_TREE_MAX_BYTES:
                fail(f"installed Kilo CLI tree exceeds the {SOFTWARE_TREE_MAX_BYTES}-byte limit")
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    final = path.lstat()
    expected = identity_of(before)
    if (
        identity_of(opened) != expected
        or identity_of(after) != expected
        or identity_of(final) != expected
    ):
        raise ConcurrentTargetChange(f"{label} changed while it was read")
    return digest.hexdigest()


def digest_resolved_symlink_tree(
    path: Path,
    root: Path,
    label: str,
    byte_counter: dict[str, int],
    path_counter: dict[str, int],
    seen_directories: set[tuple[int, int]],
) -> dict[str, Any]:
    info = path.lstat()
    mode = stat.S_IMODE(info.st_mode)
    if stat.S_ISLNK(info.st_mode):
        resolved = resolve_target_owned_symlink(path, root, label)
        nested = digest_resolved_symlink_tree(
            resolved,
            root,
            f"{label} resolved target",
            byte_counter,
            path_counter,
            seen_directories,
        )
        return {
            "type": "symlink",
            "target": os.readlink(path),
            "resolved": str(resolved.relative_to(root)),
            "resolved_target": nested,
        }
    if stat.S_ISREG(info.st_mode):
        return {
            "type": "file",
            "mode": mode,
            "size": info.st_size,
            "sha256": digest_regular_file(path, label, byte_counter),
            "owner_executable": bool(mode & stat.S_IXUSR),
        }
    if stat.S_ISDIR(info.st_mode):
        if not is_owner_private_directory(info):
            fail(f"{label} directory must be private to the current user")
        directory_identity = identity_of(info)
        if directory_identity in seen_directories:
            fail(f"{label} resolves to a directory cycle")
        seen_directories.add(directory_identity)
        children: list[dict[str, Any]] = []
        for child in sorted(path.iterdir(), key=lambda item: item.name):
            path_counter["value"] += 1
            if path_counter["value"] > SOFTWARE_TREE_MAX_PATHS:
                fail(f"installed Kilo CLI tree exceeds the {SOFTWARE_TREE_MAX_PATHS}-path limit")
            children.append(
                {
                    "name": child.name,
                    **digest_resolved_symlink_tree(
                        child,
                        root,
                        f"{label}/{child.name}",
                        byte_counter,
                        path_counter,
                        seen_directories,
                    ),
                }
            )
        seen_directories.remove(directory_identity)
        return {
            "type": "directory",
            "mode": mode,
            "tree_digest": sha256_bytes(canonical_json(children)),
            "tree_paths": len(children),
        }
    fail(f"{label} resolved target has an unsupported file type")


def validate_software_symlink(
    path: Path,
    root: Path,
    label: str,
    byte_counter: dict[str, int],
) -> dict[str, Any]:
    resolved = resolve_target_owned_symlink(path, root, label)
    path_counter = {"value": 1}
    target_record = digest_resolved_symlink_tree(
        resolved,
        root,
        f"{label} resolved target",
        byte_counter,
        path_counter,
        set(),
    )
    return {
        "path": str(path.relative_to(root)),
        "type": "symlink",
        "target": os.readlink(path),
        "resolved": str(resolved.relative_to(root)),
        "resolved_target": target_record,
        "resolved_tree_paths": path_counter["value"],
    }


def require_safe_executable(path: Path, root: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError:
        fail(f"{label} is missing")
    if stat.S_ISLNK(info.st_mode):
        resolved = resolve_target_owned_symlink(path, root, label)
        return require_safe_executable(resolved, root, f"{label} resolved target")
    if not stat.S_ISREG(info.st_mode):
        fail(f"{label} must be a regular file or target-owned symlink")
    if info.st_nlink != 1:
        fail(f"{label} must not have hard-link aliases")
    if hasattr(os, "geteuid") and owner_of(info) != os.geteuid():
        fail(f"{label} must be owned by the current user")
    if stat.S_IMODE(info.st_mode) != 0o700:
        fail(f"{label} must be private to the current user with mode 0700")
    return info


def resolved_executable_path(path: Path, root: Path, label: str) -> Path:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        resolved = resolve_target_owned_symlink(path, root, label)
        require_safe_executable(resolved, root, f"{label} resolved target")
        return resolved
    require_safe_executable(path, root, label)
    return path


def fetch_registry_metadata() -> dict[str, Any]:
    request = urllib.request.Request(
        KILO_PACKAGE_METADATA_URL,
        headers={"Accept": "application/json", "User-Agent": f"{PRODUCT_NAME}/{VERSION}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=PROCESS_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                fail(f"npm registry returned HTTP {response.status} for {KILO_PACKAGE_SPEC}")
            content = response.read(METADATA_MAX_BYTES + 1)
    except urllib.error.URLError as exc:
        fail(f"cannot read npm registry metadata for {KILO_PACKAGE_SPEC}: {exc}")
    if len(content) > METADATA_MAX_BYTES:
        fail("npm registry metadata exceeds the bounded read limit")
    return parse_json_object(content, "npm registry metadata")


def verify_registry_metadata(metadata: dict[str, Any]) -> None:
    if metadata.get("name") != KILO_PACKAGE or metadata.get("version") != KILO_CURRENT_VERSION:
        fail("npm registry metadata package identity is invalid")
    dist = metadata.get("dist")
    if not isinstance(dist, dict):
        fail("npm registry metadata omits dist")
    if dist.get("integrity") != KILO_PACKAGE_INTEGRITY:
        fail("npm registry metadata integrity does not match the pinned baseline")
    if dist.get("shasum") != KILO_PACKAGE_SHASUM:
        fail("npm registry metadata shasum does not match the pinned baseline")
    scripts = metadata.get("scripts")
    if not isinstance(scripts, dict) or scripts.get("postinstall") != "node ./postinstall.mjs":
        fail("npm registry metadata vendor lifecycle script baseline changed")
    supported, unsupported = native_package_matrix()
    expected_native = {package: KILO_CURRENT_VERSION for package in (*supported, *unsupported)}
    optional = metadata.get("optionalDependencies")
    if optional != expected_native:
        fail("npm registry metadata native optional dependency map is not synchronized")


def package_lock_path(root: Path) -> Path:
    return root / SOFTWARE_LOCK_RELATIVE


def package_lock_records(root: Path) -> dict[str, Any]:
    lock = read_json_file(package_lock_path(root), "Kilo CLI package lock")
    packages = lock.get("packages")
    if not isinstance(packages, dict):
        fail("Kilo CLI package lock omits package records")
    supported, unsupported = native_package_matrix()
    expected_records: dict[str, dict[str, Any]] = {
        KILO_PACKAGE: {
            "version": KILO_CURRENT_VERSION,
            "dist": {
                "integrity": KILO_PACKAGE_INTEGRITY,
                "shasum": KILO_PACKAGE_SHASUM,
                "tarball": f"https://registry.npmjs.org/@kilocode/cli/-/cli-{KILO_CURRENT_VERSION}.tgz",
            },
        },
        **supported,
        **unsupported,
    }
    for path, record in packages.items():
        if path == "":
            continue
        if not isinstance(record, dict):
            fail(f"Kilo CLI package lock record is invalid: {path}")
        if record.get("link") is True:
            continue
        package = package_name_from_lock_path(str(path))
        if package is None:
            fail(f"Kilo CLI package lock has unexpected package path: {path}")
        expected = expected_records.get(package)
        if expected is None:
            fail(f"Kilo CLI package lock has unexpected package identity: {package}")
        resolved = record.get("resolved")
        integrity = record.get("integrity")
        if not isinstance(resolved, str) or not resolved.startswith(NPM_REGISTRY):
            fail(f"Kilo CLI package lock record is not resolved from the official registry: {package}")
        if not isinstance(integrity, str) or not integrity:
            fail(f"Kilo CLI package lock record omits integrity: {package}")
        expected_dist = native_record_dist(expected, package)
        if record.get("version") != expected.get("version"):
            fail(f"Kilo CLI package lock version mismatch: {package}")
        if resolved != expected_dist["tarball"]:
            fail(f"Kilo CLI package lock tarball mismatch: {package}")
        if integrity != expected_dist["integrity"]:
            fail(f"Kilo CLI package lock integrity mismatch: {package}")
    cli = packages.get("node_modules/@kilocode/cli")
    if not isinstance(cli, dict):
        fail("Kilo CLI package lock omits @kilocode/cli")
    optional = cli.get("optionalDependencies")
    expected_optional = {package: KILO_CURRENT_VERSION for package in (*supported, *unsupported)}
    if optional != expected_optional:
        fail("Kilo CLI package lock omits native optional dependencies")
    return lock


def native_package_binary_digest(root: Path, package_path: Path) -> str:
    binary = package_path / "bin" / KILO_COMMAND
    require_safe_executable(binary, root, f"Kilo native package binary {package_path.name}")
    return digest_regular_file(binary, f"Kilo native package binary {package_path.name}", {"value": 0})


def optional_directory_entry_contract(
    root: Path,
    directory: Path,
    entrypoint: str,
    label: str,
) -> dict[str, Any]:
    if not path_exists_no_follow(directory):
        return {"present": False}
    info = require_directory(directory, label)
    if not is_owner_private_directory(info):
        fail(f"{label} must be private to the current user with mode 0700")
    entry = directory / entrypoint
    digest = digest_regular_file(entry, f"{label} entrypoint", {"value": 0})
    return {
        "present": True,
        "path": str(directory.relative_to(root)),
        "entrypoint": str(entry.relative_to(root)),
        "entrypoint_sha256": digest,
    }


def optional_directory_contract(root: Path, directory: Path, label: str) -> dict[str, Any]:
    if not path_exists_no_follow(directory):
        return {"present": False}
    info = require_directory(directory, label)
    if not is_owner_private_directory(info):
        fail(f"{label} must be private to the current user with mode 0700")
    return {
        "present": True,
        "path": str(directory.relative_to(root)),
    }


def optional_file_contract(
    root: Path,
    path: Path,
    label: str,
    *,
    executable: bool = False,
) -> dict[str, Any]:
    if not path_exists_no_follow(path):
        return {"present": False}
    if executable:
        require_safe_executable(path, root, label)
    digest = digest_regular_file(path, label, {"value": 0})
    return {
        "present": True,
        "path": str(path.relative_to(root)),
        "sha256": digest,
    }


def runtime_resource_contract(root: Path, package_path: Path) -> dict[str, Any]:
    bin_root = package_path / "bin"
    bin_info = require_directory(bin_root, f"Kilo native package bin {package_path.name}")
    if not is_owner_private_directory(bin_info):
        fail(f"Kilo native package bin {package_path.name} must be private to the current user")
    tree_sitter = optional_directory_entry_contract(
        root,
        bin_root / "tree-sitter",
        "tree-sitter.wasm",
        f"Kilo native package tree-sitter resources {package_path.name}",
    )
    if tree_sitter.get("present") is True:
        tree_sitter["env"] = NATIVE_TREE_SITTER_ENV
    else:
        tree_sitter = {"present": False, "env": NATIVE_TREE_SITTER_ENV}
    return {
        "schema_version": 1,
        "root": str(bin_root.relative_to(root)),
        "tree_sitter": tree_sitter,
        "console": optional_directory_entry_contract(
            root,
            bin_root / "console",
            "index.html",
            f"Kilo native package console resources {package_path.name}",
        ),
        "bwrap": optional_file_contract(
            root,
            bin_root / "bwrap",
            f"Kilo native package bwrap resource {package_path.name}",
            executable=True,
        ),
        "licenses": optional_directory_contract(
            root,
            bin_root / "licenses",
            f"Kilo native package license resources {package_path.name}",
        ),
        "sandbox_mutation_worker": optional_file_contract(
            root,
            bin_root / "kilo-sandbox-mutation-worker.js",
            f"Kilo native package sandbox worker resource {package_path.name}",
        ),
    }


def validate_vendor_postinstall_not_materialized(root: Path) -> None:
    for relative in VENDOR_POSTINSTALL_RESOURCE_RELATIVES:
        if path_exists_no_follow(root / relative):
            fail(f"vendor postinstall artifact is present despite ignore-scripts: {relative}")


def installed_native_packages(root: Path) -> dict[str, Any]:
    supported, unsupported = native_package_matrix()
    allowed_for_host = expected_native_records_for_host()
    lock = package_lock_records(root)
    lock_packages = lock.get("packages", {})
    host = host_native_platform()
    install_root = root / SOFTWARE_GLOBAL_DIR_RELATIVE / "node_modules"
    records: list[dict[str, Any]] = []
    path_count = 0
    for manifest in install_root.rglob("package.json"):
        path_count += 1
        if path_count > SOFTWARE_TREE_MAX_PATHS:
            fail("installed package scan exceeded the path limit")
        try:
            metadata = read_json_file(
                manifest,
                f"installed package manifest {manifest}",
                max_bytes=METADATA_MAX_BYTES,
            )
        except ManagerError:
            continue
        name = metadata.get("name")
        version = metadata.get("version")
        if not isinstance(name, str) or not name.startswith("@kilocode/cli-"):
            continue
        baseline_record = supported.get(name)
        if baseline_record is None:
            if name in unsupported:
                fail(f"unsupported Kilo native package is installed: {name}")
            fail(f"unexpected Kilo native package is installed: {name}")
        if version != baseline_record.get("version"):
            fail(f"installed native package has unexpected version: {name}@{version}")
        if metadata.get("os") != baseline_record.get("os"):
            fail(f"installed native package OS metadata mismatch: {name}")
        if metadata.get("cpu") != baseline_record.get("cpu"):
            fail(f"installed native package CPU metadata mismatch: {name}")
        if metadata.get("libc") != baseline_record.get("libc"):
            fail(f"installed native package libc metadata mismatch: {name}")
        lock_path_key = "node_modules/" + name
        lock_record = lock_packages.get(lock_path_key)
        if not isinstance(lock_record, dict):
            fail(f"Kilo native package is not represented in the package lock: {name}")
        dist = native_record_dist(baseline_record, name)
        if (
            lock_record.get("resolved") != dist["tarball"]
            or lock_record.get("integrity") != dist["integrity"]
        ):
            fail(f"Kilo native package lock provenance mismatch: {name}")
        package_path = manifest.parent
        records.append(
            {
                "name": name,
                "version": str(version),
                "os": list(baseline_record["os"]),
                "cpu": list(baseline_record["cpu"]),
                "libc": baseline_record.get("libc"),
                "path": str(manifest.parent.relative_to(root)),
                "binary": str(native_package_binary_relative(name)),
                "tarball": dist["tarball"],
                "integrity": dist["integrity"],
                "binary_sha256": native_package_binary_digest(root, package_path),
                "runtime_resources": runtime_resource_contract(root, package_path),
            }
        )
    if not records:
        fail("Kilo CLI npm install did not leave an installed native package")
    allowed_names = set(allowed_for_host)
    candidates = [record for record in records if record["name"] in allowed_names]
    if not candidates:
        fail(
            "Kilo CLI npm install did not install a native package allowed for "
            f"{host['os']}/{host['cpu']}/{host['libc'] or 'glibc'}"
        )
    installed_by_name = {record["name"]: record for record in candidates}
    selected_name = selected_native_package_name_for_host(host)
    selected = installed_by_name.get(selected_name)
    if selected is None:
        fail(f"selected Kilo native package is not installed: {selected_name}")
    return {
        "host": host,
        "allowed_packages": sorted(allowed_names),
        "installed": sorted(records, key=lambda item: item["name"]),
        "selection_order": list(native_package_preference_order(host)),
        "selected": selected,
    }


def package_metadata(root: Path) -> dict[str, Any]:
    metadata = read_json_file(
        root / SOFTWARE_GLOBAL_DIR_RELATIVE / "node_modules" / "@kilocode" / "cli" / "package.json",
        "Kilo CLI package",
        max_bytes=METADATA_MAX_BYTES,
    )
    if metadata.get("name") != KILO_PACKAGE:
        fail("Kilo CLI package identity is invalid")
    if metadata.get("version") != KILO_CURRENT_VERSION:
        fail("Kilo CLI package version is invalid")
    bins = metadata.get("bin")
    if isinstance(bins, dict):
        normalized_bins = {
            key: value[2:] if isinstance(value, str) and value.startswith("./") else value
            for key, value in bins.items()
        }
    else:
        normalized_bins = bins
    if normalized_bins != {"kilo": "bin/kilo", "kilocode": "bin/kilo"}:
        fail("Kilo CLI package bin map is invalid")
    scripts = metadata.get("scripts")
    if not isinstance(scripts, dict) or scripts.get("postinstall") != "node ./postinstall.mjs":
        fail("Kilo CLI package vendor lifecycle script baseline changed")
    optional = metadata.get("optionalDependencies")
    supported, unsupported = native_package_matrix()
    expected_optional = {package: KILO_CURRENT_VERSION for package in (*supported, *unsupported)}
    if optional != expected_optional:
        fail("Kilo CLI package native optional dependency map is invalid")
    return metadata


def iter_software_tree_paths(root: Path) -> list[Path]:
    paths = [Path("bin") / KILO_COMMAND, SOFTWARE_PREFIX_RELATIVE, SOFTWARE_LOCK_RELATIVE]
    install_root = root / SOFTWARE_PREFIX_RELATIVE
    if install_root.exists() or install_root.is_symlink():
        for path in install_root.rglob("*"):
            paths.append(path.relative_to(root))
            if len(paths) > SOFTWARE_TREE_MAX_PATHS:
                fail(f"installed Kilo CLI tree exceeds the {SOFTWARE_TREE_MAX_PATHS}-path limit")
    return sorted(set(paths), key=lambda item: str(item))


def compute_software_tree_digest(root: Path) -> dict[str, Any]:
    root = root.resolve()
    byte_counter = {"value": 0}
    records: list[dict[str, Any]] = []
    for relative in iter_software_tree_paths(root):
        if len(records) >= SOFTWARE_TREE_MAX_PATHS:
            fail(f"installed Kilo CLI tree exceeds the {SOFTWARE_TREE_MAX_PATHS}-path limit")
        path = root / relative
        try:
            info = path.lstat()
        except FileNotFoundError:
            fail(f"installed software path {relative} is missing")
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISDIR(info.st_mode):
            if not is_owner_private_directory(info):
                fail(f"installed software directory {relative} must be private to the current user")
            records.append({"path": str(relative), "type": "directory", "mode": mode})
            continue
        if stat.S_ISLNK(info.st_mode):
            records.append(
                validate_software_symlink(
                    path,
                    root,
                    f"installed software path {relative}",
                    byte_counter,
                )
            )
            continue
        digest = digest_regular_file(path, f"installed software file {relative}", byte_counter)
        records.append(
            {
                "path": str(relative),
                "type": "file",
                "mode": mode,
                "size": info.st_size,
                "sha256": digest,
                "owner_executable": bool(mode & stat.S_IXUSR),
            }
        )
    require_safe_executable(root / "bin" / KILO_COMMAND, root, "Kilo CLI executable")
    metadata = package_metadata(root)
    validate_vendor_postinstall_not_materialized(root)
    lock = package_lock_records(root)
    native_provenance = installed_native_packages(root)
    selected = native_provenance["selected"]
    wrapper_sha256 = digest_regular_file(
        resolved_executable_path(
            root / "bin" / KILO_COMMAND,
            root,
            "Kilo CLI executable",
        ),
        "Kilo CLI executable",
        {"value": 0},
    )
    return {
        "tree_digest": sha256_bytes(canonical_json(records)),
        "tree_bytes": byte_counter["value"],
        "tree_paths": len(records),
        "package_name": metadata["name"],
        "version": metadata["version"],
        "native_package_provenance": native_provenance,
        "native_packages": native_provenance["installed"],
        "selected_native_package": selected["name"],
        "selected_native_package_path": selected["path"],
        "native_executable": selected["binary"],
        "native_executable_sha256": selected["binary_sha256"],
        "runtime_resource_contract": selected["runtime_resources"],
        "package_lock_sha256": digest_regular_file(
            package_lock_path(root),
            "Kilo CLI package lock",
            {"value": 0},
        ),
        "package_lock_version": lock.get("lockfileVersion"),
        "entrypoint_sha256": wrapper_sha256,
        "wrapper_sha256": wrapper_sha256,
        "package_manifest_sha256": digest_regular_file(
            root
            / SOFTWARE_GLOBAL_DIR_RELATIVE
            / "node_modules"
            / "@kilocode"
            / "cli"
            / "package.json",
            "Kilo CLI package manifest",
            {"value": 0},
        ),
    }


def software_manifest_identity() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "product_name": PRODUCT_NAME,
        "package": KILO_PACKAGE,
        "install_method": "npm-global-exact-isolated-ignore-scripts",
        "package_version": KILO_CURRENT_VERSION,
        "package_integrity": KILO_PACKAGE_INTEGRITY,
        "package_shasum": KILO_PACKAGE_SHASUM,
        "npm_registry": NPM_REGISTRY,
        "npm_install_argv": list(NPM_INSTALL_ARGV),
        "npm_lock_argv": list(NPM_LOCK_ARGV),
        "lifecycle_scripts": "disabled",
        "vendor_postinstall": {
            "present": True,
            "script": "node ./postinstall.mjs",
            "executed": False,
            "nested_npm_fallback_allowed": False,
        },
        "executable": f"bin/{KILO_COMMAND}",
        "entrypoint_kind": "target-owned-native-wrapper",
        "vendor_package_bin": str(KILO_PACKAGE_BIN_RELATIVE),
        "native_executable_policy": "selected-native-package-bin-kilo",
        "install_root": str(SOFTWARE_PREFIX_RELATIVE),
        "package_lock": str(SOFTWARE_LOCK_RELATIVE),
    }


def build_software_manifest(root: Path) -> dict[str, Any]:
    return {**software_manifest_identity(), **compute_software_tree_digest(root)}


def write_stage_software_manifest(live_stage: Path) -> None:
    manifest = live_stage / SOFTWARE_MANIFEST_RELATIVE
    manifest.parent.mkdir(mode=OWNER_DIR_MODE, parents=True, exist_ok=True)
    manifest.write_bytes(canonical_json(build_software_manifest(live_stage)))
    manifest.chmod(OWNER_FILE_MODE)


def software_presence(target: Path) -> dict[str, Any]:
    replace_paths_present = [
        str(relative)
        for relative in SOFTWARE_REPLACE_PATHS
        if path_exists_no_follow(target / relative)
    ]
    owned_parent_paths_present = [
        str(relative)
        for relative in SOFTWARE_PARENT_PATHS
        if path_exists_no_follow(target / relative)
    ]
    if not replace_paths_present and not owned_parent_paths_present:
        state = "absent"
    elif len(replace_paths_present) == len(SOFTWARE_REPLACE_PATHS):
        state = "installed"
    else:
        state = "partial"
    return {
        "software_state": state,
        "partial": state == "partial",
        "replace_paths_present": replace_paths_present,
        "owned_parent_paths_present": owned_parent_paths_present,
    }


def software_status(target: Path) -> dict[str, Any]:
    if not require_private_target_directory_for_software(target, allow_missing=True):
        return {
            "schema_version": 1,
            "command": "software-status",
            "target": str(target),
            "installed": False,
            "current": False,
            "version": None,
            "executable": None,
            "software_state": "absent",
            "partial": False,
            "replace_paths_present": [],
            "owned_parent_paths_present": [],
        }
    presence = software_presence(target)
    executable = kilo_executable(target)
    if presence["software_state"] != "installed":
        return {
            "schema_version": 1,
            "command": "software-status",
            "target": str(target),
            "installed": False,
            "current": False,
            "version": None,
            "executable": str(executable),
            **presence,
        }
    try:
        manifest = read_json_file(
            software_manifest_path(target),
            "Kilo CLI software manifest",
            owner_only=True,
        )
    except ManagerError as exc:
        return {
            "schema_version": 1,
            "command": "software-status",
            "target": str(target),
            "installed": True,
            "current": False,
            "version": None,
            "executable": str(executable),
            **presence,
            "validation_error": str(exc),
        }
    try:
        expected = build_software_manifest(target)
    except ManagerError as exc:
        expected = None
        validation_error = str(exc)
    else:
        validation_error = None
    current = (
        expected is not None
        and manifest == expected
        and manifest.get("version") == KILO_CURRENT_VERSION
    )
    result = {
        "schema_version": 1,
        "command": "software-status",
        "target": str(target),
        "installed": True,
        "current": current,
        "version": manifest.get("version"),
        "executable": str(executable),
        "entrypoint_sha256": manifest.get("entrypoint_sha256"),
        "wrapper_sha256": manifest.get("wrapper_sha256"),
        "native_executable": manifest.get("native_executable"),
        "native_executable_sha256": manifest.get("native_executable_sha256"),
        "package": manifest.get("package"),
        "install_method": manifest.get("install_method"),
        "lifecycle_scripts": manifest.get("lifecycle_scripts"),
        "selected_native_package": manifest.get("selected_native_package"),
        "selected_native_package_path": manifest.get("selected_native_package_path"),
        "runtime_resource_contract": manifest.get("runtime_resource_contract"),
        **presence,
    }
    if validation_error is not None:
        result["validation_error"] = validation_error
    return result


def validate_software_parent_destination(target: Path, relative: Path) -> None:
    parent = target / relative
    if not path_exists_no_follow(parent):
        return
    info = require_directory(parent, f"existing software parent {relative}")
    if not is_owner_private_directory(info):
        fail(f"existing software parent {relative} must be private to the current user")


def validate_replace_destination(target: Path, relative: Path) -> None:
    destination = target / relative
    if not path_exists_no_follow(destination):
        return
    if relative == Path("bin") / KILO_COMMAND:
        require_safe_executable(destination, target, "existing Kilo CLI executable")
        return
    if relative == SOFTWARE_PREFIX_RELATIVE:
        info = require_directory(destination, f"existing software directory {relative}")
        if not is_owner_private_directory(info):
            fail(f"existing software directory {relative} must be private to the current user")
        return
    if relative in {SOFTWARE_MANIFEST_RELATIVE, SOFTWARE_LOCK_RELATIVE}:
        require_regular_file(
            destination,
            f"existing software file {relative}",
            owner_only=True,
        )
        return
    fail(f"unsupported software replace path: {relative}")


def validate_existing_software_tree_safety(target: Path) -> None:
    install_root = target / SOFTWARE_GLOBAL_DIR_RELATIVE
    if not path_exists_no_follow(install_root):
        return
    byte_counter = {"value": 0}
    for relative in iter_software_tree_paths(target):
        path = target / relative
        if not path_exists_no_follow(path):
            continue
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            if not is_owner_private_directory(info):
                fail(f"existing software directory {relative} must be private to the current user")
            continue
        if stat.S_ISLNK(info.st_mode):
            validate_software_symlink(
                path,
                target,
                f"existing software path {relative}",
                byte_counter,
            )
            continue
        digest_regular_file(path, f"existing software file {relative}", byte_counter)


def validate_existing_software_surface(target: Path) -> None:
    for relative in SOFTWARE_PARENT_PATHS:
        validate_software_parent_destination(target, relative)
    for relative in SOFTWARE_REPLACE_PATHS:
        validate_replace_destination(target, relative)
    validate_existing_software_tree_safety(target)


def ensure_replace_parent(destination: Path) -> None:
    parent = destination.parent
    try:
        info = parent.lstat()
    except FileNotFoundError:
        parent.mkdir(mode=OWNER_DIR_MODE, parents=True)
        parent.chmod(OWNER_DIR_MODE)
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        fail(f"software destination parent {parent} must be a real directory")
    if not is_owner_private_directory(info):
        fail(f"software destination parent {parent} must be private to the current user")


def move_replace_path(source: Path, destination: Path) -> None:
    ensure_replace_parent(destination)
    os.replace(source, destination)


def move_old_path(source: Path, saved: Path) -> None:
    saved.parent.mkdir(mode=OWNER_DIR_MODE, parents=True, exist_ok=True)
    os.replace(source, saved)


def cleanup_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def restore_software_paths(
    target: Path,
    hold: Path,
    live_stage: Path,
    *,
    moved_old: list[Path],
    installed_new: list[Path],
    preexisting_parent_paths: set[Path],
) -> None:
    new_paths = set(installed_new)
    for relative in SOFTWARE_REPLACE_PATHS:
        if relative not in new_paths and not path_exists_no_follow(live_stage / relative):
            new_paths.add(relative)
    for relative in reversed(SOFTWARE_REPLACE_PATHS):
        destination = target / relative
        if relative in new_paths and path_exists_no_follow(destination):
            cleanup_path(destination)
    for relative in reversed(moved_old):
        saved = hold / relative
        if path_exists_no_follow(saved):
            move_replace_path(saved, target / relative)
    for relative in sorted(SOFTWARE_PARENT_PATHS, key=lambda item: len(item.parts), reverse=True):
        if relative in preexisting_parent_paths:
            continue
        parent = target / relative
        if not parent.exists() or parent.is_symlink() or not parent.is_dir():
            continue
        try:
            parent.rmdir()
        except OSError:
            continue


def replace_software_state(target: Path, live_stage: Path, hold_parent: Path) -> None:
    for relative in SOFTWARE_REPLACE_PATHS:
        source = live_stage / relative
        if not path_exists_no_follow(source):
            fail(f"staged software path {relative} is missing")
        validate_replace_destination(live_stage, relative)
        validate_replace_destination(target, relative)
    hold = hold_parent / "rollback"
    if path_exists_no_follow(hold):
        cleanup_path(hold)
    hold.mkdir(mode=OWNER_DIR_MODE)
    preexisting_parent_paths = {
        relative for relative in SOFTWARE_PARENT_PATHS if path_exists_no_follow(target / relative)
    }
    moved_old: list[Path] = []
    installed_new: list[Path] = []
    try:
        for relative in SOFTWARE_REPLACE_PATHS:
            destination = target / relative
            if path_exists_no_follow(destination):
                saved = hold / relative
                move_old_path(destination, saved)
                moved_old.append(relative)
        for relative in SOFTWARE_REPLACE_PATHS:
            move_replace_path(live_stage / relative, target / relative)
            installed_new.append(relative)
        status = software_status(target)
        if not status["installed"] or not status["current"]:
            fail("installed Kilo CLI did not validate as the pinned npm package")
    except BaseException:
        moved_old = [
            relative
            for relative in SOFTWARE_REPLACE_PATHS
            if path_exists_no_follow(hold / relative)
        ]
        restore_software_paths(
            target,
            hold,
            live_stage,
            moved_old=moved_old,
            installed_new=installed_new,
            preexisting_parent_paths=preexisting_parent_paths,
        )
        raise
    finally:
        shutil.rmtree(hold, ignore_errors=True)


def materialize_stage_entrypoint(live_stage: Path, native_provenance: dict[str, Any]) -> None:
    entrypoint = live_stage / "bin" / KILO_COMMAND
    selected = native_provenance.get("selected")
    if not isinstance(selected, dict):
        fail("Kilo CLI native package selection is invalid")
    native_relative = selected.get("binary")
    if not isinstance(native_relative, str):
        fail("Kilo CLI native package binary selection is invalid")
    native_bin = live_stage / safe_relative_path(native_relative)
    require_safe_executable(native_bin, live_stage, "staged Kilo CLI native executable")
    entrypoint.parent.mkdir(mode=OWNER_DIR_MODE, parents=True, exist_ok=True)
    if path_exists_no_follow(entrypoint):
        info = entrypoint.lstat()
        if stat.S_ISLNK(info.st_mode) or stat.S_ISREG(info.st_mode):
            entrypoint.unlink()
        else:
            fail("staged Kilo CLI executable path is not replaceable")
    runtime_resources = selected.get("runtime_resources")
    if not isinstance(runtime_resources, dict):
        fail("Kilo CLI native package resource contract is invalid")
    entrypoint.write_bytes(native_wrapper_bytes(Path(native_relative), runtime_resources))
    entrypoint.chmod(0o700)
    require_safe_executable(entrypoint, live_stage, "staged Kilo CLI executable")


def write_lock_project(stage_root: Path) -> Path:
    project = stage_root / "npm-lock-project"
    project.mkdir(mode=OWNER_DIR_MODE)
    package_json = {
        "private": True,
        "name": "nddev-kilo-cli-lock",
        "version": "0.0.0",
        "dependencies": {KILO_PACKAGE: KILO_CURRENT_VERSION},
    }
    path = project / "package.json"
    path.write_bytes(canonical_json(package_json))
    path.chmod(OWNER_FILE_MODE)
    return project


def generate_package_lock(stage_root: Path, live_stage: Path, npm: str, env: dict[str, str]) -> Path:
    project = write_lock_project(stage_root)
    completed = bounded_process(
        [npm, *NPM_LOCK_ARGV],
        cwd=project,
        env=env,
        timeout=PROCESS_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        fail(
            "npm package-lock generation for Kilo CLI failed with exit "
            f"{completed.returncode}: {completed.stderr.strip()}"
        )
    source = project / "package-lock.json"
    require_regular_file(source, "generated Kilo CLI package lock", max_bytes=METADATA_MAX_BYTES)
    destination = live_stage / SOFTWARE_LOCK_RELATIVE
    destination.parent.mkdir(mode=OWNER_DIR_MODE, parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    destination.chmod(OWNER_FILE_MODE)
    package_lock_records(live_stage)
    return destination


def run_npm_install(stage_root: Path, live_stage: Path) -> None:
    npm, path_entries = find_npm_executable()
    registry_metadata = fetch_registry_metadata()
    verify_registry_metadata(registry_metadata)
    env = install_stage_environment(stage_root, live_stage, path_entries=path_entries)
    generate_package_lock(stage_root, live_stage, npm, env)
    prefix = live_stage / SOFTWARE_PREFIX_RELATIVE
    completed = bounded_process(
        [npm, *NPM_INSTALL_ARGV, "--prefix", str(prefix)],
        cwd=stage_root,
        env=env,
        timeout=PROCESS_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        fail(
            f"npm install for Kilo CLI failed with exit {completed.returncode}: {completed.stderr.strip()}"
        )
    validate_vendor_postinstall_not_materialized(live_stage)
    chmod_private_tree(live_stage)
    materialize_hardlinked_regular_files(live_stage)
    native_provenance = installed_native_packages(live_stage)
    materialize_stage_entrypoint(live_stage, native_provenance)
    package_lock_records(live_stage)
    installed_native_packages(live_stage)


def remove_created_target_if_empty(target: Path, existed_before: bool) -> None:
    if existed_before:
        return
    try:
        if target.exists() and target.is_dir() and not any(target.iterdir()):
            target.rmdir()
    except OSError:
        pass


def install_or_update_cli(target: Path, command: str) -> dict[str, Any]:
    target_existed_before = path_exists_no_follow(target)
    preflight = software_status(target)
    if command == "install-cli":
        if preflight.get("partial"):
            fail("partial target-owned Kilo CLI software state exists; use update-cli")
        if preflight.get("replace_paths_present") or preflight.get("owned_parent_paths_present"):
            fail("Kilo CLI software already exists; use update-cli")
    if command == "update-cli":
        if preflight["software_state"] == "absent":
            fail("Kilo CLI is not installed at the selected target; use install-cli")
        if preflight["installed"] and preflight["current"]:
            return {
                "schema_version": 1,
                "command": command,
                "target": str(target),
                "changed": False,
                "version": preflight["version"],
                "executable": preflight["executable"],
                "selected_native_package": preflight.get("selected_native_package"),
                "native_executable": preflight.get("native_executable"),
                "wrapper_sha256": preflight.get("wrapper_sha256"),
            }
    staging: Path | None = None
    with target_lock(target, create_parent=(command == "install-cli")) as transaction:
        try:
            ensure_private_directory(
                target,
                create=(command == "install-cli"),
                transaction=transaction,
            )
            status = software_status(target)
            if command == "install-cli":
                if status.get("partial"):
                    fail("partial target-owned Kilo CLI software state exists; use update-cli")
                if status.get("replace_paths_present") or status.get("owned_parent_paths_present"):
                    fail("Kilo CLI software already exists; use update-cli")
            if command == "update-cli":
                if status["software_state"] == "absent":
                    fail("Kilo CLI is not installed at the selected target; use install-cli")
                validate_existing_software_surface(target)
                if status["installed"] and status["current"]:
                    return {
                        "schema_version": 1,
                        "command": command,
                        "target": str(target),
                        "changed": False,
                        "version": status["version"],
                        "executable": status["executable"],
                        "selected_native_package": status.get("selected_native_package"),
                        "native_executable": status.get("native_executable"),
                        "wrapper_sha256": status.get("wrapper_sha256"),
                    }
            staging = Path(
                tempfile.mkdtemp(dir=target.parent, prefix=f".{target.name}.nddev-kilo-cli-stage.")
            )
            staging.chmod(OWNER_DIR_MODE)
            live_stage = staging / "live"
            live_stage.mkdir(mode=OWNER_DIR_MODE)
            run_npm_install(staging, live_stage)
            chmod_private_tree(live_stage)
            write_stage_software_manifest(live_stage)
            replace_software_state(target, live_stage, staging)
            installation = require_current_software(target)
        except BaseException:
            remove_created_target_if_empty(target, target_existed_before)
            raise
        finally:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
    return {
        "schema_version": 1,
        "command": command,
        "target": str(target),
        "changed": True,
        "version": installation["version"],
        "executable": installation["executable"],
        "selected_native_package": installation.get("selected_native_package"),
        "native_executable": installation.get("native_executable"),
        "wrapper_sha256": installation.get("wrapper_sha256"),
    }


def remove_cli(target: Path) -> dict[str, Any]:
    status = software_status(target)
    if status["software_state"] == "absent":
        return {
            "schema_version": 1,
            "command": "remove-cli",
            "target": str(target),
            "changed": False,
        }
    staging = Path(
        tempfile.mkdtemp(dir=target.parent, prefix=f".{target.name}.nddev-kilo-cli-remove.")
    )
    try:
        empty_live = staging / "live"
        empty_live.mkdir(mode=OWNER_DIR_MODE)
        hold = staging / "hold"
        hold.mkdir(mode=OWNER_DIR_MODE)
        preexisting_parent_paths = {
            relative
            for relative in SOFTWARE_PARENT_PATHS
            if path_exists_no_follow(target / relative)
        }
        moved_old: list[Path] = []
        with target_lock(target):
            require_private_target_directory_for_software(target, allow_missing=False)
            validate_existing_software_surface(target)
            try:
                for relative in SOFTWARE_REPLACE_PATHS:
                    destination = target / relative
                    if path_exists_no_follow(destination):
                        saved = hold / relative
                        move_old_path(destination, saved)
                        moved_old.append(relative)
                for relative in sorted(
                    SOFTWARE_PARENT_PATHS, key=lambda item: len(item.parts), reverse=True
                ):
                    if relative in preexisting_parent_paths:
                        parent = target / relative
                        try:
                            parent.rmdir()
                        except OSError:
                            pass
            except BaseException:
                restore_software_paths(
                    target,
                    hold,
                    empty_live,
                    moved_old=[
                        relative
                        for relative in SOFTWARE_REPLACE_PATHS
                        if path_exists_no_follow(hold / relative)
                    ],
                    installed_new=[],
                    preexisting_parent_paths=preexisting_parent_paths,
                )
                raise
        return {
            "schema_version": 1,
            "command": "remove-cli",
            "target": str(target),
            "changed": bool(moved_old),
        }
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def require_current_software(target: Path) -> dict[str, Any]:
    status = software_status(target)
    if not status["installed"]:
        fail("Kilo CLI is not installed at the selected target; run install-cli")
    if not status["current"]:
        detail = f": {status['validation_error']}" if "validation_error" in status else ""
        fail(f"Kilo CLI is not current at the selected target; run update-cli{detail}")
    return status


def clean_launch_env(target: Path) -> dict[str, str]:
    return launch_environment(target)


def revalidate_launch_executable(target: Path, installation: dict[str, Any]) -> str:
    executable = installation.get("executable")
    expected_sha256 = installation.get("entrypoint_sha256")
    if not isinstance(executable, str) or Path(executable) != kilo_executable(target):
        fail("Kilo CLI executable provenance is invalid; run update-cli")
    if not isinstance(expected_sha256, str) or not expected_sha256:
        fail("Kilo CLI executable digest provenance is missing; run update-cli")
    resolved = resolved_executable_path(Path(executable), target, "Kilo CLI executable")
    before = resolved.lstat()
    observed_sha256 = digest_regular_file(
        resolved,
        "Kilo CLI executable",
        {"value": 0},
    )
    after = resolved.lstat()
    if identity_of(before) != identity_of(after):
        raise ConcurrentTargetChange("Kilo CLI executable changed before launch")
    if observed_sha256 != expected_sha256:
        fail("Kilo CLI executable digest changed before launch; run update-cli")
    native = installation.get("native_executable")
    native_sha256 = installation.get("native_executable_sha256")
    if not isinstance(native, str) or Path(native).is_absolute():
        fail("Kilo CLI native executable provenance is invalid; run update-cli")
    if not isinstance(native_sha256, str) or not native_sha256:
        fail("Kilo CLI native executable digest provenance is missing; run update-cli")
    native_path = target / safe_relative_path(native)
    native_resolved = resolved_executable_path(native_path, target, "Kilo CLI native executable")
    native_before = native_resolved.lstat()
    observed_native_sha256 = digest_regular_file(
        native_resolved,
        "Kilo CLI native executable",
        {"value": 0},
    )
    native_after = native_resolved.lstat()
    if identity_of(native_before) != identity_of(native_after):
        raise ConcurrentTargetChange("Kilo CLI native executable changed before launch")
    if observed_native_sha256 != native_sha256:
        fail("Kilo CLI native executable digest changed before launch; run update-cli")
    return executable


def forbidden_launch_option(argument: str) -> str | None:
    if argument == "--":
        return None
    option = argument.split("=", 1)[0]
    if option in FORBIDDEN_LAUNCH_ARGS:
        return option
    if option.startswith("--no-") and f"--{option[5:]}" in FORBIDDEN_LAUNCH_ARGS:
        return option
    if option.startswith("-") and not option.startswith("--"):
        forbidden_shorts = {short[1:] for short in FORBIDDEN_LAUNCH_SHORT_ARGS}
        for character in option[1:]:
            if character in forbidden_shorts:
                return f"-{character}"
    return None


def normalize_launch_child_args(child_args: list[str]) -> list[str]:
    forwarded = list(child_args)
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]
    for argument in forwarded:
        option = forbidden_launch_option(argument)
        if option is not None:
            fail(
                "launch argument is managed by nddev-kilo-cli-app and cannot be "
                f"overridden: {option}"
            )
    return forwarded


def launch_command_for_profile(executable: str, profile_id: str, forwarded: list[str]) -> list[str]:
    if profile_id == "full-auto":
        return [executable, "run", "--auto", *forwarded]
    return [executable, "run", *forwarded]


def launch_command_for_setup(executable: str, setup_id: str, forwarded: list[str]) -> list[str]:
    """Backward-compatible helper for callers that passed legacy setup ids."""
    if setup_id == DEFAULT_SETUP_ID:
        return launch_command_for_profile(executable, DEFAULT_PROFILE_ID, forwarded)
    fail(f"legacy setup cannot be launched: {setup_id}")


def launch(target: Path, child_args: list[str], *, timeout_seconds: int = 3600) -> int:
    if timeout_seconds <= 0:
        fail("launch timeout must be positive")
    target = resolve_target(target, create=False)
    forwarded = normalize_launch_child_args(child_args)
    with target_lock(target):
        stamp = require_active_clean_installed(target)
        installation = require_current_software(target)
        env = clean_launch_env(target)
        profile_id = stamp_profile_id(stamp) or DEFAULT_PROFILE_ID
        protection = protect_launch_handoff_paths(target, installation)
        try:
            command = launch_command_for_profile(
                revalidate_launch_executable(target, installation),
                profile_id,
                forwarded,
            )
            try:
                process = subprocess.Popen(
                    command,
                    env=env,
                    start_new_session=(os.name == "posix"),
                )
            except FileNotFoundError:
                fail("kilo executable disappeared before launch")
            try:
                return process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, 15)
                else:
                    process.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=5)
                if process.poll() is None:
                    if os.name == "posix":
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(process.pid, 9)
                    else:
                        process.kill()
                    process.wait()
                return 124
        finally:
            protection.restore()


def print_payload(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage isolated NDDev Kilo Code CLI setups.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="list available setups")
    list_parser.add_argument("--json", action="store_true")

    status_parser = subparsers.add_parser("status", help="show target status")
    status_parser.add_argument("--target", required=True)
    status_parser.add_argument("--json", action="store_true")

    plan_parser = subparsers.add_parser("plan", help="plan setup changes")
    plan_parser.add_argument("--setup", default=DEFAULT_SETUP_ID, choices=setup_ids())
    plan_parser.add_argument("--profile", default=DEFAULT_PROFILE_ID, choices=profile_ids())
    plan_parser.add_argument("--target", required=True)
    plan_parser.add_argument("--json", action="store_true")

    install_parser = subparsers.add_parser(
        "install", help="install a setup into an explicit target"
    )
    install_parser.add_argument("--setup", default=DEFAULT_SETUP_ID, choices=setup_ids())
    install_parser.add_argument("--profile", default=DEFAULT_PROFILE_ID, choices=profile_ids())
    install_parser.add_argument("--target", required=True)
    install_parser.add_argument("--json", action="store_true")

    switch_parser = subparsers.add_parser(
        "switch", help="switch an installed target to another setup/profile"
    )
    switch_parser.add_argument("--setup", default=DEFAULT_SETUP_ID, choices=setup_ids())
    switch_parser.add_argument("--profile", default=DEFAULT_PROFILE_ID, choices=profile_ids())
    switch_parser.add_argument("--target", required=True)
    switch_parser.add_argument("--json", action="store_true")

    migrate_parser = subparsers.add_parser(
        "migrate", help="migrate legacy managed state to nddev-builder setup/profile"
    )
    migrate_parser.add_argument("--profile", choices=profile_ids())
    migrate_parser.add_argument("--target", required=True)
    migrate_parser.add_argument("--json", action="store_true")

    restore_parser = subparsers.add_parser("restore", help="restore one target-bound backup slot")
    restore_parser.add_argument("--backup", required=True, type=int)
    restore_parser.add_argument("--target", required=True)
    restore_parser.add_argument("--json", action="store_true")

    remove_parser = subparsers.add_parser("remove", help="remove managed setup state")
    remove_parser.add_argument("--target", required=True)
    remove_parser.add_argument("--json", action="store_true")

    for command, help_text in (
        ("software-status", "inspect target-owned Kilo CLI software"),
        ("install-cli", "install pinned Kilo CLI package with isolated npm"),
        ("update-cli", "update or repair target-owned Kilo CLI package with isolated npm"),
        ("remove-cli", "remove target-owned Kilo CLI software"),
    ):
        command_parser = subparsers.add_parser(command, help=help_text)
        command_parser.add_argument("--target", required=True)
        command_parser.add_argument("--json", action="store_true")

    launch_parser = subparsers.add_parser(
        "launch", help="launch Kilo with isolated HOME and KILO_CONFIG"
    )
    launch_parser.add_argument("--target", required=True)
    launch_parser.add_argument("--timeout-seconds", type=int, default=3600)
    launch_parser.add_argument("child_args", nargs=argparse.REMAINDER)

    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def dispatch(args: argparse.Namespace) -> int:
    if args.command == "list":
        print_payload(list_payload(), as_json=args.json)
        return 0
    if args.command == "status":
        print_payload(status_payload(Path(args.target)), as_json=args.json)
        return 0
    if args.command == "plan":
        print_payload(plan_payload(Path(args.target), args.setup, args.profile), as_json=args.json)
        return 0
    if args.command == "install":
        print_payload(
            mutate_setup(Path(args.target), args.setup, args.profile, "install"), as_json=args.json
        )
        return 0
    if args.command == "switch":
        print_payload(
            mutate_setup(Path(args.target), args.setup, args.profile, "switch"), as_json=args.json
        )
        return 0
    if args.command == "migrate":
        print_payload(migrate_setup(Path(args.target), args.profile), as_json=args.json)
        return 0
    if args.command == "restore":
        print_payload(restore_setup(Path(args.target), args.backup), as_json=args.json)
        return 0
    if args.command == "remove":
        print_payload(remove_setup(Path(args.target)), as_json=args.json)
        return 0
    if args.command == "software-status":
        print_payload(
            software_status(resolve_target(Path(args.target), create=False)), as_json=args.json
        )
        return 0
    if args.command in {"install-cli", "update-cli"}:
        print_payload(
            install_or_update_cli(resolve_target(Path(args.target), create=False), args.command),
            as_json=args.json,
        )
        return 0
    if args.command == "remove-cli":
        print_payload(
            remove_cli(resolve_target(Path(args.target), create=False)), as_json=args.json
        )
        return 0
    if args.command == "launch":
        return launch(Path(args.target), args.child_args, timeout_seconds=args.timeout_seconds)
    fail(f"unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parse_args(arguments)
        return dispatch(args)
    except ManagerError as exc:
        if "--json" in arguments:
            print(json.dumps({"error": str(exc)}, indent=2, sort_keys=True), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
