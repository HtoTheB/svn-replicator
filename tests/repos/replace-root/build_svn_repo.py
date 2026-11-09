"""Build the 'replace-root' SVN fixture: the root of an existing branch is
REPLACED by a copy of another branch (svn cp trunk branches/p1 while
branches/p1 already exists).

The replace is pinned as a continuation of the existing branch: the
action R is classified as a modification, the branch is not re-parented
and no [copy-ancestor] log lines are produced (open question B4/B5 in
the analysis plan - consciously pinned, to be revisited when the
semantics are decided). The branch's content is swapped wholesale: the
replaced branch carries the source branch's content at the replace
revision.

History (r1 is the root marker commit created by SvnFixture):
  r2 trunk {a}
  r3 layout
  r4 plain branch p1 {seed} (no copy-from)
  r5 trunk a -> A2
  r6 replace branches/p1 with a copy of /trunk (root R from /trunk:4 -> p1 keeps its lineage)
  r7 p1 + n1
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
    fx.commit("create p1",
              mkdirs=["branches/p1"], files={"branches/p1/seed.txt": "seed"})
    fx.commit("update a", files={"trunk/a.txt": "A2"})
    fx.wc_replace("trunk", "branches/p1")
    fx.commit("replace p1 with trunk")
    fx.commit("note in p1", files={"branches/p1/n1.txt": "N1"})


if __name__ == "__main__":
    build(Path(sys.argv[1]).resolve())
