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
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

ROOT = Path(__file__).resolve().parents[1]
CATALOG_ROOT = ROOT / "setups"
VERSION = (ROOT / "VERSION").read_text(encoding="ascii").strip()
PRODUCT_NAME = "nddev-kilo-cli-app"
STAMP_NAME = "NDDEV-KILO-CLI-SETUP.json"
BACKUP_NAME = "NDDEV-KILO-CLI-BACKUP.json"
STAMP_SCHEMA = 1
BACKUP_SCHEMA = 1
MAX_BACKUPS = 10
OWNER_FILE_MODE = 0o600
OWNER_DIR_MODE = 0o700
MANAGED_PAYLOAD_MAX_BYTES = 1024 * 1024
METADATA_MAX_BYTES = 256 * 1024
SOURCE_CONFIG = "config.json"
CONFIG = "xdg-config/kilo/kilo.jsonc"
BUILDER_INSTRUCTIONS = "instructions/nddev-builder.md"
BUILDER_SKILL = "skills/nddev-builder/SKILL.md"
BUILDER_FILES = (BUILDER_INSTRUCTIONS, BUILDER_SKILL)
MANAGED_FILES = (CONFIG, *BUILDER_FILES)
CONFIG_MANAGED_KEYS = (
    "permission",
    "sandbox",
    "default_agent",
    "agent",
    "skills",
    "command",
    "instructions",
    "experimental",
)
BUILDER_PROJECTION = "native-agent-skill-command-config"
KILO_CURRENT_VERSION = "7.4.16"
KILO_PACKAGE = "@kilocode/cli"
KILO_COMMAND = "kilo"
KILO_PACKAGE_SPEC = f"{KILO_PACKAGE}@{KILO_CURRENT_VERSION}"
KILO_PACKAGE_INTEGRITY = (
    "sha512-sOvq0HW6CZebCvGyUn0fTFj/1mX4nplpuoCJuqe6QjDUliLNYRC6ky9Rjl8kdT/"
    "nk0OGNO1kRaygEyc7+Htr2Q=="
)
KILO_PACKAGE_SHASUM = "c39f0f94f1cae2aeed28b4a5b5a952a5efab2b1d"
BUN_INSTALL_ARGV = ("add", "--global", "--exact", "--trust", KILO_PACKAGE_SPEC)
CONTROLLED_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
PROCESS_OUTPUT_MAX_BYTES = 256 * 1024
PROCESS_TIMEOUT_SECONDS = 180
STAGE_VERSION_PROBE_TIMEOUT_SECONDS = 60
SOFTWARE_TREE_MAX_BYTES = 1024 * 1024 * 1024
SOFTWARE_TREE_MAX_PATHS = 120000
SOFTWARE_GLOBAL_DIR_RELATIVE = Path("install") / "global"
KILO_PACKAGE_BIN_RELATIVE = (
    SOFTWARE_GLOBAL_DIR_RELATIVE / "node_modules" / "@kilocode" / "cli" / "bin" / "kilo"
)
KILO_NATIVE_BIN_RELATIVE = (
    SOFTWARE_GLOBAL_DIR_RELATIVE / "node_modules" / "@kilocode" / "cli" / "bin" / ".kilo"
)
SOFTWARE_MANIFEST_RELATIVE = Path("software") / "kilo-cli.json"
SOFTWARE_REPLACE_PATHS = (
    Path("bin") / KILO_COMMAND,
    SOFTWARE_GLOBAL_DIR_RELATIVE,
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
    "BUN_CONFIG_",
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
    "BUN_AUTH_TOKEN",
    "npm_config_userconfig",
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
    permission_profile: str
    builder_enabled: bool
    managed_files: tuple[str, ...]
    config: dict[str, Any]
    files: dict[str, bytes]


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


def setup_ids() -> list[str]:
    if not CATALOG_ROOT.is_dir():
        return []
    return sorted(path.name for path in CATALOG_ROOT.iterdir() if path.is_dir())


def load_setup(setup_id: str) -> Setup:
    if setup_id not in setup_ids():
        fail(f"unknown setup id: {setup_id}")
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
        permission_profile=str(metadata.get("permission_profile", "")),
        builder_enabled=bool(metadata.get("builder_enabled")),
        managed_files=managed_files,
        config=config,
        files=files,
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
    if create:
        transaction = DirectoryTransaction([])
        try:
            ensure_directory_chain(path.parent, transaction, "target parent")
            parent_info = require_directory(path.parent, "target parent")
            if not is_owner_private_directory(parent_info):
                fail("target parent must be private to the current user with mode 0700")
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
    return target.parent / f".{target.name}.nddev-kilo-cli-backups"


def lock_path(target: Path) -> Path:
    return target.parent / f".{target.name}.nddev-kilo-cli.lock"


@contextlib.contextmanager
def target_lock(target: Path, *, create_parent: bool = False) -> Iterator[DirectoryTransaction]:
    transaction = DirectoryTransaction([])
    if create_parent:
        ensure_directory_chain(target.parent, transaction, "canonical target parent")
    parent_info = require_directory(target.parent, "canonical target parent")
    if not is_owner_private_directory(parent_info):
        transaction.cleanup()
        fail("canonical target parent must be private to the current user with mode 0700")
    lock = lock_path(target)
    owner = lock / "owner.json"
    try:
        lock.mkdir(mode=OWNER_DIR_MODE)
        lock.chmod(OWNER_DIR_MODE)
        owner.write_bytes(
            canonical_json({"schema_version": 1, "pid": os.getpid(), "target": str(target)})
        )
        owner.chmod(OWNER_FILE_MODE)
    except FileExistsError:
        transaction.cleanup()
        fail(f"target is locked: {lock}")
    except BaseException:
        with contextlib.suppress(OSError):
            if path_exists_no_follow(owner) and not owner.is_symlink():
                owner.unlink()
            lock.rmdir()
        transaction.cleanup()
        raise
    failed = False
    try:
        yield transaction
    except BaseException:
        failed = True
        raise
    finally:
        try:
            owner = lock / "owner.json"
            if path_exists_no_follow(owner) and not owner.is_symlink():
                owner.unlink()
            lock.rmdir()
        except OSError as exc:
            raise ManagerError(f"target lock cleanup failed: {lock}") from exc
        if failed:
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


def managed_config_fragment(setup: Setup, target: Path) -> dict[str, Any]:
    fragment = extract_managed_config(setup.config)
    skills = copy.deepcopy(fragment.get("skills", {}))
    skills["paths"] = [str((target / "skills").resolve(strict=False))]
    fragment["skills"] = skills
    fragment["instructions"] = [str((target / BUILDER_INSTRUCTIONS).resolve(strict=False))]
    agent = copy.deepcopy(fragment.get("agent", {}))
    builder_agent = copy.deepcopy(agent.get("nddev-builder", {}))
    builder_agent["prompt"] = (
        "{file:" + str((target / BUILDER_INSTRUCTIONS).resolve(strict=False)) + "}"
    )
    requirements = copy.deepcopy(builder_agent.get("requirements", {}))
    requirements["skills"] = ["nddev-builder"]
    builder_agent["requirements"] = requirements
    agent["nddev-builder"] = builder_agent
    fragment["agent"] = agent
    command = copy.deepcopy(fragment.get("command", {}))
    builder_command = copy.deepcopy(command.get("nddev-builder", {}))
    builder_command["agent"] = "nddev-builder"
    command["nddev-builder"] = builder_command
    fragment["command"] = command
    return fragment


def digest_for_content(relative: str, content: bytes) -> str:
    if relative == CONFIG:
        config = parse_json_object(content, "managed config")
        return sha256_bytes(canonical_json(extract_managed_config(config)))
    return sha256_bytes(content)


def stamp_for_desired(
    target: Path, setup_id: str, desired: dict[str, bytes | None]
) -> dict[str, Any]:
    managed_records = []
    for relative in MANAGED_FILES:
        content = desired.get(relative)
        if content is None:
            fail(f"desired state omits managed file: {relative}")
        managed_records.append({"path": relative, "sha256": digest_for_content(relative, content)})
    return {
        "schema_version": STAMP_SCHEMA,
        "product_name": PRODUCT_NAME,
        "build_version": VERSION,
        "setup_id": setup_id,
        "canonical_target": str(target),
        "managed_files": managed_records,
        "builder": {
            "enabled": True,
            "projection": BUILDER_PROJECTION,
            "agent": "nddev-builder",
            "skill": "nddev-builder",
            "files": list(BUILDER_FILES),
        },
        "runtime": {
            "command": "kilo",
            "config_env": "KILO_CONFIG",
            "package": KILO_PACKAGE,
            "version": KILO_CURRENT_VERSION,
        },
    }


def desired_for_setup(target: Path, setup_id: str) -> dict[str, bytes | None]:
    setup = load_setup(setup_id)
    managed = managed_config_fragment(setup, target)
    merged = merge_config(current_config(target), managed)
    desired: dict[str, bytes | None] = {
        CONFIG: canonical_json(merged),
        BUILDER_INSTRUCTIONS: setup.files[BUILDER_INSTRUCTIONS],
        BUILDER_SKILL: setup.files[BUILDER_SKILL],
    }
    desired[STAMP_NAME] = canonical_json(stamp_for_desired(target, setup_id, desired))
    return desired


def read_stamp(target: Path) -> dict[str, Any] | None:
    path = stamp_path(target)
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    reject_relative_symlink_ancestors(target, STAMP_NAME)
    stamp = read_json_file(path, "target stamp", owner_only=True)
    if stamp.get("schema_version") != STAMP_SCHEMA:
        fail("target stamp schema is unsupported")
    if stamp.get("product_name") != PRODUCT_NAME:
        fail("target stamp belongs to another product")
    if stamp.get("canonical_target") != str(target):
        fail("target stamp is bound to a different target")
    if not isinstance(stamp.get("managed_files"), list):
        fail("target stamp has invalid managed file records")
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
    path = target / safe_relative_path(relative)
    path.parent.mkdir(mode=OWNER_DIR_MODE, parents=True, exist_ok=True)
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
    del expected
    require_directory(target, "target")
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
    pool.mkdir(mode=OWNER_DIR_MODE, exist_ok=True)
    require_directory(pool, "backup pool")
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
    slot_dir = backup_pool(target) / str(slot)
    require_directory(slot_dir, f"backup slot {slot}")
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
    setup_id = str(envelope["source_setup_id"])
    if setup_id not in setup_ids():
        fail(f"backup references unknown setup id: {setup_id}")
    desired: dict[str, bytes | None] = {}
    for record in envelope["managed_files"]:
        relative = str(record["path"])
        content = read_regular_file(
            backup_pool(target) / str(slot) / safe_relative_path(relative),
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
    desired[STAMP_NAME] = canonical_json(stamp_for_desired(target, setup_id, desired))
    return setup_id, desired


def desired_for_remove(target: Path) -> dict[str, bytes | None]:
    config = current_config(target)
    unmanaged = {
        key: copy.deepcopy(value) for key, value in config.items() if key not in CONFIG_MANAGED_KEYS
    }
    desired: dict[str, bytes | None] = {
        CONFIG: canonical_json(unmanaged) if unmanaged else None,
        BUILDER_INSTRUCTIONS: None,
        BUILDER_SKILL: None,
        STAMP_NAME: None,
    }
    return desired


def mutate_setup(target: Path, setup_id: str, operation: str) -> dict[str, Any]:
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
                if stamp["setup_id"] != setup_id:
                    backup_slot = backup_current_state(target, stamp)
        elif operation == "switch":
            stamp = require_clean_installed(target)
            if stamp["setup_id"] != setup_id:
                backup_slot = backup_current_state(target, stamp)
        else:
            fail(f"unsupported mutation operation: {operation}")
        desired = desired_for_setup(target, setup_id)
        snapshot = snapshot_paths(target, set(desired))
        try:
            replace_managed_state(target, desired, None)
        except Exception:
            restore_snapshot(target, snapshot)
            raise
    return {
        "operation": operation,
        "setup_id": setup_id,
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
            replace_managed_state(target, desired, None)
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
            replace_managed_state(target, desired, None)
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
    return {
        "installed": True,
        "target": str(target),
        "setup_id": stamp["setup_id"],
        "build_version": stamp["build_version"],
        "drift": drift,
        "builder": stamp.get("builder", {"enabled": False, "projection": BUILDER_PROJECTION}),
        "backup_pool": str(backup_pool(target)),
        "software": software,
    }


def plan_payload(target: Path, setup_id: str) -> dict[str, Any]:
    load_setup(setup_id)
    target = resolve_target(target, create=False)
    stamp = read_stamp(target) if target.exists() else None
    operation = "install" if stamp is None else "switch"
    return {
        "operation": operation,
        "setup_id": setup_id,
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
                "permission_profile": setup.permission_profile,
                "builder_enabled": setup.builder_enabled,
                "managed_files": list(setup.managed_files),
            }
        )
    return {"product_name": PRODUCT_NAME, "build_version": VERSION, "setups": setups}


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


def safe_child_base_environment(*, include_path: bool) -> dict[str, str]:
    env: dict[str, str] = {}
    if include_path:
        env["PATH"] = os.environ.get("PATH", CONTROLLED_PATH)
    for name in ("LANG", "LC_ALL", "LC_CTYPE", "TERM", "COLORTERM", "NO_COLOR", "CI"):
        value = os.environ.get(name)
        if value:
            env[name] = value
    return env


def install_stage_environment(stage_root: Path, live_stage: Path) -> dict[str, str]:
    home = stage_root / "home"
    tmp = stage_root / "tmp"
    xdg_config = stage_root / "xdg-config"
    xdg_data = stage_root / "xdg-data"
    xdg_state = stage_root / "xdg-state"
    xdg_cache = stage_root / "xdg-cache"
    bun_cache = stage_root / "bun-cache"
    for directory in (home, tmp, xdg_config, xdg_data, xdg_state, xdg_cache, bun_cache):
        directory.mkdir(mode=OWNER_DIR_MODE, parents=True, exist_ok=True)
        directory.chmod(OWNER_DIR_MODE)
    env = safe_child_base_environment(include_path=True)
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "TMPDIR": str(tmp),
            "XDG_CONFIG_HOME": str(xdg_config),
            "XDG_DATA_HOME": str(xdg_data),
            "XDG_STATE_HOME": str(xdg_state),
            "XDG_CACHE_HOME": str(xdg_cache),
            "BUN_INSTALL_GLOBAL_DIR": str(live_stage / SOFTWARE_GLOBAL_DIR_RELATIVE),
            "BUN_INSTALL_BIN": str(live_stage / "bin"),
            "BUN_INSTALL_CACHE_DIR": str(bun_cache),
        }
    )
    return env


