from git import Repo
import shutil
from git import Repo, GitCommandError
from helper import *
from pathlib import Path
from commitMetadata import *
defaultGitDir = "./.localWCs/git"
defaultSvnDir = "./.localWCs/svn"


def getGitBranchNameFromSvnPath(
        path: str,
        trunkLocation: str,
        branchLocation: str,
        tagLocation: str):
    # Normalize path
    path = ensure_slashes(path)

    if path.find(trunkLocation) >= 0:
        return "trunk"
    if path.find(branchLocation) >= 0:
        # Get the name of the first folder after the prefix
        return substrUntilFirstOccurance(path.removeprefix(ensure_slashes(branchLocation)), "/")
    if path.find(tagLocation) >= 0:
        return "tag/"+substrUntilFirstOccurance(path.removeprefix(ensure_slashes(tagLocation)), "/")


def init_separate_git_repo(
    git_dir: str = defaultGitDir,
    worktree: str = defaultSvnDir,
    initial_branch: str = "trunk",
) -> Repo:
    """
    Initialisiert ein Git-Repository, dessen .git-Verzeichnis in 'git_dir'
    liegt, dessen Working-Tree aber 'worktree' ist (dein SVN-Ordner).

    - git_dir: z.B. ".localWCs/git"
    - worktree: z.B. ".localWCs/svn"
    - HEAD zeigt auf 'initial_branch' (unborn, bis der erste Commit kommt)
    - .svn/ wird in diesem Repo ignoriert
    """
    git_dir_path = Path(git_dir).expanduser().resolve()
    worktree_path = Path(worktree).expanduser().resolve()

    if git_dir_path.is_dir():
        shutil.rmtree(git_dir_path)

    git_dir_path.mkdir(parents=True, exist_ok=True)
    if not worktree_path.is_dir():
        raise ValueError(
            f"SVN-Working-Copy-Verzeichnis existiert nicht: {worktree_path}")

    print(f"[git] Initialising local repo...")
    # 1. Zuerst ein bare Repo im git_dir anlegen
    repo = Repo.init(git_dir_path)

    # 3. Unborn HEAD auf gewünschten Branch setzen
    ref = f"refs/heads/{initial_branch}"
    repo.git.symbolic_ref("HEAD", ref)

    # TODO: template für ein gitignore im Zielverzeichnis anlegen
    # 4. .svn für dieses Repo ignorieren (ohne .gitignore im SVN-Baum)
    exclude_path = Path(repo.git_dir) / "info" / "exclude"

    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    with exclude_path.open("a", encoding="utf-8") as f:
        f.write("\n.svn/\n")

    return repo


def switch_branch(repo: Repo, branch_name: str, create_if_missing: bool = False) -> None:
    ref = f"refs/heads/{branch_name}"

    # Fall 1: Repo hat noch keinen Commit -> unborn HEAD
    if not repo.head.is_valid():
        # Einfach nur HEAD-Symbolik setzen, kein Branch-Commit existiert bisher
        repo.git.symbolic_ref("HEAD", ref)
        return

    # Fall 2: Repo hat bereits Commits
    # Gibt es den Branch bereits?
    if branch_name in {h.name: h for h in repo.heads}:
        # Branch existiert -> HEAD einfach auf diesen Branch zeigen lassen
        repo.git.symbolic_ref("HEAD", ref)
        return

    # Branch existiert noch nicht
    if not create_if_missing:
        raise GitCommandError("switch_branch",
                              f"Branch '{branch_name}' existiert nicht und create_if_missing=False.")

    # Neuen Branch-Ref am aktuellen HEAD-Commit anlegen
    if repo.head.is_detached:
        raise GitCommandError("switch_branch",
                              "Local Repo is corrupted, HEAD is detached")
    else:
        # HEAD zeigt bereits auf einen Branch; neuer Branch vom aktuellen Commit
        repo.create_head(branch_name)

    # HEAD auf den neuen Branch setzen
    repo.git.symbolic_ref("HEAD", ref)


def commit(repo: Repo, meta: CommitMetadata, worktreePath: Path, *, setTag: str | None = None) -> Commit:
    """
    Erzeugt einen Commit im gegebenen Repo auf dem aktuellen Branch.

    - 'worktree' ist das Verzeichnis, dessen Inhalt in den Commit soll
      (z.B. eine SVN-Working-Copy eines bestimmten Branches).
    - Es wird nichts am Worktree verändert; Git liest nur Dateien.
    """
    if not worktreePath.is_dir():
        raise ValueError(f"Worktree existiert nicht: {worktreePath}")

    # 1. Diesen Worktree in der Config eintragen
    #    (schreibt NUR in .git/config, verändert keine Dateien im Worktree)
    repo.git.config("core.worktree", str(worktreePath))

    # 2. Alle Änderungen aus diesem Worktree stagen (respektiert .git/info/exclude -> .svn)
    repo.git.add(A=True)

    # 3. Autor / Committer
    author = Actor(meta.author_name, meta.author_email)

    committer_name = meta.committer_name or meta.author_name
    committer_email = meta.committer_email or meta.author_email
    committer = Actor(committer_name, committer_email)

    # 4. Datumsangaben: mindestens commit_date ist vorhanden
    #    author_date: falls None, nehmen wir commit_date
    author_date_str = CommitMetadata.to_git_date(
        meta.author_date or meta.commit_date)
    commit_date_str = CommitMetadata.to_git_date(meta.commit_date)

    # 5. Commit erzeugen
    new_commit = repo.index.commit(
        meta.message,
        author=author,
        committer=committer,
        author_date=author_date_str,
        commit_date=commit_date_str,
        skip_hooks=True
    )

    return new_commit

from git import Repo, GitCommandError


def rename_branch(
    repo: Repo,
    old_name: str,
    new_name: str,
) -> None:
    """
    Bennent einen Git-Branch von old_name nach new_name um.

    - Ändert nur Refs, NICHT den Working Tree.
    - Wenn das Repo noch keinen Commit hat (unborn HEAD) und HEAD auf old_name zeigt,
      wird nur HEAD auf new_name umgehängt.
    - Bei force=True wird '-M' statt '-m' benutzt (überschreibt evtl. existierenden Branch).

    :raises ValueError: wenn der Branch nicht existiert oder umbenennen fehlschlägt.
    """

    args = ["-M", old_name, new_name]
    try:
        # entspricht: git branch -m|-M old_name new_name
        repo.git.branch(*args)
    except GitCommandError as e:
        raise ValueError(
            f"Branch '{old_name}' konnte nicht in '{new_name}' umbenannt werden: {e}"
        ) from e