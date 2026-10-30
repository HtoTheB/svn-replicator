"""Deterministic test harness for svn-replicator.

For every fixture in tests/repos/:
  1. build a small SVN repository (tests/repos/<name>/build_svn_repo.py),
  2. run the tool on it with the fixture's generated config,
  3. compare the produced Git mirror against the fixture's expectation
     files (expected*.json): ref set, per-ref commit sequence (SVN
     revision, subject, parent identities, file contents), commit
     trailers, lightweight tags, and the run's [copy-ancestor] log
     counts.

Only the Python standard library is used (plus git/svn on the command
line), so it runs anywhere the tool itself runs - including GitHub
Actions runners.

Usage:
    .venv/bin/python tests/run_tests.py [fixture_name ...]
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent
TMP_DIR = TESTS_DIR / "tmp"


class CheckError(AssertionError):
    pass


def fail(msg: str):
    raise CheckError(msg)


def run_cmd(args, **kw):
    r = subprocess.run(args, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(args)}\n{r.stdout}\n{r.stderr}")
    return r.stdout


# ---------------------------------------------------------------------------
# Fixture construction
# ---------------------------------------------------------------------------

def build_fixture(fixture_dir: Path, work_dir: Path):
    """Run the fixture's build script into work_dir."""
    build_script = fixture_dir / "build_svn_repo.py"
    r = subprocess.run(
        [sys.executable, str(build_script), str(work_dir)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        fail(f"fixture build failed:\n{r.stdout}\n{r.stderr}")


# ---------------------------------------------------------------------------
# Running the tool
# ---------------------------------------------------------------------------

def write_config(cfg_path: Path, fixture: dict, repo_url: str, work_base: Path):
    cfg = fixture.get("config", {})
    ignored = cfg.get("ignoredFolders", [])
    yaml_text = f"""svn:
  remoteURI: "{repo_url}"
  skipRevisions: []
  trunkFolder: "/trunk/"
  branchFolder: "/branches/"
  tagFolder: "/tags/"
  ignoredFolders: {json.dumps(ignored)}
git:
  commitMsgPrefix: true
  keepDeletedBranches: {str(cfg.get("keepDeletedBranches", False)).lower()}
  keepDeletedTags: {str(cfg.get("keepDeletedTags", False)).lower()}
localFiles:
  svnWorkingDir: "{work_base / 'wc_svn'}"
  gitWorkingDir: "{work_base / 'git_mirror'}"
logging:
  level: "INFO"
"""
    cfg_path.write_text(yaml_text)


def run_tool(cfg_path: Path):
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "src" / "main.py"),
         "--config", str(cfg_path)],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    return r


# ---------------------------------------------------------------------------
# Reading the produced Git mirror
# ---------------------------------------------------------------------------

TRAILER_RE = re.compile(r"^(Svn-Revision|Svn-Branch): (.*)$")


def read_mirror(mirror: Path):
    """Return (refs, identity, tag_names).

    refs:      ref name -> list of commits, oldest first,
               commit = dict(rev, subject, parents=[(branch, rev) | None], sha)
    identity:  sha -> (svn_branch, svn_revision)
    """
    refs = {}
    identity = {}
    heads = run_cmd(["git", "-C", str(mirror), "for-each-ref", "refs/heads",
                     "--format=%(refname:short)"]).split()
    for ref in heads:
        raw = run_cmd(["git", "-C", str(mirror), "log", "--reverse",
                       "--first-parent", "--format=%H%x00%T%x00%P%x00%B", ref])
        commits = []
        for entry in raw.split("\n\x00"):
            if not entry.strip():
                continue
            head_part, _, body = entry.partition("\n")
            sha, tree, parents_raw, _, subject = head_part.split("\x00", 4)
            trailers = {}
            for line in body.splitlines():
                m = TRAILER_RE.match(line)
                if m:
                    trailers[m.group(1)] = m.group(2)
            branch = trailers.get("Svn-Branch")
            rev = trailers.get("Svn-Revision")
            if branch is None or rev is None or not rev.isdigit():
                fail(f"commit {sha[:8]} on {ref} lacks a parseable Svn-Revision/Svn-Branch trailer")
            identity[sha] = (branch, int(rev))
            commits.append({
                "sha": sha,
                "tree": tree,
                "rev": int(rev),
                "subject": subject,
                "parents": [identity.get(p) for p in parents_raw.split() if p],
            })
        refs[ref] = commits

    # Every parent of every commit must be resolvable (no dangling lineages).
    for ref, commits in refs.items():
        for c in commits:
            for p in c["parents"]:
                if p is None:
                    fail(f"commit {c['sha'][:8]} on {ref} has an unresolvable parent")

    tag_names = run_cmd(["git", "-C", str(mirror), "tag", "-l"]).split()
    return refs, identity, tag_names


