import logging
from commitMetadata import CommitMetadata  # Adjust path to your project
from pathlib import Path, PurePath
import shutil
from svn.local import LocalClient
from svn.remote import RemoteClient
from svn.exception import SvnException
from commitMetadata import CommitMetadata
from helper import *
import os
import xml.etree.ElementTree as ET
from typing import List
from typing import Iterable
from pathChange import *
from branchChange import *

logger = logging.getLogger(__name__)
logging.getLogger("svn").setLevel(logging.INFO)


def initSvn(svnRemoteURI: str) -> RemoteClient:
    """
    Creates a RemoteClient for an SVN repository.

    - If repoPath is a URL -> RemoteClient(repoPath)
    - If repoPath is a local path -> file:// URL from the path

    Working copies are NOT accepted.
    If verify=True, a simple 'svn log' check is performed.
    """
    # Setting this environment variable fixes issues with a missing locale
    os.environ["LC_ALL"] = "C"

    client = RemoteClient(svnRemoteURI)
    try:
        client.info()
    except SvnException as exc:
        raise ValueError(
            f"'{svnRemoteURI}' is not an accessible SVN repository."
        ) from exc

    return client


def cloneRepo(
    svnRemoteClient: RemoteClient, dest: str
) -> LocalClient:
    """
    Provides a local working copy in 'dest':

    :param client: RemoteClient (from initSvn), LocalClient is also tolerated.
    :param dest: Target folder for the working copy
    :return: LocalClient for the working copy in dest
    """
    dest_path = Path(dest).expanduser().resolve()

    # Check if target exists
    if dest_path.exists():
        if not dest_path.is_dir():
            # This is a file, we don't want it
            logger.info(
                f"[svn] '{dest_path}' is a file. Deleting and recloning...")
            dest_path.unlink()
        else:
            # It's a folder, check whether it contains the correct svn repo
            try:
                localClient = LocalClient(str(dest_path))
                if localClient.info()["url"] == svnRemoteClient.url:
                    # It's the correct repository, update and return it
                    logger.info(f"[svn] Found appropriate local repo. Updating...")
                    localClient.cleanup()
                    return localClient
                else:
                    # It's a repository, but the wrong one
                    logger.info(
                        f"[svn] '{dest_path}' is a repo with the wrong remote. Deleting and recloning..."
                    )
                    shutil.rmtree(dest_path)
            except SvnException:
                # It's not a repository
                logger.info(
                    f"[svn] '{dest_path}' is not a working copy. Deleting and recloning..."
                )
                shutil.rmtree(dest_path)

    # We get here if there wasn't a proper local repo before
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    logger.debug(f"[svn] Cloning remote...")
    svnRemoteClient.checkout(str(dest_path), 1)
    return LocalClient(str(dest_path))


def getRevisionCount(svnLocalClient: LocalClient) -> int:
    info = svnLocalClient.info(revision="HEAD")
    return info["entry#revision"]


def getChangesInRevision(svnLocalClient: LocalClient, revision: int) -> List[PathChange]:
    """
    Returns all changes in a revision, including SVN-Action ('A','M','D','R')
    and node kind ('file','dir').
    Equivalent to: svn log -v --xml -r <revision> <path>
    """
    xml_output = svnLocalClient.run_command(
        "log",
        ["--xml", "-v", "-r", str(revision), svnLocalClient.path],
    )

    changes: List[PathChange] = []
    for p in ET.fromstringlist(xml_output).find("logentry").find("paths").findall("path"):
        if not p.text:
            continue

        changes.append(
            PathChange(
                path=PurePath(p.text),
                action=SvnAction.fromString(p.get("action", default="?")),
                kind=SvnNodeKind.fromString(p.get("kind", default="?"))
            )
        )

    return changes


def findAffectedBranches(
    changes: Iterable[PathChange],
    trunk_root: PurePath,
    branches_root: PurePath,
    tags_root: PurePath,
    excluded_branches: Iterable[PurePath],
) -> List[BranchChange]:

    result: List[BranchChange] = []

    for change in changes:
        branch_root: PurePath | None = None

        branchInfos = getBranchInfos(change.path,
                                     trunk_root,
                                     branches_root,
                                     tags_root)

        if branchInfos == None:
            continue

        branch_root, branch_name, isTag = branchInfos
        if branch_root in excluded_branches:
            continue

        # Check if there already is an entry for our branch
        current = next((x for x in result if x.path == branch_root), None)
        # Check if the selected change is the root directory of a branch
        is_root_dir = (
            change.kind == SvnNodeKind.DIR and change.path == branch_root)

        # 1) Branch deleted (delete of root directory)
        if change.action == SvnAction.DELETE and is_root_dir:
            if current is None:
                result.append(BranchChange(path=branch_root,
                                           name=branch_name,
                                           type=BranchChangeType.DELETED,
                                           isTag=isTag))
            else:
                raise Exception("Change in Folder while parent deleted!")
                # current.type = BranchChangeType.DELETED

        # 2) Branch newly created (add of root directory)
        elif change.action == SvnAction.ADD and is_root_dir:
            if current is None:
                result.append(BranchChange(path=branch_root,
                                           name=branch_name,
                                           type=BranchChangeType.ADDED,
                                           isTag=isTag))

        # 3) Other changes somewhere below the branch -> at least MODIFIED
        elif current is None:
            result.append(BranchChange(path=branch_root,
                                       name=branch_name,
                                       type=BranchChangeType.MODIFIED,
                                       isTag=isTag))
        elif current is not None:
            current.type = BranchChangeType.MODIFIED
        # If ADDED/DELETED is already set, leave unchanged

    return result

# TODO: This seems to work, but why are we not using the revision parameter?
def getRevisionMetadata(svnLocalClient: LocalClient, revision: int) -> CommitMetadata:
    # --- Part 1: Basic data from svn info() ---
    info = svnLocalClient.info()
    
    # Adjust these keys to match your info() structure if needed
    rev = int(info["commit#revision"])  # or "commit_revision"

    # --- Part 2: Metadata from svn log() for this exact revision ---
    entries = svnLocalClient.log_default(
        revision_from=rev,
        revision_to=rev,
        changelist=False,  # metadata only, no path list
    )
    # If no logs are available, it's okay to crash
    entry = next(entries)

    # Adjust these field names to match your LogEntry definition if needed
    message = f"r{rev}: {entry.msg or ''}"
    author = entry.author
    date = entry.date  # is already a datetime object

    # E-Mail: placeholder here, you can add SVN user -> email mapping later
    author_email = f"{author}@example.invalid"

    # If you want the SVN revision/URL in the Git commit text, you can add it here:
    # message = f"r{rev} {message}"

    meta = CommitMetadata(
        message=message,
        author_name=author,
        author_email=author_email,
        committer_name=author,
        committer_email=author_email,
        author_date=date,
        commit_date=date,
        # You set parent_commits later in the Git part
    )

    return meta


def switchRevision(svnLocalClient: LocalClient, revision: int | str) -> None:
    """
    Updates the working copy to the specified revision.

    :param svnLocalClient: LocalClient for your SVN working copy
    :param revision: Revision number (e.g. 42) or 'HEAD'
    """
    # Convert int -> str so both 42 and '42'/'HEAD' work
    svnLocalClient.update(revision=str(revision))
