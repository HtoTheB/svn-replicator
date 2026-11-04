"""Helper for building small SVN fixture repositories (test harness).

Commits are made with a fixed author ('fixture') so the generated history
does not depend on the machine user. Commit timestamps are wall-clock time
and cannot be pinned with plain svn 1.x (no --date option), which is why
the harness compares git trees, graph structure and messages rather than
commit SHAs.
"""
import subprocess
from pathlib import Path

AUTHOR = "fixture"


def _run(args, **kw):
    r = subprocess.run(args, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(args)}\nstdout: {r.stdout}\nstderr: {r.stderr}")
    return r.stdout


class SvnFixture:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.repo_dir = self.root / "repo.svn"
        self.url = f"file://{self.repo_dir}"
        self.wc = self.root / "wc"

    def create(self):
        """Create an empty repository and a working copy of it.

        A placeholder root file is committed first: a working copy of an
        EMPTY repository (base revision 0) pins path pegs at r0, which makes
        working-copy copies record wrong copyfrom-rev values. Checking out
        at r1 avoids that.
        """
        _run(["svnadmin", "create", str(self.repo_dir)])
        _run(["svn", "checkout", "-q", self.url, str(self.wc)])
        (self.wc / ".root").write_text("fixture repository root marker")
        _run(["svn", "add", str(self.wc / ".root")])
        _run(["svn", "commit", "-q", "--username", AUTHOR,
              "-m", "create root", str(self.wc)])

    def _head_rev(self) -> int:
        out = _run(["svn", "info", "--show-item", "revision", self.url])
        return int(out.strip())

    def commit(self, msg: str, files=None, mkdirs=None, delete=None) -> int:
        """Apply changes in the working copy and commit them.

        files:  {relative path: text content} - created or updated
        mkdirs: [relative path] - directories to create
        delete: [relative path] - versioned items to delete

        Returns the new repository HEAD revision.
        """
        # Repository-side operations (repo_copy/repo_delete) leave the working
        # copy behind; sync it first so locally prepared changes do not clash
        # with paths that only exist in the repository. The update also keeps
        # the working copy's peg revisions current, which working-copy copies
        # record as their copyfrom-rev (a stale peg records the wrong source
        # snapshot, unlike a URL copy which always records the repo HEAD).
        _run(["svn", "update", "-q", str(self.wc)])
        for d in mkdirs or []:
            (self.wc / d).mkdir(parents=True, exist_ok=True)
        for p, content in (files or {}).items():
            fp = self.wc / p
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content)
        for p in delete or []:
            _run(["svn", "delete", str(self.wc / p)])

        # Add everything unversioned, parents before children.
        status = _run(["svn", "status", str(self.wc)])
        unversioned = sorted(
            (line[8:] for line in status.splitlines() if line.startswith("?")),
            key=lambda s: (s.count("/"), s),
        )
        added = []
        for path in unversioned:
            if any(path.startswith(a + "/") for a in added):
                continue
            _run(["svn", "add", str(self.wc / path)])
            added.append(path)

        _run(["svn", "commit", "-q", "--username", AUTHOR, "-m", msg, str(self.wc)])
        return self._head_rev()

    def repo_copy(self, src_rel: str, dst_rel: str, msg: str) -> int:
        """Copy inside the repository (svn cp URL URL -m msg), one commit."""
        _run([
            "svn", "cp", f"{self.url}/{src_rel}", f"{self.url}/{dst_rel}",
            "--username", AUTHOR, "-m", msg, "-q",
        ])
        return self._head_rev()

    def repo_delete(self, rel: str, msg: str) -> int:
        """Delete a repository path (svn delete URL -m msg), one commit."""
        _run([
            "svn", "delete", f"{self.url}/{rel}",
            "--username", AUTHOR, "-m", msg, "-q",
        ])
        return self._head_rev()

    def wc_copy(self, src_rel: str, dst_rel: str):
        """Copy inside the working copy WITHOUT committing. The destination
        is unversioned and becomes part of the next commit() - this is how a
        single revision can contain a copied root directory together with
        new files inside it."""
        _run(["svn", "cp", str(self.wc / src_rel), str(self.wc / dst_rel)])

    def update(self):
        """Bring the working copy to the repository HEAD. Working-copy copies
        record the source directory's peg revision as their copyfrom-rev, so
        update first when a copy should reflect the source's tip."""
        _run(["svn", "update", "-q", str(self.wc)])
