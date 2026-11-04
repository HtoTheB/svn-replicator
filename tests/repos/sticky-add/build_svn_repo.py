"""Build the 'sticky-add' SVN fixture: a single revision that creates a branch
by copy AND adds new files inside it.

The copied root directory is listed before its new descendants in the
revision's path list. The mirror must still recognize the branch as a
creation and parent it to the copy-from source - not downgrade it to a
plain modification of an existing branch.

History (r1 is the root marker commit created by SvnFixture):
  r2 trunk {a, b}
  r3 layout
  r4 plain branch {seed}          <- will be the active branch before r6
  r5 update a                      <- copy-from source state
  r6 branches/feature = copy of /trunk + new files x1, x2   (one revision)
  r7 update b
  r8 feature + x3

Copy-from detail: a working-copy copy records the peg revision of the
SOURCE DIRECTORY as its copyfrom-rev. commit() syncs the working copy
first (so it catches up with repository-side operations), but after a
commit the source directory's peg is still one revision behind the repo
HEAD. The fixture therefore calls update() before the copy, so the
branch forks trunk at its tip (r5). The tool then parents the branch to
the largest trunk commit at or before r5 - the r5 commit itself.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from svn_fixture import SvnFixture  # noqa: E402


def build(root: Path):
    fx = SvnFixture(root)
    fx.create()
    fx.commit("init trunk", files={"trunk/a.txt": "A1", "trunk/b.txt": "B1"})
    fx.commit("create layout", mkdirs=["branches", "tags"])
    fx.commit("create plain branch",
              mkdirs=["branches/plain"], files={"branches/plain/seed.txt": "seed"})
    fx.commit("update a", files={"trunk/a.txt": "A2"})
    # Bring the working copy to trunk's tip, then copy it so the branch
    # forks trunk at its current revision (the realistic "branch from trunk"
    # scenario). The copy is staged in the working copy and committed together
    # with the new files, so everything lands in a single revision.
    fx.update()
    fx.wc_copy("trunk", "branches/feature")
    fx.commit("create feature branch from trunk",
              files={"branches/feature/x1.txt": "X1", "branches/feature/x2.txt": "X2"})
    fx.commit("update b", files={"trunk/b.txt": "B2"})
    fx.commit("add x3 to feature", files={"branches/feature/x3.txt": "X3"})


if __name__ == "__main__":
    build(Path(sys.argv[1]).resolve())
