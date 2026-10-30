import logging
from pathlib import Path, PurePath
from tqdm import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm
from my_svn import *
import helper
from helper import *
from my_git import *
from my_git import find_origin_commit, svn_revision_of

logger = logging.getLogger(__name__)


def _resolve_origin(br: BranchChange, rev: int, gitRepo: Repo,
                    trunk: PurePath, branches: PurePath, tags: PurePath):
    """Resolve the git commit to parent a copy-creation to.

    Returns (base_commit | None, reason | None, origin_git_name | None).
    base_commit is None iff the origin is unresolvable (reason set).
    Never raises.
    """
    origin = getBranchInfos(br.copyfrom_path, trunk, branches, tags)
    if origin is None:
        return (None,
                f"copy-from path '{br.copyfrom_path}' does not map to a mirrored branch",
                None)
    o_root, o_name, o_isTag = origin
    o_branch = "tag/" + o_name if o_isTag else o_name
    if br.copyfrom_rev is None:
        return (None, f"copyfrom-rev missing/unparseable for '{br.copyfrom_path}'", o_branch)
    if br.copyfrom_rev >= rev:
        return (None, f"invalid copyfrom-rev {br.copyfrom_rev} (>= current rev {rev})", o_branch)
    base = find_origin_commit(gitRepo, o_branch, br.copyfrom_rev)
    if base is None:
        return (None,
                (f"no mirrored commit on '{o_branch}' at or before r{br.copyfrom_rev} "
                 f"(source ignored/skipped, predates the mirror, or its deleted "
                 f"lifetime is not preserved)"),
                o_branch)
    return base, None, o_branch


def process_svn_to_git(config):
    """
    Main processing function that mirrors SVN repository to Git.

    Args:
        config: Configuration dictionary containing svn, git, and localFiles settings
    """
    # Initialize SVN clients
    svnRemoteClient = initSvn(config["svn"]["remoteURI"])
    svnLocalClient = cloneRepo(svnRemoteClient, config["localFiles"]["svnWorkingDir"])
    svnRevisionCount = getRevisionCount(svnLocalClient)

    # Folder roots, resolved once (used per revision for branch classification
    # and for copy-origin resolution)
    trunkRoot = PurePath(config["svn"]["trunkFolder"])
    branchesRoot = PurePath(config["svn"]["branchFolder"])
    tagsRoot = PurePath(config["svn"]["tagFolder"])
    # Materialize the ignore list: `x in <map iterator>` would consume it, so
    # after the first check per revision every check returns False (B9).
    ignoredRoots = [PurePath(f) for f in config["svn"]["ignoredFolders"]]

    # Initialize Git repository
    gitRepo = create_new_git_repo(
        config["localFiles"]["gitWorkingDir"]
    )

    try:
        # Redirect the output of the logging library to work with the tqdm progress bar
        with logging_redirect_tqdm():
            # Iterate through all the revisions with progress bar
            for i in progressBar(1, svnRevisionCount, "Processing revisions"):
                # Skip revisions that we are not supposed to look at
                if i in config["svn"]["skipRevisions"]:
                    continue

                # Look at all the changed files
                fileChanges = getChangesInRevision(svnLocalClient, i)
                # Check whether any of the files match one of the folders that we're supposed to look at
                branchChanges = findAffectedBranches(
                    fileChanges,
                    trunkRoot,
                    branchesRoot,
                    tagsRoot,
                    ignoredRoots,
                )

                if len(branchChanges) >= 2:
                    logger.debug(f"Revision {i} modified multiple branches")
                if len(branchChanges) >= 1:
                    switchRevision(svnLocalClient, i)
                    # Create a commit for every folder that we need to change
                    for br in branchChanges:
                        branchName = "tag/" + br.name if br.isTag else br.name
                        if br.type in [
                            BranchChangeType.MODIFIED,
                            BranchChangeType.ADDED,
                        ]:
                            # Copy-origin parenting (gated: ADDED + copy-from +
                            # missing destination ref + copyAncestors enabled)
                            base_commit = None
                            deviation_reason = None
                            origin_branch = None
                            if (br.type == BranchChangeType.ADDED
                                    and br.copyfrom_path is not None
                                    and branchName not in {h.name for h in gitRepo.heads}  # D6 destination gate
                                    and config["git"]["copyAncestors"]):
                                base_commit, deviation_reason, origin_branch = \
                                    _resolve_origin(br, i, gitRepo,
                                                    trunkRoot, branchesRoot, tagsRoot)

                            # Switch to branch and commit new changes
                            switch_branch(gitRepo, branchName, True, base_commit=base_commit)
                            new_commit = commit(
                                gitRepo,
                                getRevisionMetadata(svnLocalClient, i, branchName,
                                                    config["git"]["commitMsgPrefix"]),
                                Path(svnLocalClient.path).append(br.path),
                            )
                            if base_commit is not None:
                                logger.info(
                                    f"[copy-ancestor] r{i}: branch '{branchName}' copied from "
                                    f"'{br.copyfrom_path}@r{br.copyfrom_rev}' → parented to "
                                    f"{base_commit.hexsha[:8]} (origin '{origin_branch}', "
                                    f"mirrored r{svn_revision_of(base_commit)})")
                            elif deviation_reason is not None:
                                parent = new_commit.parents[0] if new_commit.parents else None
                                actual = (f"{parent.hexsha[:8]} ({parent.summary})" if parent
                                          else "none (root commit)")
                                logger.warning(
                                    f"[copy-ancestor] r{i}: branch '{branchName}' copied from "
                                    f"'{br.copyfrom_path}@r{br.copyfrom_rev}' but origin "
                                    f"unresolvable ({deviation_reason}) → assigned parent: "
                                    f"{actual} instead")
                            if br.isTag:
                                logger.debug(
                                    f"Tag {br.name} was created/modified in revision {i}"
                                )
                                set_git_tag(gitRepo, br.name, new_commit)

                        elif br.type == BranchChangeType.DELETED:
                            # The branch was deleted in the SVN repo
                            logger.debug(
                                f"Branch {branchName} was deleted in revision {i}"
                            )
                            
                            if config["git"]["keepDeletedBranches"]:
                                rename_branch(
                                    gitRepo, branchName, f"del/{branchName}@{i}"
                                )
                            else:
                                delete_branch(gitRepo, branchName)

                            if br.isTag:
                                logger.debug(
                                    f"Tag {br.name} was deleted in revision {i}"
                                )
                                if not config["git"]["keepDeletedTags"]:
                                    delete_git_tag(gitRepo, br.name)
    finally:
        clear_worktree_config(gitRepo)
