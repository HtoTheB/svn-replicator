"""Build the 'plain-delete' SVN fixture: a branch created WITHOUT a copy
(a plain mkdir + files) that is later deleted. The lifecycle fixture only
deletes copy-created branches; this one pins the plain-branch side: the
deletion handling (keep -> del/plain@rev, nokeep -> ref gone) is the
same, but the branch's single commit carries the legacy parent (trunk at
r2, the active branch tip) and no [copy-ancestor] log lines are expected
at all.

History (r1 is the root marker commit created by SvnFixture):
  r2 trunk {a}
  r3 layout
  r4 plain branch {seed} (no copy-from)
  r5 trunk a -> A2
  r6 plain + n1
  r7 delete branches/plain
  r8 trunk a -> A3
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
    fx.commit("create plain branch",
              mkdirs=["branches/plain"], files={"branches/plain/seed.txt": "seed"})
    fx.commit("update a", files={"trunk/a.txt": "A2"})
    fx.commit("note in plain", files={"branches/plain/n1.txt": "N1"})
    fx.repo_delete("branches/plain", "delete plain")
    fx.commit("update a", files={"trunk/a.txt": "A3"})


if __name__ == "__main__":
    build(Path(sys.argv[1]).resolve())
