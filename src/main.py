import yaml
from pathlib import Path, PurePath
from my_svn import *
import helper
from helper import *
from my_git import *
import defaults

configFileName = "config.yml"

# Load config from file
config = yaml.safe_load(
    open(Path(__file__).parent.parent / configFileName, "rb"))

svnRemoteClient = initSvn(config["svn"]["remoteURI"])
svnLocalClient = cloneRepo(svnRemoteClient, config["localFiles"]["svnWorkingDir"])
svnRevisionCount = getRevisionCount(svnLocalClient)

gitRepo = init_separate_git_repo()

# Iterate through all the revisions
for i in range(1, svnRevisionCount):
    # Skip revisions that we are not supposed to look at
    if i in config["svn"]["skipRevisions"]:
        continue

    # Look at all the changed files
    fileChanges = getChangesInRevision(svnLocalClient, i)
    # Chech whether any of the files match one of the folders that we're supposed to look at
    branchChanges = findAffectedBranches(
        fileChanges,
        PurePath(config["svn"]["trunkFolder"]),
        PurePath(config["svn"]["branchFolder"]),
        PurePath(config["svn"]["tagFolder"]),
        map(PurePath, config["svn"]["ignoredFolders"]),
    )

    print("\r\x1b[2K"+ # Delete last line
          f"Processing revision {i}/{svnRevisionCount}...", end="", flush=True)
    
    if len(branchChanges) >= 2:
        print(f"[main] Revision {i} modified multiple branches")
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
                print(f"[main] Branch {br.name} was deleted in revision {i}")
                # TODO: Option to choose between renaming and deleting branches
                rename_branch(gitRepo, br.name, f"del/{br.name}@{i}")