def native_wrapper_bytes() -> bytes:
    return (
        "#!/bin/sh\n"
        "set -eu\n"
        'case "$0" in\n'
        "  */*) self_dir=${0%/*} ;;\n"
        "  *) self_dir=. ;;\n"
        "esac\n"
        'package_bin_dir="$self_dir/../install/global/node_modules/@kilocode/cli/bin"\n'
        'if [ -f "$package_bin_dir/tree-sitter/tree-sitter.wasm" ]; then\n'
        '  export KILO_TREE_SITTER_WASM_DIR="$package_bin_dir/tree-sitter"\n'
        "fi\n"
        'exec "$package_bin_dir/.kilo" "$@"\n'
    ).encode("utf-8")


def target_runtime_paths(target: Path) -> dict[str, Path]:
    return {
        "HOME": target / "home",
        "TMPDIR": target / "tmp",
        "XDG_CONFIG_HOME": target / "xdg-config",
        "XDG_DATA_HOME": target / "xdg-data",
        "XDG_STATE_HOME": target / "xdg-state",
        "XDG_CACHE_HOME": target / "xdg-cache",
    }


def launch_environment(target: Path) -> dict[str, str]:
    paths = target_runtime_paths(target)
    for path in paths.values():
        path.mkdir(mode=OWNER_DIR_MODE, parents=True, exist_ok=True)
        path.chmod(OWNER_DIR_MODE)
    env = safe_child_base_environment(include_path=False)
    for key, path in paths.items():
        env[key] = str(path)
    env["KILO_CONFIG"] = str((target / CONFIG).resolve(strict=False))
    return env


