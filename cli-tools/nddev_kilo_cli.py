#!/usr/bin/env python3
"""Target-explicit setup manager for Kilo Code CLI.

The manager owns only the selected Kilo configuration keys, the native NDDev
builder projection, target-bound metadata, and target-bound backups under an
explicit absolute target. It never infers or mutates the caller's live
``~/.config/kilo`` state.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
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
CONFIG = "config.json"
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
    "OPENAI_API_KEY",
}


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


def fail(message: str) -> NoReturn:
    raise ManagerError(message)


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def identity_of(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


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


def require_regular_file(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError:
        fail(f"{label} is missing")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        fail(f"{label} must be a regular non-symlink file")
    if info.st_nlink != 1:
        fail(f"{label} must not have hard-link aliases")
    return info


def read_regular_file(
    path: Path,
    label: str,
    *,
    max_bytes: int = MANAGED_PAYLOAD_MAX_BYTES,
) -> bytes:
    before = require_regular_file(path, label)
    if before.st_size > max_bytes:
        fail(f"{label} exceeds the {max_bytes}-byte size limit")
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
    final = require_regular_file(path, label)
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
    path: Path, label: str, *, max_bytes: int = METADATA_MAX_BYTES
) -> dict[str, Any]:
    return parse_json_object(read_regular_file(path, label, max_bytes=max_bytes), label)


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
    config = read_json_file(setup_root / CONFIG, f"setup {setup_id} config")
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
    path = path.resolve(strict=False)
    reject_absolute_symlink_ancestors(path)
    if create:
        path.mkdir(mode=OWNER_DIR_MODE, parents=True, exist_ok=True)
        os.chmod(path, OWNER_DIR_MODE)
    else:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return path
        if stat.S_ISLNK(info.st_mode):
            fail("target must not be a symlink")
        if not stat.S_ISDIR(info.st_mode):
            fail("target must be a real directory")
    require_directory(path, "target")
    return path


def backup_pool(target: Path) -> Path:
    return target.parent / f".{target.name}.nddev-kilo-cli-backups"


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
    stamp = read_json_file(path, "target stamp")
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
        path, f"managed file {relative}", max_bytes=MANAGED_PAYLOAD_MAX_BYTES
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
    target = resolve_target(target, create=True)
    backup_slot: int | None = None
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
    target = resolve_target(target, create=True)
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
    stamp = read_stamp(target)
    if stamp is None:
        return {
            "installed": False,
            "target": str(target),
            "builder": {"enabled": False, "projection": BUILDER_PROJECTION},
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


def clean_launch_env(target: Path) -> dict[str, str]:
    runtime = target / ".nddev-runtime"
    paths = {
        "HOME": runtime / "home",
        "XDG_CONFIG_HOME": runtime / "xdg-config",
        "XDG_DATA_HOME": runtime / "xdg-data",
        "XDG_STATE_HOME": runtime / "xdg-state",
        "XDG_CACHE_HOME": runtime / "xdg-cache",
    }
    for path in paths.values():
        path.mkdir(mode=OWNER_DIR_MODE, parents=True, exist_ok=True)
    env: dict[str, str] = {}
    for key in ("PATH", "LANG", "LC_ALL", "TERM", "COLORTERM", "NO_COLOR", "CI"):
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    for key, path in paths.items():
        env[key] = str(path)
    env["KILO_CONFIG"] = str((target / CONFIG).resolve(strict=False))
    for key in list(env):
        if key in SECRET_ENV_NAMES or any(key.startswith(prefix) for prefix in SECRET_ENV_PREFIXES):
            if key not in {"KILO_CONFIG"}:
                env.pop(key, None)
    return env


def launch(target: Path, child_args: list[str], *, timeout_seconds: int = 3600) -> int:
    if timeout_seconds <= 0:
        fail("launch timeout must be positive")
    target = resolve_target(target, create=False)
    require_clean_installed(target)
    env = clean_launch_env(target)
    executable = shutil.which("kilo", path=env.get("PATH", ""))
    if executable is None:
        fail("kilo executable was not found on PATH")
    forwarded = list(child_args)
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]
    command = [executable, "run", "--auto", *forwarded]
    try:
        return subprocess.run(command, env=env, check=False, timeout=timeout_seconds).returncode
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