def read_tree_files(mirror: Path, commit_sha: str):
    """name -> blob content for a commit's tree (recursive, files only)."""
    out = run_cmd(["git", "-C", str(mirror), "ls-tree", "-r", "--", commit_sha])
    files = {}
    for line in out.splitlines():
        meta, _, name = line.partition("\t")
        mode, obj_type, blob = meta.split()
        if obj_type != "blob":
            fail(f"unexpected non-blob entry in tree: {line}")
        files[name] = run_cmd(["git", "-C", str(mirror), "cat-file", "blob", blob])
    return files


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def check_mirror(fixture_name: str, exp: dict, mirror: Path):
    refs, _identity, tag_names = read_mirror(mirror)

    expected_refs = exp.get("refs", {})
    if set(refs) != set(expected_refs):
        fail(f"{fixture_name}: ref set mismatch\n  actual:   {sorted(refs)}\n  expected: {sorted(expected_refs)}")

    for name in sorted(expected_refs):
        expected = expected_refs[name]
        actual = refs[name]

        if expected.get("trailer_branch") is not None:
            branches = {b for b, _ in (identity_of(c) for c in actual)}
            if branches != {expected["trailer_branch"]}:
                fail(f"{fixture_name}/{name}: Svn-Branch trailers {branches} != {{{expected['trailer_branch']!r}}}")

        exp_commits = expected["commits"]
        if len(actual) != len(exp_commits):
            fail(f"{fixture_name}/{name}: {len(actual)} commits, expected {len(exp_commits)}:\n  actual:   {[c['rev'] for c in actual]}\n  expected: {[e[0] for e in exp_commits]}")

        for i, (e, a) in enumerate(zip(exp_commits, actual)):
            e_rev, e_subject, e_parents, e_files = (e + [None] * 4)[:4]
            where = f"{fixture_name}/{name} commit {i + 1}"
            if a["rev"] != e_rev:
                fail(f"{where}: Svn-Revision {a['rev']} != {e_rev}")
            if a["subject"] != e_subject:
                fail(f"{where}: subject {a['subject']!r} != {e_subject!r}")
            if e_parents is not None:
                if a["parents"] != e_parents:
                    fail(f"{where}: parents {a['parents']} != {e_parents}")
            elif a["parents"]:
                fail(f"{where}: expected root commit (no parent), got {a['parents']}")
            if e_files is not None:
                actual_files = read_tree_files(mirror, a["sha"])
                if actual_files != e_files:
                    diff = {k: (actual_files.get(k), v) for k in set(actual_files) | set(e_files)
                            if actual_files.get(k) != v}
                    fail(f"{where}: tree mismatch:\n  {diff}")

    if "tags" in exp:
        if sorted(tag_names) != sorted(exp["tags"]):
            fail(f"{fixture_name}: tags {sorted(tag_names)} != {sorted(exp['tags'])}")


def identity_of(commit):
    return commit["parents"][0] if commit["parents"] else None


def check_log(fixture_name: str, exp: dict, tool_output: str):
    expected = exp.get("log", {})
    actual_info = len(re.findall(r"INFO.*\[copy-ancestor\]", tool_output))
    actual_warn = len(re.findall(r"WARNING.*\[copy-ancestor\]", tool_output))
    if expected.get("info") is not None and actual_info != expected["info"]:
        fail(f"{fixture_name}: {actual_info} [copy-ancestor] INFO lines, expected {expected['info']}")
    if expected.get("warning") is not None and actual_warn != expected["warning"]:
        fail(f"{fixture_name}: {actual_warn} [copy-ancestor] WARNING lines, expected {expected['warning']}\n{tool_output[-3000:]}")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run_fixture(fixture_dir: Path):
    name = fixture_dir.name
    expected_files = sorted(fixture_dir.glob("expected*.json"))
    if not expected_files:
        fail(f"{name}: no expected*.json files")

    for exp_file in expected_files:
        exp = json.loads(exp_file.read_text())
        work = TMP_DIR / name / exp_file.stem
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)

        build_fixture(fixture_dir, work)
        repo_url = f"file://{work / 'repo.svn'}"

        cfg_path = work / "config.yml"
        write_config(cfg_path, exp, repo_url, work)

        r = run_tool(cfg_path)
        if r.returncode != 0:
            fail(f"{name} ({exp_file.name}): tool exited {r.returncode}\n{r.stdout}\n{r.stderr}")

        check_log(name, exp, r.stdout + r.stderr)
        check_mirror(name, exp, work / "git_mirror")
        print(f"  PASS  {name} / {exp_file.name}")


def main(argv):
    names = argv or None
    failures = []
    for fixture_dir in sorted((TESTS_DIR / "repos").iterdir()):
        if not fixture_dir.is_dir():
            continue
        if names and fixture_dir.name not in names:
            continue
        print(f"fixture: {fixture_dir.name}")
        try:
            run_fixture(fixture_dir)
        except CheckError as e:
            failures.append((fixture_dir.name, str(e)))
            print(f"  FAIL  {fixture_dir.name}: {e}")
        except Exception as e:  # noqa: BLE001 - report any crash as a failure
            failures.append((fixture_dir.name, repr(e)))
            print(f"  FAIL  {fixture_dir.name}: {e!r}")

    if failures:
        print(f"\n{len(failures)} fixture(s) FAILED")
        return 1
    print("\nall fixtures passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
