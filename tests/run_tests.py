"""Deterministic test harness for svn-replicator.

For every fixture in tests/repos/<name>/ there is:
  - a ready-made SVN source,  repo.svn/        (committed; read at test time)
  - expectation file(s),       expected*.json   (structural: refs, per-ref
                                                commit sequence, parents,
                                                tree contents, [copy-ancestor]
                                                log counts)
  - a golden git reference,    <stem>.git       (committed; the exact git
                                                repository the tool is
                                                expected to produce)

For every expectation file the harness:
  1. runs the tool once on the committed SVN source,
  2. checks the produced mirror structurally against expected*.json,
  3. checks the produced mirror against the golden reference by identity:
     same ref map and the same reachable object set, i.e. every commit,
     tree and blob hash matches - so a run that drifts from the pinned
     behavior (including any nondeterminism such as wall-clock or machine
     data leaking into a commit) fails.

The structural check is the DIAGNOSTIC: it names the branch, commit and
parent that drifted. The golden check is the GUARD: it pins the exact
output, commit hashes included, so a change between tool versions that
alters the result is caught.

Regenerating a golden (after an intentional behavior change, with the
matching expected*.json updated first):
    .venv/bin/python tests/run_tests.py --regen [fixture_name ...]
The structural check must pass before a golden is rewritten, so a golden
is always consistent with its committed expectation file.

Only the Python standard library is used (plus git/svn on the command
line), so it runs anywhere the tool itself runs - including GitHub
Actions runners. The SVN sources are plain repositories, readable via a
file:// URL; nothing is built at test time.

Usage:
    .venv/bin/python tests/run_tests.py [fixture_name ...]
    .venv/bin/python tests/run_tests.py --regen [fixture_name ...]
"""
import json
import re
import shutil
import subprocess
import sys
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
# Reading the produced Git mirror (structural view)
# ---------------------------------------------------------------------------

TRAILER_RE = re.compile(r"^(Svn-Revision|Svn-Branch): (.*)$")


