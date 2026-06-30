from dataclasses import dataclass
from enum import Enum
from pathlib import PurePath


class BranchChangeType(Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"


def getBranchInfos(path: PurePath,
                   trunk_root: PurePath,
                   branches_root: PurePath,
                   tags_root: PurePath,) -> tuple[PurePath, str, bool]:
    # /trunk or below
        if path.is_relative_to(trunk_root):
            branch_name = "trunk"
            branch_root = trunk_root
            isTag = False

        # /branches/<name>/...
        elif path.is_relative_to(branches_root):
            try:
                rel = path.relative_to(branches_root)  # z.B. "feature1/foo.txt"
            except ValueError:
                return None
            if not rel.parts:
                # Änderung direkt an /branches (ohne Namen) -> keinem Branch zuordnen
                return None
            branch_name = rel.parts[0]
            branch_root = branches_root / branch_name
            isTag = False

        # /tags/<name>/...
        elif path.is_relative_to(tags_root):
            try:
                rel = path.relative_to(tags_root)
            except ValueError:
                return None
            if not rel.parts:
                return None
            branch_name = rel.parts[0]
            branch_root = tags_root / branch_name
            isTag = True
        else:
            return None
        
        return (PurePath(branch_root), branch_name, isTag)


@dataclass
class BranchChange:
    path: PurePath
    name: str
    type: BranchChangeType
    isTag: bool = False
