import logging
from pathlib import Path, PurePath
from tqdm import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm
from my_svn import *
import helper
from helper import *
from my_git import *

logger = logging.getLogger(__name__)


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

    # Initialize Git repository
    gitRepo = init_separate_git_repo(
        config["localFiles"]["gitWorkingDir"], 
        config["localFiles"]["svnWorkingDir"]
    )

    # Redirect the output of the logging library to work with the tqdm progress bar
    with logging_redirect_tqdm():
        # Iterate through all the revisions with progress bar
        for i in tqdm(
            range(1, svnRevisionCount + 1),
            desc="Processing revisions",
            unit=" rev",
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [ETA: {remaining}, {rate_fmt}]",
            smoothing=0.02,
        ):
            # Skip revisions that we are not supposed to look at
            if i in config["svn"]["skipRevisions"]:
                continue

            # Look at all the changed files
            fileChanges = getChangesInRevision(svnLocalClient, i)
            # Check whether any of the files match one of the folders that we're supposed to look at
            branchChanges = findAffectedBranches(
                fileChanges,
                PurePath(config["svn"]["trunkFolder"]),
                PurePath(config["svn"]["branchFolder"]),
                PurePath(config["svn"]["tagFolder"]),
                map(PurePath, config["svn"]["ignoredFolders"]),
            )

            if len(branchChanges) >= 2:
                logger.debug(f"Revision {i} modified multiple branches")
            if len(branchChanges) >= 1:
                switchRevision(svnLocalClient, i)
                # Create a commit for every folder that we need to change
                for br in branchChanges:
                    branchName = "tag/" + br.name if br.isTag else br.name
                    if br.type in [BranchChangeType.MODIFIED, BranchChangeType.ADDED]:
                        # Switch to branch and commit new changes
                        switch_branch(gitRepo, branchName, True)
                        new_commit = commit(
                            gitRepo,
                            getRevisionMetadata(svnLocalClient, i),
                            Path(svnLocalClient.path).append(br.path),
                        )
                        if br.isTag:
                            logger.debug(f"Tag {br.name} was created/modified in revision {i}")
                            set_git_tag(gitRepo, br.name, new_commit)

                    elif br.type == BranchChangeType.DELETED:
                        # The branch was deleted in the SVN repo
                        logger.debug(f"Branch {branchName} was deleted in revision {i}")
                        # TODO: Option to choose between renaming and deleting branches
                        rename_branch(gitRepo, branchName, f"del/{branchName}@{i}")

                        if br.isTag:
                            logger.debug(f"Tag {br.name} was deleted in revision {i}")
                            delete_git_tag(gitRepo, br.name)