def read_mirror(mirror: Path):
    """Return (refs, identity, tag_names).

    refs:      ref name -> list of commits, oldest first,
                commit = dict(rev, subject, parents=[(branch, rev)], sha)
    identity:  sha -> (svn_branch, svn_revision)

    Each commit is read individually with `git cat-file -p`, which gives an
    unambiguous header (tree / parent lines) and the raw message. All refs
    are indexed first, then parent references are resolved, so lineages that
    cross refs (a branch forked from another branch) resolve regardless of
    ref order.
    """
    refs = {}
    identity = {}
    heads = run_cmd(["git", "-C", str(mirror), "for-each-ref", "refs/heads",
                     "--format=%(refname:short)"]).split()
    for ref in heads:
        shas = run_cmd(["git", "-C", str(mirror), "log", "--reverse",
                        "--first-parent", "--format=%H", ref]).split()
        commits = []
        for sha in shas:
            tree, parents, msg_lines, in_header = None, [], [], True
            for line in run_cmd(["git", "-C", str(mirror), "cat-file", "-p", sha]).splitlines():
                if in_header:
                    if line == "":
                        in_header = False
                    elif line.startswith("tree "):
                        tree = line[5:]
                    elif line.startswith("parent "):
                        parents.append(line[7:])
                else:
                    msg_lines.append(line)
            message = "\n".join(msg_lines)

            # Trailers live in the message's final paragraph.
            trailers = {}
            if message.strip():
                for line in message.rstrip("\n").split("\n\n")[-1].splitlines():
                    m = TRAILER_RE.match(line)
                    if m:
                        trailers[m.group(1)] = m.group(2)
            branch = trailers.get("Svn-Branch")
            rev = trailers.get("Svn-Revision")
            if branch is None or rev is None or not rev.isdigit():
                fail(f"commit {sha[:8]} on {ref} lacks a parseable Svn-Revision/Svn-Branch trailer")
            identity[sha] = (branch, int(rev))
            subject = message.splitlines()[0] if message else ""
            commits.append({
                "sha": sha,
                "tree": tree,
                "rev": int(rev),
                "subject": subject,
                "parents": [p for p in parents if p],
            })
        refs[ref] = commits

    # Second pass: resolve parent SHAs to (branch, revision) identities.
    for ref, commits in refs.items():
        for c in commits:
            resolved = []
            for p in c["parents"]:
                if p not in identity:
                    fail(f"commit {c['sha'][:8]} on {ref} has an unresolvable parent {p[:8]}")
                resolved.append(identity[p])
            c["parents"] = resolved

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
    refs, identity, tag_names = read_mirror(mirror)

    expected_refs = exp.get("refs", {})
    if set(refs) != set(expected_refs):
        fail(f"{fixture_name}: ref set mismatch\n  actual:   {sorted(refs)}\n  expected: {sorted(expected_refs)}")

    for name in sorted(expected_refs):
        expected = expected_refs[name]
        actual = refs[name]

        # A ref's first-parent walk includes the ancestors it was forked from
        # (their commits carry the OTHER branch's Svn-Branch trailer), so the
        # sequence check applies only to the commits this ref OWNS - the ones
        # carrying its own Svn-Branch value. The ancestry is pinned by the
        # per-commit parent identity assertions.
        exp_commits = expected["commits"]
        if expected.get("trailer_branch") is not None:
            own = [c for c in actual if identity[c["sha"]][0] == expected["trailer_branch"]]
        else:
            own = actual

        if len(own) != len(exp_commits):
            fail(f"{fixture_name}/{name}: {len(own)} commits, expected {len(exp_commits)}:\n  actual:   {[c['rev'] for c in own]}\n  expected: {[e[0] for e in exp_commits]}")

        for i, (e, a) in enumerate(zip(exp_commits, own)):
            e_rev, e_subject, e_parents, e_files = (e + [None] * 4)[:4]
            where = f"{fixture_name}/{name} commit {i + 1}"
            if a["rev"] != e_rev:
                fail(f"{where}: Svn-Revision {a['rev']} != {e_rev}")
            if a["subject"] != e_subject:
                fail(f"{where}: subject {a['subject']!r} != {e_subject!r}")
            if e_parents is not None:
                actual_parents = [list(p) for p in a["parents"]]
                if actual_parents != e_parents:
                    fail(f"{where}: parents {actual_parents} != {e_parents}")
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


def check_log(fixture_name: str, exp: dict, tool_output: str):
    expected = exp.get("log", {})
    actual_info = len(re.findall(r"INFO.*\[copy-ancestor\]", tool_output))
    actual_warn = len(re.findall(r"WARNING.*\[copy-ancestor\]", tool_output))
    if expected.get("info") is not None and actual_info != expected["info"]:
        fail(f"{fixture_name}: {actual_info} [copy-ancestor] INFO lines, expected {expected['info']}")
    if expected.get("warning") is not None and actual_warn != expected["warning"]:
        fail(f"{fixture_name}: {actual_warn} [copy-ancestor] WARNING lines, expected {expected['warning']}\n{tool_output[-3000:]}")


# ---------------------------------------------------------------------------
# Golden reference: identity comparison and regeneration
# ---------------------------------------------------------------------------

def ref_map(repo: Path) -> dict:
    """ref name -> object SHA, for all heads and tags."""
    out = run_cmd(["git", "-C", str(repo), "for-each-ref",
                   "refs/heads", "refs/tags", "--format=%(refname) %(objectname)"])
    return dict(line.split(" ", 1) for line in out.splitlines() if line.strip())


def objects(repo: Path) -> set:
    """Set of every object reachable from any ref (commits, trees, blobs)."""
    out = run_cmd(["git", "-C", str(repo), "rev-list", "--objects", "--all"])
    return {line.split(" ")[0] for line in out.splitlines() if line.strip()}


