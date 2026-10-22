"""Private review bundles are local-only, exclusive and fail closed."""

from __future__ import annotations

import json
import subprocess

import pytest

from evals import private_review


def _isolated_repo(monkeypatch, tmp_path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    repo = outputs / "repo"
    repo.mkdir()
    monkeypatch.setattr(private_review, "REPO_ROOT", repo)
    return repo


def test_private_review_writes_new_json_under_fixed_work_directory(monkeypatch, tmp_path) -> None:
    repo = _isolated_repo(monkeypatch, tmp_path)
    monkeypatch.setattr(private_review, "_bundle_name", lambda: "fixed.json")
    payload = {"format_version": 1, "cases": [{"case": 2, "status": "REVIEW"}]}

    target = private_review.write_private_review(payload)

    assert target == tmp_path / "work" / "private-review" / "fixed.json"
    assert not (repo / "work").exists()
    assert json.loads(target.read_text(encoding="utf-8")) == payload
    with pytest.raises(FileExistsError):
        private_review.write_private_review({"format_version": 2})
    assert json.loads(target.read_text(encoding="utf-8")) == payload


def test_unserializable_review_never_creates_a_json_file(monkeypatch, tmp_path) -> None:
    _isolated_repo(monkeypatch, tmp_path)

    with pytest.raises(TypeError):
        private_review.write_private_review({"cases": {"not-json"}})

    assert not list((tmp_path / "work" / "private-review").glob("*.json"))


def test_private_review_rejects_work_symlink_outside_repository(monkeypatch, tmp_path) -> None:
    _isolated_repo(monkeypatch, tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    try:
        (tmp_path / "work").symlink_to(elsewhere, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("O host não permite criar symlink de diretório")

    with pytest.raises(ValueError, match="work"):
        private_review.prepare_private_review()
    assert not list(elsewhere.iterdir())


def test_private_review_falls_back_to_ignored_repo_work_in_a_normal_clone(
    monkeypatch, tmp_path
) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True, capture_output=True)
    (repo / ".gitignore").write_text("work/\n", encoding="utf-8")
    monkeypatch.setattr(private_review, "REPO_ROOT", repo)

    target = private_review.write_private_review({"format_version": 1})

    assert target.parent == repo / "work" / "private-review"


def test_private_review_rejects_unignored_repo_work_before_writing(monkeypatch, tmp_path) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True, capture_output=True)
    monkeypatch.setattr(private_review, "REPO_ROOT", repo)

    with pytest.raises(ValueError, match="Git"):
        private_review.prepare_private_review()
    with pytest.raises(ValueError, match="Git"):
        private_review.write_private_review({"format_version": 1})

    assert not list((repo / "work" / "private-review").glob("*.json"))


def test_database_hash_is_read_in_chunks_without_exposing_file_contents(tmp_path) -> None:
    source = tmp_path / "synthetic.db"
    source.write_bytes(b"abc")

    assert private_review.sha256_file(source) == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_git_state_reports_commit_and_dirty_flag_without_diff(monkeypatch, tmp_path) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True, capture_output=True)
    tracked = repo / "tracked.txt"
    tracked.write_text("before", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Synthetic Tester",
            "-c",
            "user.email=tester@example.invalid",
            "commit",
            "-qm",
            "seed",
        ],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(private_review, "REPO_ROOT", repo)

    commit, dirty = private_review.git_state()
    assert len(commit) == 40
    assert dirty is False

    tracked.write_text("after", encoding="utf-8")
    assert private_review.git_state() == (commit, True)
