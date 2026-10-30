"""Build the 'basic' SVN fixture: plain branch (legacy parenting, behavior
must not change) and a copied branch + copied tag (copy-origin parenting).

History:
  r1 import trunk {a.txt, b.txt}
  r2 create /branches and /tags layout
  r3 create plain branch 'plain' with a seed file (NO copy-from)
  r4 modify trunk (a.txt)
  r5 copy branch 'copied' from /trunk (copyfrom-rev = 4)
  r6 cut tag 'v1' from /trunk (copyfrom-rev = 5)
  r7 modify branch 'plain' (new file)
  r8 modify branch 'copied' (a.txt)
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
    fx.repo_copy("trunk", "branches/copied", "r5: copy branch from trunk")
    fx.repo_copy("trunk", "tags/v1", "r6: cut tag v1")
    fx.commit("r7: add file to plain", files={"branches/plain/c.txt": "C1"})
    fx.commit("r8: edit copied", files={"branches/copied/a.txt": "A3"})


if __name__ == "__main__":
    build(Path(sys.argv[1]).resolve())
