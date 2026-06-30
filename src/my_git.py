import logging
import shutil
from pathlib import Path, PurePath

from git import Actor, Repo, GitCommandError
from git.exc import InvalidGitRepositoryError
from git.objects import Commit

from branchChange import BranchChangeType
from commitMetadata import CommitMetadata
from helper import ensure_slashes, substrUntilFirstOccurance
from my_svn import findAffectedBranches, getChangesInRevision, getRevisionCount, switchRevision

logger = logging.getLogger(__name__)
logging.getLogger("git").setLevel(logging.INFO)


def _revision_matches_git_state(
    repo: Repo,
    svnLocalClient,
    revision: int,
    config,
) -> bool:
    switchRevision(svnLocalClient, revision)

    fileChanges = getChangesInRevision(svnLocalClient, revision)
    branchChanges = findAffectedBranches(
        fileChanges,
        PurePath(config["svn"]["trunkFolder"]),
        PurePath(config["svn"]["branchFolder"]),
        PurePath(config["svn"]["tagFolder"]),
        map(PurePath, config["svn"]["ignoredFolders"]),
    )

    if len(branchChanges) == 0:
        return True

    for branch_change in branchChanges:
        # A commit is only acceptable if the SVN revision is also visible on
        # the affected branch or tag.
        branch_name = f"tag/{branch_change.name}" if branch_change.isTag else branch_change.name

        if branch_change.type in (BranchChangeType.ADDED, BranchChangeType.MODIFIED):
            marker = f"^r{revision}:"
            try:
                has_branch_commit = next(
                    repo.iter_commits(branch_name, grep=marker), None
                ) is not None
            except (GitCommandError, ValueError, StopIteration):
                has_branch_commit = False

            if not has_branch_commit:
                return False

            if branch_change.isTag:
                tag_ref = next((tag for tag in repo.tags if tag.name == branch_change.name), None)
                if tag_ref is None or not str(tag_ref.commit.message).startswith(f"r{revision}:"):
                    return False

        elif branch_change.type == BranchChangeType.DELETED:
            deleted_branch_name = f"del/{branch_name}@{revision}"
            branch_exists = any(head.name == branch_name for head in repo.heads)
            deleted_branch_exists = any(head.name == deleted_branch_name for head in repo.heads)

            if config["git"]["keepDeletedBranches"]:
                if not deleted_branch_exists:
                    return False
            elif branch_exists:
                return False

            if branch_change.isTag:
                tag_exists = any(tag.name == branch_change.name for tag in repo.tags)
                if config["git"]["keepDeletedTags"]:
                    if not tag_exists:
                        return False
                elif tag_exists:
                    return False

    return True


def verify_git_repo_against_svn(
    repo: Repo,
    svnLocalClient,
    config,
) -> int:
    """
    Verifies an existing Git repo against the SVN history.

    Returns the first SVN revision that is not yet fully mapped in the Git repo.
    If everything is present, returns svnRevisionCount + 1.

    Returns -1 if the git repo does not match the SVN history
    """
    svnRevisionCount = getRevisionCount(svnLocalClient)
    resume_revision = svnRevisionCount + 1

    for revision in range(1, svnRevisionCount + 1):
        if revision in config["svn"]["skipRevisions"]:
            continue

        if not _revision_matches_git_state(repo, svnLocalClient, revision, config):
            resume_revision = -1
            break

    return resume_revision


def prepare_git_repo(
    svnLocalClient,
    config,
    initial_branch: str = "trunk",
) -> tuple[Repo, int]:
    """
    Creates or loads the Git repo and returns the next SVN revision
    from which the actual mirror run should continue.
    """
    git_dir = config["localFiles"]["gitWorkingDir"]
    git_dir_path = Path(git_dir).expanduser().resolve()
    remote_url = config["git"].get("remoteUrl", "")
    try_reuse_existing_repo = config["git"].get("tryReuseExistingRepo", False)

    if try_reuse_existing_repo:
        if remote_url:
            # If a remote URL is given, we reclone to prevent issues
            if git_dir_path.exists():
                shutil.rmtree(git_dir_path)
            else:
                git_dir_path.mkdir(parents=True, exist_ok=True)
            repo = Repo.clone_from(remote_url, git_dir_path, no_checkout=True)
        
        try:
            # Check if the existing repository is matching the SVN history
            repo = Repo(git_dir_path)
            resume_revision = verify_git_repo_against_svn(repo, svnLocalClient, config)
            if resume_revision >= 1:
                return repo, resume_revision
            else:
                logger.info(f"[git] Existing does not match SVN history")
        except:
            logger.info(f"[git] Failure while verifying existing repo")

    repo = create_new_git_repo(str(git_dir_path), initial_branch)
    return repo, 1


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


def commit(
    repo: Repo, meta: CommitMetadata, worktreePath: Path) -> Commit:
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
