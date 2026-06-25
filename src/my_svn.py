import logging
from commitMetadata import CommitMetadata  # Pfad an dein Projekt anpassen
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

def initSvn(svnRemoteURI: str) -> RemoteClient:
    """
    Erzeugt einen RemoteClient für ein SVN-Repository.

    - Wenn repoPath eine URL ist -> RemoteClient(repoPath)
    - Wenn repoPath ein lokaler Pfad ist -> file://-URL aus dem Pfad

    Working Copies werden NICHT akzeptiert.
    Wenn verify=True, wird ein einfacher 'svn log'-Check gemacht.
    """
    # Setting this environment variable fixes issues with a missing locale
    os.environ["LC_ALL"] = "C"

    client = RemoteClient(svnRemoteURI)
    try:
        client.info()
    except SvnException as exc:
        raise ValueError(
            f"'Unter {svnRemoteURI}' ist kein erreichbares SVN-Repository."
        ) from exc

    return client


def cloneRepo(
    svnRemoteClient: RemoteClient, dest: str
) -> LocalClient:
    """
    Stellt eine lokale Working Copy in 'dest' bereit:

    :param client: RemoteClient (von initSvn), LocalClient wird aber auch toleriert.
    :param dest: Zielordner für die Working Copy
    :return: LocalClient für die Working Copy in dest
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
    Liefert alle Änderungen in einer Revision, inkl. SVN-Action ('A','M','D','R')
    und Node-Kind ('file','dir').
    Entspricht: svn log -v --xml -r <revision> <Pfad>
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
        if str(branch_root) in excluded_branches:
            continue

        # Check if there already is an entry for our branch
        current = next((x for x in result if x.path == branch_root), None)
        # Check the selected change is the root directory of a branch
        is_root_dir = (
            change.kind == SvnNodeKind.DIR and change.path == branch_root)

        # 1) Branch gelöscht (Delete des Root-Verzeichnisses)
        if change.action == SvnAction.DELETE and is_root_dir:
            if current is None:
                result.append(BranchChange(path=branch_root,
                                           name=branch_name,
                                           type=BranchChangeType.DELETED,
                                           isTag=isTag))
            else:
                raise Exception("Change in Folder while parent deleted!")
                # current.type = BranchChangeType.DELETED

        # 2) Branch neu angelegt (Add des Root-Verzeichnisses)
        elif change.action == SvnAction.ADD and is_root_dir:
            if current is None:
                result.append(BranchChange(path=branch_root,
                                           name=branch_name,
                                           type=BranchChangeType.ADDED,
                                           isTag=isTag))

        # 3) Sonstige Änderungen irgendwo unterhalb des Branches -> mindestens MODIFIED
        elif current is None:
            result.append(BranchChange(path=branch_root,
                                       name=branch_name,
                                       type=BranchChangeType.MODIFIED,
                                       isTag=isTag))
        elif current is not None:
            current.type = BranchChangeType.MODIFIED
        # Wenn schon ADDED/DELETED gesetzt ist, unverändert lassen

    return result


def getRevisionMetadata(svnLocalClient: LocalClient, revision: int) -> CommitMetadata:
    # --- Teil 1: Basisdaten aus svn info() ---
    info = svnLocalClient.info()

    # Passe diese Keys ggf. an deine info()-Struktur an
    rev = int(info["commit#revision"])  # oder "commit_revision"

    # --- Teil 2: Metadaten aus svn log() für genau diese Revision ---
    entries = svnLocalClient.log_default(
        revision_from=rev,
        revision_to=rev,
        changelist=False,  # nur Metadaten, keine Pfadliste
    )
    # wenn keine Logs vorhanden sind, darf das ruhig crashen
    entry = next(entries)

    # Passe diese Feldnamen ggf. an deine LogEntry-Definition an
    message = f"r{rev}: {entry.msg or ''}"
    author = entry.author
    date = entry.date  # ist bereits ein datetime-Objekt

    # E-Mail: hier Platzhalter, später kannst du ein Mapping von SVN-User -> E-Mail einbauen
    author_email = f"{author}@example.invalid"

    # Wenn du die SVN-Revision/URL im Git-Commit-Text haben willst, kannst du das hier einbauen:
    # message = f"r{rev} {message}"

    meta = CommitMetadata(
        message=message,
        author_name=author,
        author_email=author_email,
        committer_name=author,
        committer_email=author_email,
        author_date=date,
        commit_date=date,
        # parent_commits setzt du später im Git-Teil
    )

    return meta


def switchRevision(svnLocalClient: LocalClient, revision: int | str) -> None:
    """
    Bringt die Working Copy auf die angegebene Revision.

    :param svnLocalClient: LocalClient für deine SVN-Working-Copy
    :param revision: Revisionsnummer (z.B. 42) oder 'HEAD'
    """
    # int -> str konvertieren, damit sowohl 42 als auch '42'/'HEAD' gehen
    svnLocalClient.update(revision=str(revision))
