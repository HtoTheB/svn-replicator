"""Build the 'subset-copy' SVN fixture: a branch created by copying a
SUBFOLDER of another branch's root, not the root itself.

Trunk is touched between the setup and the copy so the active branch at
the copy is trunk - with the legacy parenting the new branch would be
parented to trunk@r6 instead of its correct origin feature@r5.

History (r1 is the root marker commit created by SvnFixture):
  r2 trunk {a}
  r3 layout
  r4 copy branch feature from /trunk        (copyfrom-rev 3)
  r5 feature + sub/ subfolder with s1
  r6 trunk a -> A2
  r7 copy /branches/feature/sub -> /branches/fromsubset (copyfrom-rev 6 -> feature@r5)
  r8 fromsubset + x1
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from svn_fixture import SvnFixture  # noqa: E402


def build(root: Path):
    fx = SvnFixture(root)
    fx.create()
    fx.commit("init trunk", files={"trunk/a.txt": "A1"})
    fx.commit("create layout", mkdirs=["branches", "tags"])
    fx.repo_copy("trunk", "branches/feature", "create feature")
    fx.commit("add subfolder in feature",
              mkdirs=["branches/feature/sub"],
              files={"branches/feature/sub/s1.txt": "S1"})
    fx.commit("update a", files={"trunk/a.txt": "A2"})
    fx.repo_copy("branches/feature/sub", "branches/fromsubset",
                 "create fromsubset from feature/sub")
    fx.commit("edit fromsubset", files={"branches/fromsubset/x1.txt": "X1"})


if __name__ == "__main__":
    build(Path(sys.argv[1]).resolve())
