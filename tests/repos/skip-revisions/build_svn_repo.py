"""Build the 'skip-revisions' SVN fixture: revisions that are skipped
via svn.skipRevisions. Two revisions are skipped - a trunk change (r6)
and a branch change (r8). The b2 copy at r7 records copyfrom-rev 6,
which is skipped: its origin resolves to trunk@r5, the largest
MIRRORED trunk commit at or before r6 - without the skip it would be
trunk@r6.

History (r1 is the root marker commit created by SvnFixture); the
expected config skips revisions 6 and 8:
  r2 trunk {a}
  r3 layout
  r4 copy branch b1 from /trunk       (copyfrom-rev 3 -> trunk@r2)
  r5 trunk a -> A2
  r6 trunk a -> A3                    <- SKIPPED (gap in the trunk mirror)
  r7 copy branch b2 from /trunk       (copyfrom-rev 6, skipped -> origin trunk@r5)
  r8 b1 + n1                          <- SKIPPED (gap in the b1 mirror)
  r9 b2 + m1
  r10 trunk a -> A4                   (parented to trunk@r5, across the gap)
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
    fx.repo_copy("trunk", "branches/b1", "create b1")
    fx.commit("update a", files={"trunk/a.txt": "A2"})
    fx.commit("update a", files={"trunk/a.txt": "A3"})
    fx.repo_copy("trunk", "branches/b2", "create b2")
    fx.commit("note in b1", files={"branches/b1/n1.txt": "N1"})
    fx.commit("edit b2", files={"branches/b2/m1.txt": "M1"})
    fx.commit("update a", files={"trunk/a.txt": "A4"})


if __name__ == "__main__":
    build(Path(sys.argv[1]).resolve())
