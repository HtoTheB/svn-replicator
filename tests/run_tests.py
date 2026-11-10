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
    .venv/bin/python tests/run_tests.py [stage ...]
    .venv/bin/python tests/run_tests.py --regen [stage ...]

A stage is a fixture name (runs every expectation file in it) or a single
expectation file as 'fixture/stem' (the file name without '.json'), e.g.
'lifecycle' or 'lifecycle/expected_keep'.
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


def git_dir_for(repo: Path) -> Path:
    """The git directory to address explicitly: <repo>/.git for a mirror with
    a worktree, the directory itself for a bare golden. Git commands use
    --git-dir with this path instead of -C discovery, so they behave
    identically wherever the checkout lives (discovery walks up into the
    enclosing repository for a git dir that looks incomplete)."""
    git_dir = repo / ".git"
    return git_dir if git_dir.is_dir() else repo


# ---------------------------------------------------------------------------
# Running the tool
# ---------------------------------------------------------------------------

def write_config(cfg_path: Path, fixture: dict, repo_url: str, work_base: Path):
    cfg = fixture.get("config", {})
    ignored = cfg.get("ignoredFolders", [])
    skip = cfg.get("skipRevisions", [])
    yaml_text = f"""svn:
  remoteURI: "{repo_url}"
  skipRevisions: {json.dumps(skip)}
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
    git_dir = str(git_dir_for(mirror))
    heads = run_cmd(["git", "--git-dir", git_dir, "for-each-ref", "refs/heads",
                     "--format=%(refname:short)"]).split()
    for ref in heads:
        shas = run_cmd(["git", "--git-dir", git_dir, "log", "--reverse",
                        "--first-parent", "--format=%H", ref]).split()
        commits = []
        for sha in shas:
            tree, parents, msg_lines, in_header = None, [], [], True
            for line in run_cmd(["git", "--git-dir", git_dir, "cat-file", "-p", sha]).splitlines():
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

    tag_names = run_cmd(["git", "--git-dir", git_dir, "tag", "-l"]).split()
    # Tag name -> target commit SHA. Tags are lightweight (set_git_tag points
    # them at the branch's newest commit), so each must equal the target of
    # the corresponding branch head (live, or del/<name>@<rev> when preserved).
    tag_targets = {
        name: sha
        for name, sha in (
            line.split(" ", 1)
            for line in run_cmd(
                ["git", "--git-dir", git_dir, "for-each-ref", "refs/tags",
                 "--format=%(refname:short) %(objectname)"]
            ).splitlines()
            if line.strip()
        )
    }
    return refs, identity, tag_names, tag_targets


def read_tree_files(mirror: Path, commit_sha: str):
    """name -> blob content for a commit's tree (recursive, files only)."""
    git_dir = str(git_dir_for(mirror))
    out = run_cmd(["git", "--git-dir", git_dir, "ls-tree", "-r", "--", commit_sha])
    files = {}
    for line in out.splitlines():
        meta, _, name = line.partition("\t")
        mode, obj_type, blob = meta.split()
        if obj_type != "blob":
            fail(f"unexpected non-blob entry in tree: {line}")
        files[name] = run_cmd(["git", "--git-dir", git_dir, "cat-file", "blob", blob])
    return files


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def check_mirror(fixture_name: str, exp: dict, mirror: Path):
    refs, identity, tag_names, tag_targets = read_mirror(mirror)

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
                    diff = {k: (actual_files.get(k), e_files.get(k))
                            for k in set(actual_files) | set(e_files)
                            if actual_files.get(k) != e_files.get(k)}
                    fail(f"{where}: tree mismatch:\n  {diff}")

    if "tags" in exp:
        if sorted(tag_names) != sorted(exp["tags"]):
            fail(f"{fixture_name}: tags {sorted(tag_names)} != {sorted(exp['tags'])}")
        for name in exp["tags"]:
            # The tag's target branch: the live head, or the preserved
            # deleted lifetime when keepDeletedTags is on.
            head = f"tag/{name}"
            if head not in refs:
                del_heads = [h for h in refs if h.startswith(f"del/{head}@")]
                if not del_heads:
                    fail(f"{fixture_name}: tag '{name}' has no target branch head (looked for {head} and del/{head}@*)")
                head = sorted(del_heads, key=lambda h: int(h.rsplit("@", 1)[1]))[0]
            expected_target = refs[head][-1]["sha"]
            actual_target = tag_targets.get(name)
            if actual_target != expected_target:
                fail(f"{fixture_name}: tag '{name}' points at {actual_target}, "
                     f"expected the tip of {head} ({expected_target})")


