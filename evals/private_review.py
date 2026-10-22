"""Opt-in, local-only evidence files for the live evaluation."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _private_directory() -> Path:
    root = REPO_ROOT.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("A raiz do repositório está indisponível.")
    # Codex keeps deliverables under outputs/. Evidence belongs in the task's
    # sibling work/; a standalone clone uses its ignored repository work/.
    work = (
        root.parent.parent / "work" if root.parent.name.casefold() == "outputs" else root / "work"
    )
    work.mkdir(exist_ok=True)
    if work.resolve(strict=True) != work:
        raise ValueError("O diretório work não pode apontar para fora da raiz prevista.")
    review = work / "private-review"
    review.mkdir(exist_ok=True)
    if review.resolve(strict=True) != review:
        raise ValueError("O diretório privado não pode apontar para fora de work.")
    if root.parent.name.casefold() != "outputs":
        ignored = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "-q", "--", "work/private-review/"],
            check=False,
            capture_output=True,
            timeout=10,
        )
        if ignored.returncode != 0:
            raise ValueError("O diretório privado não está ignorado pelo Git.")
    return review


def prepare_private_review() -> None:
    """Verify the fixed local destination before starting paid work."""
    _private_directory()


def git_state() -> tuple[str, bool]:
    """Return commit and dirty flag, never Git's potentially sensitive diff."""
    root = REPO_ROOT.resolve(strict=True)
    commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    if len(commit) < 40 or any(character not in "0123456789abcdef" for character in commit):
        raise ValueError("O commit Git não pôde ser identificado.")
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=normal"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout
    return commit, bool(status.strip())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _bundle_name() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"review-{stamp}-{secrets.token_hex(8)}.json"


def write_private_review(payload: dict[str, object]) -> Path:
    """Write a new JSON file under ignored work/ without replacing prior evidence."""
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    directory = _private_directory()
    name = _bundle_name()
    if Path(name).name != name or not name.endswith(".json"):
        raise ValueError("Nome de pacote inválido.")
    target = directory / name
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
            destination.write(serialized)
            destination.flush()
            os.fsync(destination.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target
