import time
from pathlib import Path, PurePath
from my_svn import *
import helper
from helper import *
from my_git import *


def progress_line(current, total, start_time):
    """Generate a progress line for console output."""
    elapsed = time.time() - start_time
    if elapsed <= 0:
        elapsed = 1

    revisions_per_minute = current / elapsed * 60
    remaining = total - current
    eta = "unknown"
    if current > 0:
        eta_seconds = remaining / current * elapsed
        mins, sec = divmod(int(eta_seconds), 60)
        hours, mins = divmod(mins, 60)
        eta = f"{hours:d}:{mins:02d}:{sec:02d}"

    return (f"Processing revision {current}/{total}... "
            f"{revisions_per_minute:.1f} revisions/min, ETA {eta}")


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
    gitRepo = init_separate_git_repo()

    startTime = time.time()

    # Iterate through all the revisions
    for i in range(1, svnRevisionCount):
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

        print("\r\x1b[2K" + # Delete last line
              progress_line(i, svnRevisionCount, startTime), end="", flush=True)
        
        if len(branchChanges) >= 2:
            print(f"[processor] Revision {i} modified multiple branches")
        if len(branchChanges) >= 1:
            switchRevision(svnLocalClient, i)
            # Create a commit for every folder that we need to change
            for br in branchChanges:
                if br.type in [BranchChangeType.MODIFIED, BranchChangeType.ADDED]:
                    # Switch to branch and commit new changes
                    switch_branch(gitRepo, br.name, True)
                    commit(gitRepo, getRevisionMetadata(svnLocalClient, i),
                        Path(svnLocalClient.path).append(br.path))
                    
                elif br.type == BranchChangeType.DELETED:
                    print(f"[processor] Branch {br.name} was deleted in revision {i}")
                    # TODO: Option to choose between renaming and deleting branches
                    rename_branch(gitRepo, br.name, f"del/{br.name}@{i}")
    
    print()  # Print newline after progress bar
