"""Build the 'tag-recreate' SVN fixture: a tag that is cut, deleted and
cut again under the same name (the tag-side counterpart of the branch
re-creation in the 'lifecycle' fixture).

History (r1 is the root marker commit created by SvnFixture):
  r2 trunk {a}
  r3 layout
  r4 trunk a -> A2
  r5 cut tag v1 from /trunk         (copyfrom-rev 4)
  r6 trunk a -> A3
  r7 delete tags/v1
  r8 cut tag v1 from /trunk again   (copyfrom-rev 6, re-creation)
  r9 trunk a -> A4
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
    fx.commit("update a", files={"trunk/a.txt": "A2"})
    fx.repo_copy("trunk", "tags/v1", "cut tag v1")
    fx.commit("update a", files={"trunk/a.txt": "A3"})
    fx.repo_delete("tags/v1", "delete tag v1")
    fx.repo_copy("trunk", "tags/v1", "recreate tag v1")
    fx.commit("update a", files={"trunk/a.txt": "A4"})


if __name__ == "__main__":
    build(Path(sys.argv[1]).resolve())
