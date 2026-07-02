import logging
from git import Repo
import shutil
from git import Repo, GitCommandError
from helper import *
from pathlib import Path
from commitMetadata import *

logger = logging.getLogger(__name__)
logging.getLogger("git").setLevel(logging.INFO)


def getGitBranchNameFromSvnPath(
    path: str, trunkLocation: str, branchLocation: str, tagLocation: str
):
    # Normalize path
    path = ensure_slashes(path)

    if path.find(trunkLocation) >= 0:
        return "trunk"
    if path.find(branchLocation) >= 0:
        # Get the name of the first folder after the prefix
        return substrUntilFirstOccurance(
            path.removeprefix(ensure_slashes(branchLocation)), "/"
        )
    if path.find(tagLocation) >= 0:
        return "tag/" + substrUntilFirstOccurance(
            path.removeprefix(ensure_slashes(tagLocation)), "/"
        )


def create_new_git_repo(
    git_dir: str,
    initial_branch: str = "trunk",
) -> Repo:
    """
    Initializes a Git repository whose .git directory is located in 'git_dir',
    but whose working tree is elsewhere (your SVN folder).

    - git_dir: e.g. ".localWCs/git"
    - worktree: e.g. ".localWCs/svn"
    - HEAD points to 'initial_branch' (unborn until the first commit comes)
    - .svn/ is ignored in this repository
    """
    git_dir_path = Path(git_dir).expanduser().resolve()

    # Delete existing git directory if it exists
    # TODO: Make this configurable
    if git_dir_path.is_dir():
        logger.debug(
            f"[git] Working directory {git_dir_path} already exists. Deleting..."
        )
        shutil.rmtree(git_dir_path)
    logger.debug("[git] Initialising new local repo...")
    git_dir_path.mkdir(parents=True, exist_ok=True)
    repo = Repo.init(git_dir_path)

    # Force the HEAD of the branch to the initial branch, eventhough
    # there are no commits yet
    ref = f"refs/heads/{initial_branch}"
    repo.git.symbolic_ref("HEAD", ref)

    # Add .svn/ to the exclude file, to prevent git from tracking the
    # svn folder, without having to mess with the files in the working tree
    exclude_path = Path(repo.git_dir) / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    with exclude_path.open("a", encoding="utf-8") as f:
        f.write("\n.svn/\n")

    return repo


def switch_branch(
    repo: Repo, branch_name: str, create_if_missing: bool = False
) -> None:
    ref = f"refs/heads/{branch_name}"

    # Case 1: Repo has no commits yet -> unborn HEAD
    if not repo.head.is_valid():
        # Just set HEAD symbolism, no branch commit exists yet
        repo.git.symbolic_ref("HEAD", ref)
        return

    # Case 2: Repo already has commits
    # Does the branch already exist?
    if branch_name in {h.name: h for h in repo.heads}:
        # Branch exists -> just point HEAD to this branch
        repo.git.symbolic_ref("HEAD", ref)
        return

    # Branch does not exist yet
    if not create_if_missing:
        raise GitCommandError(
            "switch_branch",
            f"Branch '{branch_name}' does not exist and create_if_missing=False.",
        )

    # Create new branch ref at current HEAD commit
    elif repo.head.is_detached:
        raise GitCommandError(
            "switch_branch", "Local Repo is corrupted, HEAD is detached"
        )
    else:
        # HEAD already points to a branch; create new branch from current commit
        repo.create_head(branch_name)

    # Set HEAD to the new branch
    repo.git.symbolic_ref("HEAD", ref)


def commit(repo: Repo, meta: CommitMetadata, worktreePath: Path) -> Commit:
    """
    Creates a commit in the given repository on the current branch.

    - 'worktree' is the directory whose contents should be in the commit
      (e.g., an SVN working copy of a specific branch).
    - Nothing is modified in the worktree; Git only reads files.
    """
    if not worktreePath.is_dir():
        raise ValueError(f"Worktree does not exist: {worktreePath}")

    # 1. Register this worktree in the config
    #    (writes ONLY to .git/config, does not modify files in the worktree)
    repo.git.config("core.worktree", str(worktreePath))

    # 2. Stage all changes from this worktree (respects .git/info/exclude -> .svn)
    repo.git.add(A=True)

    # 3. Author / Committer
    author = Actor(meta.author_name, meta.author_email)

    committer_name = meta.committer_name or meta.author_name
    committer_email = meta.committer_email or meta.author_email
    committer = Actor(committer_name, committer_email)

    # 4. Date information: at least commit_date is available
    #    author_date: if None, we use commit_date
    author_date = meta.author_date or meta.commit_date
    commit_date = meta.commit_date or meta.author_date
    if author_date is None or commit_date is None:
        raise ValueError("Commit metadata must contain at least one timestamp")

    author_date_str = CommitMetadata.to_git_date(author_date)
    commit_date_str = CommitMetadata.to_git_date(commit_date)

    # 5. Create commit
    new_commit = repo.index.commit(
        meta.message,
        author=author,
        committer=committer,
        author_date=author_date_str,
        commit_date=commit_date_str,
        skip_hooks=True,
    )

    return new_commit


def clear_worktree_config(repo: Repo) -> None:
    """
    Removes the temporary core.worktree configuration from the Git repository.
    This resets the repository back to its normal working-tree behavior.
    """
    try:
        repo.git.config("--unset", "core.worktree")
    except GitCommandError:
        # core.worktree may not be set in some runs anymore.
        pass


def rename_branch(
    repo: Repo,
    old_name: str,
    new_name: str,
) -> None:
    """
    Renames a Git branch from old_name to new_name.
    """
    repo.git.branch("-M", old_name, new_name)


def delete_branch(
    repo: Repo,
    branch_name: str,
) -> None:
    """
    Deletes a Git branch.
    """
    repo.git.update_ref("-d", f"refs/heads/{branch_name}")


def set_git_tag(repo: Repo, tag_name: str, commit: Commit) -> None:
    """
    Sets a Git tag on the specified commit.
    """
    repo.create_tag(tag_name, commit.hexsha, force=True)


def delete_git_tag(repo: Repo, tag_name: str) -> None:
    """
    Deletes a Git tag.
    """
    tag_ref = next((tag for tag in repo.tags if tag.name == tag_name), None)
    if tag_ref is not None:
        repo.delete_tag(tag_ref)
