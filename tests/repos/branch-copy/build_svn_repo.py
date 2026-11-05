"""Build the 'branch-copy' SVN fixture: branches copied from another
branch (not trunk), including a two-hop chain.

Trunk updates are interleaved between the copies so that, at each copy,
the currently active branch is NOT the copy-from source - with the legacy
parenting (parent = active branch tip) the produced parents would differ
from the correct ones, so the expectations discriminate.

History (r1 is the root marker commit created by SvnFixture):
  r2 trunk {a}
  r3 layout
  r4 copy branch b1 from /trunk       (URL copy, copyfrom-rev 3 -> trunk@r2)
  r5 b1 + n1                          (active: b1)
  r6 trunk a -> A2                    (active: trunk)
  r7 copy branch b2 from /branches/b1 (URL copy, copyfrom-rev 6 -> b1@r5)
  r8 b2 + m1                          (active: b2)
  r9 trunk a -> A3                    (active: trunk)
  r10 copy branch b3 from /branches/b2 (URL copy, copyfrom-rev 9 -> b2@r8)
  r11 b3 + m2                         (active: b3)
  r12 b1 n1 -> N2                     (active: b1)
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
    fx.commit("note in b1", files={"branches/b1/n1.txt": "N1"})
    fx.commit("update a", files={"trunk/a.txt": "A2"})
    fx.repo_copy("branches/b1", "branches/b2", "create b2 from b1")
    fx.commit("edit b2", files={"branches/b2/m1.txt": "M1"})
    fx.commit("update a", files={"trunk/a.txt": "A3"})
    fx.repo_copy("branches/b2", "branches/b3", "create b3 from b2")
    fx.commit("edit b3", files={"branches/b3/m2.txt": "M2"})
    fx.commit("update b1 note", files={"branches/b1/n1.txt": "N2"})


if __name__ == "__main__":
    build(Path(sys.argv[1]).resolve())
