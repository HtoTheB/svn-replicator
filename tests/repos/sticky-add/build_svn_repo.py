"""Build the 'sticky-add' SVN fixture: a single revision that creates a branch
by copy AND adds new files inside it.

The copied root directory is listed before its new descendants in the
revision's path list. The mirror must still recognize the branch as a
creation and parent it to the copy-from source - not downgrade it to a
plain modification of an existing branch.

History:
  r1 trunk {a, b}
  r2 layout
  r3 plain branch {seed}          <- will be the active branch before r5
  r4 trunk a -> A2                <- copy-from source state
  r5 branches/feature = copy of /trunk + new files x1, x2   (one revision)
  r6 trunk b -> B2
  r7 feature + x3
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tests"))
from svn_fixture import SvnFixture  # noqa: E402


def build(root: Path):
    fx = SvnFixture(root)
    fx.create()
    fx.commit("r1: init trunk", files={"trunk/a.txt": "A1", "trunk/b.txt": "B1"})
    fx.commit("r2: create layout", mkdirs=["branches", "tags"])
    fx.commit("r3: create plain branch",
              mkdirs=["branches/plain"], files={"branches/plain/seed.txt": "seed"})
    fx.commit("r4: update a", files={"trunk/a.txt": "A2"})
    # The copy is staged in the working copy and committed together with the
    # new files, so everything lands in a single revision.
    fx.wc_copy("trunk", "branches/feature")
    fx.commit("r5: create feature branch from trunk",
              files={"branches/feature/x1.txt": "X1", "branches/feature/x2.txt": "X2"})
    fx.commit("r6: update b", files={"trunk/b.txt": "B2"})
    fx.commit("r7: add x3 to feature", files={"branches/feature/x3.txt": "X3"})


if __name__ == "__main__":
    build(Path(sys.argv[1]).resolve())
