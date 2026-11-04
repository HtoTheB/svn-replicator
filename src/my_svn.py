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
from typing import List, Iterable, Optional
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
    xml_changes = ET.fromstringlist(xml_output).find("logentry").find("paths").findall("path")
    for p in xml_changes:
        if not p.text:
            continue
        
        copyfrom_path = p.attrib.get("copyfrom-path")
        copyfrom_rev_raw = p.attrib.get("copyfrom-rev")
        copyfrom_rev: Optional[int] = None
        if copyfrom_rev_raw:
            try:
                copyfrom_rev = int(copyfrom_rev_raw)
            except ValueError:
                logger.warning(f"r{revision}: unparseable copyfrom-rev '{copyfrom_rev_raw}' for {p.text}")
        changes.append(PathChange(
            path=PurePath(p.text),
            action=SvnAction.fromString(p.get("action", default="?")),
            kind=SvnNodeKind.fromString(p.get("kind", default="?")),
            copyfrom_path=PurePath(copyfrom_path) if copyfrom_path else None,
            copyfrom_rev=copyfrom_rev,
        ))

    return changes


def findAffectedBranches(
    fileChanges: Iterable[PathChange],
    trunk_root: PurePath,
    branches_root: PurePath,
    tags_root: PurePath,
    excluded_branches: Iterable[PurePath],
) -> List[BranchChange]:

    result: List[BranchChange] = []

    for change in fileChanges:
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
                                           isTag=isTag,
                                           copyfrom_path=change.copyfrom_path,
                                           copyfrom_rev=change.copyfrom_rev))
            elif change.copyfrom_path is not None:
                # A root 'A' with copy-from is authoritative even if a
                # descendant change already created the aggregate as MODIFIED
                # (the copied root may be listed before its descendants in the
                # same revision)
                current.type = BranchChangeType.ADDED
                current.copyfrom_path = change.copyfrom_path
                current.copyfrom_rev = change.copyfrom_rev

        # 3) Other changes somewhere below the branch -> at least MODIFIED
        elif current is None:
            result.append(BranchChange(path=branch_root,
                                       name=branch_name,
                                       type=BranchChangeType.MODIFIED,
                                       isTag=isTag))
        elif not (current.type == BranchChangeType.ADDED
                  and current.copyfrom_path is not None):
            # An ADDED-with-copyfrom creation is sticky: later changes inside the
            # same revision must not downgrade it (the copied root can be listed
            # before several descendant adds in the same revision).
            current.type = BranchChangeType.MODIFIED

    return result

def getRevisionMetadata(svnLocalClient: LocalClient, revision: int,
                        git_branch_name: str, use_msg_prefix: bool) -> CommitMetadata:
    """Commit metadata for the EXACT given revision (no info() coupling)."""
    entries = svnLocalClient.log_default(
        revision_from=revision,
        revision_to=revision,
        changelist=False,  # metadata only, no path list
    )
    # If no logs are available, it's okay to crash
    entry = next(entries)

    # Two trailers in ONE final paragraph, no blank line between them:
    # splitting them into separate paragraphs makes GitPython's
    # trailers_dict drop all but the last one.
    message_body = (f"r{revision}: {entry.msg or ''}" if use_msg_prefix
                    else (entry.msg or ''))
    message = (f"{message_body}\n\n"
               f"Svn-Revision: {revision}\n"
               f"Svn-Branch: {git_branch_name}")

    author = entry.author

    meta = CommitMetadata(
        message=message,
        author_name=author,
        author_email=f"{author}@example.invalid",
        committer_name=author,
        committer_email=f"{author}@example.invalid",
        author_date=entry.date,
        commit_date=entry.date,
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