def check_log(fixture_name: str, exp: dict, tool_output: str):
    expected = exp.get("log", {})
    info_lines = re.findall(r"INFO.*\[copy-ancestor\].*", tool_output)
    warn_lines = re.findall(r"WARNING.*\[copy-ancestor\].*", tool_output)
    if expected.get("info") is not None and len(info_lines) != expected["info"]:
        fail(f"{fixture_name}: {len(info_lines)} [copy-ancestor] INFO lines, expected {expected['info']}")
    if expected.get("warning") is not None and len(warn_lines) != expected["warning"]:
        fail(f"{fixture_name}: {len(warn_lines)} [copy-ancestor] WARNING lines, expected {expected['warning']}\n{tool_output[-3000:]}")
    # Per-copy attribution: each expected fallback deviation names the branch
    # and the assigned parent (as branch + revision). Exactly one warning must
    # match each, and it must name that parent - so two warnings for one copy
    # and none for the other cannot satisfy a count-based check.
    for w in expected.get("warnings", []):
        branch, parent_rev = w["branch"], w["parent_rev"]
        matches = [line for line in warn_lines
                   if f"branch '{branch}'" in line and "assigned parent" in line]
        if len(matches) != 1:
            fail(f"{fixture_name}: expected exactly one [copy-ancestor] WARNING for branch '{branch}', got {len(matches)}")
        m = re.search(r"assigned parent: ([0-9a-f]{8}) \(r(\d+): (.*?)\) instead", matches[0])
        if not m or int(m.group(2)) != parent_rev:
            fail(f"{fixture_name}: WARNING for '{branch}' does not name the assigned parent "
                 f"at r{parent_rev}:\n{matches[0]}")


# ---------------------------------------------------------------------------
# Golden reference: identity comparison and regeneration
# ---------------------------------------------------------------------------

def ref_map(repo: Path) -> dict:
    """ref name -> object SHA, for all heads and tags."""
    out = run_cmd(["git", "--git-dir", str(git_dir_for(repo)), "for-each-ref",
                   "refs/heads", "refs/tags", "--format=%(refname) %(objectname)"])
    return dict(line.split(" ", 1) for line in out.splitlines() if line.strip())


def objects(repo: Path) -> set:
    """Set of every object reachable from any ref (commits, trees, blobs)."""
    out = run_cmd(["git", "--git-dir", str(git_dir_for(repo)), "rev-list", "--objects", "--all"])
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
    """Replace the golden reference with a bare clone of the produced mirror,
    with every ref stored as a loose ref file (see body)."""
    if golden.exists():
        shutil.rmtree(golden)
    run_cmd(["git", "clone", "--bare", "--quiet", str(mirror), str(golden)])
    git_dir = str(golden)
    refs = run_cmd(["git", "--git-dir", git_dir, "for-each-ref",
                    "--format=%(refname) %(objectname)"]).splitlines()
    # The clone packed every ref into packed-refs and left refs/ empty; git
    # does not track empty directories, so once the golden has been committed
    # to the outer repository and checked out again the bare directory has no
    # refs/ and is no longer recognized as a git repository. Recreate the
    # refs as LOOSE files: remove packed-refs (which holds every ref), then
    # update-ref each one - with the packed file gone each update is a real
    # write, so refs/ survives the round-trip.
    (golden / "packed-refs").unlink()
    for line in refs:
        name, sha = line.split(" ", 1)
        run_cmd(["git", "--git-dir", git_dir, "update-ref", name, sha])
    print(f"  regenerated {golden.name} ({len(objects(golden))} objects)")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run_fixture(fixture_dir: Path, regen: bool, stem: str | None = None):
    """Run a fixture. stem limits the run to one expectation file (the file
    name without '.json'); None runs every expected*.json in the fixture."""
    name = fixture_dir.name
    source = fixture_dir / "repo.svn"
    if not (source / "db").is_dir():
        fail(f"{name}: committed SVN source missing at {source}")
    repo_url = f"file://{source.resolve()}"

    expected_files = sorted(fixture_dir.glob("expected*.json"))
    if stem is not None:
        expected_files = [f for f in expected_files if f.stem == stem]
        if not expected_files:
            available = ", ".join(f.stem for f in sorted(fixture_dir.glob("expected*.json")))
            fail(f"{name}: no expectation file with stem '{stem}' (available: {available})")
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
    selections = argv or None
    failures = []
    available = sorted(d.name for d in (TESTS_DIR / "repos").iterdir() if d.is_dir())
    if selections:
        unknown = [s for s in selections if s.split("/", 1)[0] not in available]
        if unknown:
            print(f"unknown fixture(s): {sorted(unknown)} (available: {available})")
            return 1
        if not selections:
            print("no stage selected")
            return 1
    for fixture_dir in sorted((TESTS_DIR / "repos").iterdir()):
        if not fixture_dir.is_dir():
            continue
        # A selection is either the fixture name (all its expectation files)
        # or 'fixture/stem' (a single expectation file).
        sel = next((s for s in (selections or []) if s == fixture_dir.name or s.startswith(fixture_dir.name + "/")), None)
        if selections and sel is None:
            continue
        stem = sel.removeprefix(fixture_dir.name + "/") if (sel and "/" in sel) else None
        print(f"fixture: {fixture_dir.name}" + (f"/{stem}" if stem else "") + ("  [regen]" if regen else ""))
        try:
            run_fixture(fixture_dir, regen, stem)
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
