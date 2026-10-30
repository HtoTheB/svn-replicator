"""Build the 'lifecycle' SVN fixture: a branch and a tag that are created,
deleted and (for the branch) created again under the same name.

History:
  r1 trunk {a}
  r2 layout
  r3 copy branch b1 from /trunk        (copyfrom-rev 1)
  r4 trunk a -> A2
  r5 b1 + note.txt
  r6 delete branches/b1
  r7 copy branch b1 from /trunk again  (copyfrom-rev 4, re-creation)
  r8 cut tag t1 from /trunk            (copyfrom-rev 4)
  r9 delete tags/t1
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tests"))
from svn_fixture import SvnFixture  # noqa: E402


def build(root: Path):
    fx = SvnFixture(root)
    fx.create()
    fx.commit("r1: init trunk", files={"trunk/a.txt": "A1"})
    fx.commit("r2: create layout", mkdirs=["branches", "tags"])
    fx.repo_copy("trunk", "branches/b1", "r3: create b1")
    fx.commit("r4: update a", files={"trunk/a.txt": "A2"})
    fx.commit("r5: note in b1", files={"branches/b1/note.txt": "N1"})
    fx.repo_delete("branches/b1", "r6: delete b1")
    fx.repo_copy("trunk", "branches/b1", "r7: recreate b1")
    fx.repo_copy("trunk", "tags/t1", "r8: cut tag t1")
    fx.repo_delete("tags/t1", "r9: delete t1")


if __name__ == "__main__":
    build(Path(sys.argv[1]).resolve())
