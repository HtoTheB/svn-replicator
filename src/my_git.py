import logging
from typing import Optional
from git import Repo
import shutil
from git import Repo, GitCommandError
from git.objects import Commit
from helper import *
from pathlib import Path
from commitMetadata import *

logger = logging.getLogger(__name__)
logging.getLogger("git").setLevel(logging.INFO)


TRAILER_SVN_REVISION = "svn-revision"
TRAILER_SVN_BRANCH = "svn-branch"


def _trailers_of(commit: Commit) -> dict:
    """Trailer key -> first value, lower-cased, read from the commit message
    in-process. The tool writes both trailers as the message's final
    paragraph, so only that paragraph is scanned. Parsed here rather than via
    git interpret-trailers because lookup walks thousands of commits and a
    subprocess per commit would dominate the run."""
    message = commit.message or ""
    paragraphs = message.split("\n\n")
    if not paragraphs:
        return {}
    trailers = {}
    for line in paragraphs[-1].splitlines():
        key, sep, value = line.partition(":")
        if not sep or not value.strip():
            continue
        key = key.strip().lower()
        if key not in trailers:
            trailers[key] = value.strip()
    return trailers


def _as_revision(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def svn_revision_of(commit: Commit) -> Optional[int]:
    """The mirrored SVN revision of a commit, read from its Svn-Revision
    trailer. The trailer key matches case-insensitively and the first value
    wins; a malformed trailer value returns None."""
    return _as_revision(_trailers_of(commit).get(TRAILER_SVN_REVISION))


def svn_branch_of(commit: Commit) -> Optional[str]:
    """The git branch (including the 'tag/' prefix) a commit was made on,
    read from its Svn-Branch trailer; None if the commit carries no such trailer."""
    return _trailers_of(commit).get(TRAILER_SVN_BRANCH)


def find_origin_commit(repo: Repo, git_branch_name: str, copyfrom_rev: int) -> Optional[Commit]:
    """The commit to parent a copy-creation to: the commit with the largest
    Svn-Revision <= copyfrom_rev that was committed ON git_branch_name.

    A branch's git history contains its origin's ancestry, so ancestry alone
    cannot identify the origin's own commits — the Svn-Branch trailer does.
    Candidate refs are the branch's live ref plus any preserved deleted
    lifetime ('del/<name>@<rev>'); they are enumerated via ref.path from
    existing references only (str(ref) is just the branch name, and walking a
    non-existent ref raises). The same commit reached via several refs is
    counted once; distinct commits tied at the same revision are warned about
    rather than picked silently. Returns None when no candidate qualifies,
    in which case the caller falls back to the default parent and logs it."""
    live = f"refs/heads/{git_branch_name}"
    refs = [r.path for r in repo.references
            if r.path == live or r.path.startswith(f"refs/heads/del/{git_branch_name}@")]
    best = None                      # (rev, commit)
    seen = set()
    for ref in refs:                 # existing refs only; walking a missing ref would raise
        for c in repo.iter_commits(ref, first_parent=True):
            if svn_branch_of(c) != git_branch_name:      # scope to the branch's own commits
                continue
            rev = svn_revision_of(c)
            if rev is None or rev > copyfrom_rev:
                continue
            if c.hexsha in seen:      # same commit via several refs is not a tie
                continue
            seen.add(c.hexsha)
            if best is None or rev > best[0]:
                best = (rev, c)
            elif rev == best[0]:
                logger.warning(f"[copy-ancestor] duplicate Svn-Revision {rev} on '{git_branch_name}'; keeping first found")
    return best[1] if best else None


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
    repo: Repo, branch_name: str, create_if_missing: bool = False,
    base_commit: Commit | None = None,
) -> None:
    """Point HEAD at branch_name, creating the branch when it is missing.

    An existing branch is never reset. A missing branch is created at
    base_commit (e.g. the resolved copy-from origin) or at the current HEAD;
    both also work while HEAD is unborn.
    """
    ref = f"refs/heads/{branch_name}"

    # The branch exists -> point HEAD at it. Deleted lifetimes live under
    # different ref names ('del/...') and can never collide with the live one.
    if branch_name in {h.name for h in repo.heads}:
        repo.git.symbolic_ref("HEAD", ref)
        return

    # Unborn HEAD and no base -> nothing to create yet; the first commit
    # writes the ref.
    if not repo.head.is_valid() and base_commit is None:
        repo.git.symbolic_ref("HEAD", ref)
        return

    if not create_if_missing:
        raise GitCommandError(
            "switch_branch",
            f"Branch '{branch_name}' does not exist and create_if_missing=False.",
        )

    # A detached HEAD would make 'create at current HEAD' meaningless
    # (an unborn HEAD has no commit to be detached from)
    if repo.head.is_valid() and repo.head.is_detached:
        raise GitCommandError(
            "switch_branch", "Local Repo is corrupted, HEAD is detached"
        )

    if base_commit is not None:
        repo.create_head(branch_name, commit=base_commit.hexsha)
    else:
        # no base: create at the current HEAD commit
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
