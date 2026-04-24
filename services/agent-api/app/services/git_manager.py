"""Git manager — handles git operations for generated websites."""

import logging
import os
import shutil
from pathlib import Path
from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()


def get_project_path(project_slug: str) -> Path:
    return Path(settings.generated_sites_path) / project_slug


def init_project_repo(project_slug: str) -> str:
    """Initialize a git repo for a new project. Returns the repo path."""
    import git

    project_path = get_project_path(project_slug)
    project_path.mkdir(parents=True, exist_ok=True)

    repo = git.Repo.init(project_path)
    # Create .gitignore
    gitignore = project_path / ".gitignore"
    gitignore.write_text("__pycache__/\n.DS_Store\nnode_modules/\n")

    repo.index.add([".gitignore"])
    repo.index.commit("Initial project setup")

    logger.info(f"Initialized git repo at {project_path}")
    return str(project_path)


def commit_revision(
    project_slug: str,
    revision_number: int,
    message: str,
    files: list[dict] | None = None,
) -> str:
    """Write files and commit a new revision. Returns commit hash."""
    import git

    project_path = get_project_path(project_slug)
    repo = git.Repo(project_path)

    # Write files if provided
    if files:
        for file_info in files:
            file_path = project_path / file_info["path"]
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(file_info["content"], encoding="utf-8")

    # Stage all changes
    repo.git.add(A=True)

    # Commit
    commit_message = f"[rev-{revision_number}] {message}"
    commit = repo.index.commit(commit_message)

    logger.info(f"Committed revision {revision_number} for {project_slug}: {commit.hexsha[:8]}")
    return commit.hexsha


def create_revision_branch(project_slug: str, revision_number: int) -> str:
    """Create a branch for a new revision. Returns branch name."""
    import git

    project_path = get_project_path(project_slug)
    repo = git.Repo(project_path)

    branch_name = f"revision-{revision_number}"
    repo.create_head(branch_name)
    repo.heads[branch_name].checkout()

    logger.info(f"Created branch {branch_name} for {project_slug}")
    return branch_name


def merge_revision(project_slug: str, revision_number: int) -> str:
    """Merge a revision branch into main. Returns merge commit hash."""
    import git

    project_path = get_project_path(project_slug)
    repo = git.Repo(project_path)

    branch_name = f"revision-{revision_number}"
    main = repo.heads["master"] if "master" in [h.name for h in repo.heads] else repo.heads["main"]
    main.checkout()
    repo.git.merge(branch_name, m=f"Merge revision {revision_number}")

    return repo.head.commit.hexsha


def rollback_to_revision(project_slug: str, target_commit_hash: str) -> str:
    """Rollback to a specific commit. Returns new commit hash."""
    import git

    project_path = get_project_path(project_slug)
    repo = git.Repo(project_path)

    repo.git.revert("--no-commit", f"{target_commit_hash}..HEAD")
    commit = repo.index.commit(f"Rollback to {target_commit_hash[:8]}")

    logger.info(f"Rolled back {project_slug} to {target_commit_hash[:8]}")
    return commit.hexsha


def diff_commits(project_slug: str, base_hash: str, head_hash: str) -> dict:
    """Return a unified diff between two commits and a list of changed files."""
    import git

    project_path = get_project_path(project_slug)
    repo = git.Repo(project_path)
    try:
        patch = repo.git.diff(base_hash, head_hash, "--", ".")
    except Exception as exc:
        logger.warning("git diff failed: %s", exc)
        patch = ""
    try:
        changed = repo.git.diff(base_hash, head_hash, "--name-status").splitlines()
    except Exception:
        changed = []
    files: list[dict] = []
    for line in changed:
        parts = line.split("\t")
        if len(parts) >= 2:
            files.append({"status": parts[0], "path": parts[-1]})
    return {"patch": patch, "files": files}


def get_project_files(project_slug: str) -> list[dict]:
    """Read all tracked files from a project. Returns [{path, content}]."""
    project_path = get_project_path(project_slug)
    files = []

    for file_path in project_path.rglob("*"):
        if file_path.is_file() and ".git" not in file_path.parts:
            rel_path = file_path.relative_to(project_path)
            try:
                content = file_path.read_text(encoding="utf-8")
                files.append({"path": str(rel_path), "content": content})
            except UnicodeDecodeError:
                files.append({"path": str(rel_path), "content": "[binary file]"})

    return files


def copy_revision_for_preview(project_slug: str, revision_id: str) -> str:
    """Copy current project state to a preview directory. Returns preview path."""
    project_path = get_project_path(project_slug)
    preview_path = Path(settings.generated_sites_path) / f"{project_slug}" / "preview" / revision_id

    if preview_path.exists():
        shutil.rmtree(preview_path)

    # Copy only non-git files
    shutil.copytree(
        project_path,
        preview_path,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules", "preview"),
    )

    logger.info(f"Created preview at {preview_path}")
    return str(preview_path)