def compare_to_golden(fixture_name: str, stem: str, mirror: Path, golden: Path):
    """The produced mirror must be identical to the golden reference:
    same ref map and the same reachable object set, i.e. every commit, tree
    and blob hash matches (commit hashes included)."""
    if not golden.exists():
        fail(f"{fixture_name} ({stem}): golden reference {golden.name} is missing - "
             f"run with --regen to create it")

    map_m, map_g = ref_map(mirror), ref_map(golden)
    if map_m != map_g:
        only_m = {k: v for k, v in map_m.items() if map_g.get(k) != v}
        only_g = {k: v for k, v in map_g.items() if map_m.get(k) != v}
        fail(f"{fixture_name} ({stem}): ref map differs from golden:\n  mirror: {only_m}\n  golden: {only_g}")

    obj_m, obj_g = objects(mirror), objects(golden)
    if obj_m != obj_g:
        fail(f"{fixture_name} ({stem}): object set differs from golden: "
             f"only-in-mirror={sorted(obj_m - obj_g)[:5]} only-in-golden={sorted(obj_g - obj_m)[:5]}")
    print(f"  golden OK: {len(obj_m)} objects, ref map and all hashes match {golden.name}")


def make_golden(mirror: Path, golden: Path):
    """Replace the golden reference with a bare clone of the produced mirror."""
    if golden.exists():
        shutil.rmtree(golden)
    run_cmd(["git", "clone", "--bare", "--quiet", str(mirror), str(golden)])
    print(f"  regenerated {golden.name} ({len(objects(golden))} objects)")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run_fixture(fixture_dir: Path, regen: bool):
    name = fixture_dir.name
    source = fixture_dir / "repo.svn"
    if not (source / "db").is_dir():
        fail(f"{name}: committed SVN source missing at {source}")
    repo_url = f"file://{source.resolve()}"

    expected_files = sorted(fixture_dir.glob("expected*.json"))
    if not expected_files:
        fail(f"{name}: no expected*.json files")

    for exp_file in expected_files:
        stem = exp_file.stem
        exp = json.loads(exp_file.read_text())
        golden = fixture_dir / f"{stem}.git"

        work = TMP_DIR / name / stem
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)

        cfg_path = work / "config.yml"
        write_config(cfg_path, exp, repo_url, work)
        r = run_tool(cfg_path)
        if r.returncode != 0:
            fail(f"{name} ({stem}): tool exited {r.returncode}\n{r.stdout}\n{r.stderr}")

        # Structural checks double as the gate for regeneration: a golden is
        # only rewritten when the mirror still matches its expectation file.
        check_log(name, exp, r.stdout + r.stderr)
        check_mirror(name, exp, work / "git_mirror")

        if regen:
            make_golden(work / "git_mirror", golden)
        else:
            compare_to_golden(name, stem, work / "git_mirror", golden)
        print(f"  PASS  {name} / {exp_file.name}")


def main(argv):
    regen = False
    if "--regen" in argv:
        regen = True
        argv = [a for a in argv if a != "--regen"]
    names = argv or None
    failures = []
    for fixture_dir in sorted((TESTS_DIR / "repos").iterdir()):
        if not fixture_dir.is_dir():
            continue
        if names and fixture_dir.name not in names:
            continue
        print(f"fixture: {fixture_dir.name}" + ("  [regen]" if regen else ""))
        try:
            run_fixture(fixture_dir, regen)
        except CheckError as e:
            failures.append((fixture_dir.name, str(e)))
            print(f"  FAIL  {fixture_dir.name}: {e}")
        except Exception as e:  # noqa: BLE001 - report any crash as a failure
            failures.append((fixture_dir.name, repr(e)))
            print(f"  FAIL  {fixture_dir.name}: {e!r}")

    if failures:
        print(f"\n{len(failures)} fixture(s) FAILED")
        return 1
    print("\nall fixtures passed" + (" (goldens regenerated)" if regen else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