def find_bun_executable() -> str:
    candidate = shutil.which("bun", path=os.environ.get("PATH", ""))
    if candidate is None:
        fail("bun executable not found on PATH")
    return candidate


def bounded_process(
    command: list[str], *, cwd: Path, env: dict[str, str], timeout: int
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError:
        fail(f"process executable not found: {command[0]}")
    except subprocess.TimeoutExpired:
        fail(f"process timed out after {timeout} seconds: {command[0]}")
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
        fail("Kilo CLI package postinstall contract is invalid")
    return metadata


def iter_software_tree_paths(root: Path) -> list[Path]:
    paths = [Path("bin") / KILO_COMMAND, SOFTWARE_GLOBAL_DIR_RELATIVE]
    install_root = root / SOFTWARE_GLOBAL_DIR_RELATIVE
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
    return {
        "tree_digest": sha256_bytes(canonical_json(records)),
        "tree_bytes": byte_counter["value"],
        "tree_paths": len(records),
        "package_name": metadata["name"],
        "version": metadata["version"],
        "entrypoint_sha256": digest_regular_file(
            resolved_executable_path(
                root / "bin" / KILO_COMMAND,
                root,
                "Kilo CLI executable",
            ),
            "Kilo CLI executable",
            {"value": 0},
        ),
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
        "install_method": "bun-global-trusted-exact",
        "package_version": KILO_CURRENT_VERSION,
        "package_integrity": KILO_PACKAGE_INTEGRITY,
        "package_shasum": KILO_PACKAGE_SHASUM,
        "bun_argv": list(BUN_INSTALL_ARGV),
        "executable": f"bin/{KILO_COMMAND}",
        "entrypoint_kind": "target-owned-native-wrapper",
        "package_bin": str(KILO_PACKAGE_BIN_RELATIVE),
        "native_executable": str(KILO_NATIVE_BIN_RELATIVE),
        "install_root": str(SOFTWARE_GLOBAL_DIR_RELATIVE),
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
        "package": manifest.get("package"),
        "install_method": manifest.get("install_method"),
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
    if relative == SOFTWARE_GLOBAL_DIR_RELATIVE:
        info = require_directory(destination, f"existing software directory {relative}")
        if not is_owner_private_directory(info):
            fail(f"existing software directory {relative} must be private to the current user")
        return
    if relative == SOFTWARE_MANIFEST_RELATIVE:
        require_regular_file(
            destination,
            f"existing software manifest {relative}",
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
            fail("installed Kilo CLI did not validate as the pinned Bun package")
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


def observed_kilo_version(executable: Path, target: Path) -> str:
    require_safe_executable(executable, target, "staged Kilo CLI executable")
    completed = bounded_process(
        [str(executable), "--version"],
        cwd=target,
        env=launch_environment(target),
        timeout=STAGE_VERSION_PROBE_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        fail(f"Kilo CLI version smoke failed with exit {completed.returncode}")
    text = "\n".join((completed.stdout, completed.stderr)).strip()
    if KILO_CURRENT_VERSION not in text:
        fail(f"Kilo CLI returned an invalid version string: {text!r}")
    return KILO_CURRENT_VERSION


def materialize_stage_entrypoint(live_stage: Path) -> None:
    entrypoint = live_stage / "bin" / KILO_COMMAND
    package_bin = live_stage / KILO_PACKAGE_BIN_RELATIVE
    native_bin = live_stage / KILO_NATIVE_BIN_RELATIVE
    require_safe_executable(package_bin, live_stage, "staged Kilo CLI package bin")
    require_safe_executable(native_bin, live_stage, "staged Kilo CLI native executable")
    info = entrypoint.lstat()
    if not stat.S_ISLNK(info.st_mode):
        fail("staged Kilo CLI executable must be the Bun global symlink")
    resolved = resolve_target_owned_symlink(entrypoint, live_stage, "staged Kilo CLI executable")
    if resolved != package_bin:
        fail("staged Kilo CLI executable does not point at the official package bin")
    entrypoint.unlink()
    entrypoint.write_bytes(native_wrapper_bytes())
    entrypoint.chmod(0o700)
    require_safe_executable(entrypoint, live_stage, "staged Kilo CLI executable")


def run_bun_install(stage_root: Path, live_stage: Path) -> None:
    bun = find_bun_executable()
    env = install_stage_environment(stage_root, live_stage)
    completed = bounded_process(
        [bun, *BUN_INSTALL_ARGV],
        cwd=stage_root,
        env=env,
        timeout=PROCESS_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        fail(
            f"bun install for Kilo CLI failed with exit {completed.returncode}: {completed.stderr.strip()}"
        )
    chmod_private_tree(live_stage)
    materialize_hardlinked_regular_files(live_stage)
    materialize_stage_entrypoint(live_stage)
    observed = observed_kilo_version(live_stage / "bin" / KILO_COMMAND, live_stage)
    if observed != KILO_CURRENT_VERSION:
        fail(f"Bun produced Kilo CLI {observed}, expected {KILO_CURRENT_VERSION}")


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
        validate_existing_software_surface(target)
        if preflight["installed"] and preflight["current"]:
            return {
                "schema_version": 1,
                "command": command,
                "target": str(target),
                "changed": False,
                "version": preflight["version"],
                "executable": preflight["executable"],
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
                    }
            staging = Path(
                tempfile.mkdtemp(dir=target.parent, prefix=f".{target.name}.nddev-kilo-cli-stage.")
            )
            staging.chmod(OWNER_DIR_MODE)
            live_stage = staging / "live"
            live_stage.mkdir(mode=OWNER_DIR_MODE)
            run_bun_install(staging, live_stage)
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
    validate_existing_software_surface(target)
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


def launch_command_for_setup(executable: str, setup_id: str, forwarded: list[str]) -> list[str]:
    if setup_id == "full-auto":
        return [executable, "run", "--auto", *forwarded]
    return [executable, "run", *forwarded]


def launch(target: Path, child_args: list[str], *, timeout_seconds: int = 3600) -> int:
    if timeout_seconds <= 0:
        fail("launch timeout must be positive")
    target = resolve_target(target, create=False)
    with target_lock(target):
        stamp = require_clean_installed(target)
        installation = require_current_software(target)
        env = clean_launch_env(target)
        executable = str(installation["executable"])
        setup_id = str(stamp["setup_id"])
    forwarded = normalize_launch_child_args(child_args)
    command = launch_command_for_setup(executable, setup_id, forwarded)
    try:
        return subprocess.run(command, env=env, check=False, timeout=timeout_seconds).returncode
    except FileNotFoundError:
        fail("kilo executable disappeared before launch")
    except subprocess.TimeoutExpired:
        return 124


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
    plan_parser.add_argument("--setup", required=True, choices=setup_ids())
    plan_parser.add_argument("--target", required=True)
    plan_parser.add_argument("--json", action="store_true")

    install_parser = subparsers.add_parser(
        "install", help="install a setup into an explicit target"
    )
    install_parser.add_argument("--setup", required=True, choices=setup_ids())
    install_parser.add_argument("--target", required=True)
    install_parser.add_argument("--json", action="store_true")

    switch_parser = subparsers.add_parser(
        "switch", help="switch an installed target to another setup"
    )
    switch_parser.add_argument("--setup", required=True, choices=setup_ids())
    switch_parser.add_argument("--target", required=True)
    switch_parser.add_argument("--json", action="store_true")

    restore_parser = subparsers.add_parser("restore", help="restore one target-bound backup slot")
    restore_parser.add_argument("--backup", required=True, type=int)
    restore_parser.add_argument("--target", required=True)
    restore_parser.add_argument("--json", action="store_true")

    remove_parser = subparsers.add_parser("remove", help="remove managed setup state")
    remove_parser.add_argument("--target", required=True)
    remove_parser.add_argument("--json", action="store_true")

    for command, help_text in (
        ("software-status", "inspect target-owned Kilo CLI software"),
        ("install-cli", "install pinned Kilo CLI package with Bun"),
        ("update-cli", "update or repair target-owned Kilo CLI package with Bun"),
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
        print_payload(plan_payload(Path(args.target), args.setup), as_json=args.json)
        return 0
    if args.command == "install":
        print_payload(mutate_setup(Path(args.target), args.setup, "install"), as_json=args.json)
        return 0
    if args.command == "switch":
        print_payload(mutate_setup(Path(args.target), args.setup, "switch"), as_json=args.json)
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
