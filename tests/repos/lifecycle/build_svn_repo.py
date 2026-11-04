"""Build the 'lifecycle' SVN fixture: a branch and a tag that are created,
deleted and (for the branch) created again under the same name.

History (r1 is the root marker commit created by SvnFixture):
  r2 trunk {a}
  r3 layout
  r4 copy branch b1 from /trunk        (copyfrom-rev 3, the repo HEAD)
  r5 trunk a -> A2
  r6 b1 + note.txt
  r7 delete branches/b1
  r8 copy branch b1 from /trunk again  (copyfrom-rev 5, re-creation)
  r9 cut tag t1 from /trunk            (copyfrom-rev 5)
  r10 delete tags/t1
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
    fx.commit("note in b1", files={"branches/b1/note.txt": "N1"})
    fx.repo_delete("branches/b1", "delete b1")
    fx.repo_copy("trunk", "branches/b1", "recreate b1")
    fx.repo_copy("trunk", "tags/t1", "cut tag t1")
    fx.repo_delete("tags/t1", "delete t1")


if __name__ == "__main__":
    build(Path(sys.argv[1]).resolve())
