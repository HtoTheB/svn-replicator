"""Build the 'deviation' SVN fixture: two branches whose copy-from origin
cannot be resolved from the mirror, plus one that resolves normally.

History (r1 is the root marker commit created by SvnFixture):
  r2 trunk {a}
  r3 layout + ignored/ {i} + external/ {e}
  r4 copy branch fromIgnored from /ignored   (source is in ignoredFolders)
  r5 copy branch fromOutside from /external  (path maps to no configured folder)
  r6 copy branch normal from /trunk          (resolves: trunk at r2)

Both unresolved branches must fall back to the legacy parent (the current
HEAD), each logging one [copy-ancestor] WARNING that names the actually
assigned parent; the run must still complete.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from svn_fixture import SvnFixture  # noqa: E402


def build(root: Path):
    fx = SvnFixture(root)
    fx.create()
    fx.commit("init trunk", files={"trunk/a.txt": "A1"})
    fx.commit("create layout and extra folders",
              mkdirs=["branches", "tags", "ignored", "external"],
              files={"ignored/i.txt": "I1", "external/e.txt": "E1"})
    fx.repo_copy("ignored", "branches/fromIgnored", "copy from ignored source")
    fx.repo_copy("external", "branches/fromOutside", "copy from external source")
    fx.repo_copy("trunk", "branches/normal", "copy from trunk")


if __name__ == "__main__":
    build(Path(sys.argv[1]).resolve())
