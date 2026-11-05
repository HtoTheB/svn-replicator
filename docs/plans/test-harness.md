# Test harness plan

Goal: a deterministic test harness runnable on GitHub Actions that pins the
exact Git output of the tool. Each fixture is a ready-made SVN source
(committed) plus a committed golden git reference; the harness runs the tool
on the source and requires the produced mirror to match the golden
**byte-for-byte in identity** (ref map + every commit, tree and blob hash),
in addition to a structural check that acts as the diagnostic.

**Status: DONE 2026-11-02 (golden-reference design); extended 2026-11-05
with six edge-case fixtures (10 total).** All fixtures green against
their committed goldens. The golden check was proven to fire
on three injected faults — broken origin resolution, reverted sticky-ADDED
aggregation, and a hash-level nondeterminism leak (randomized committer
identity, which the structural check alone cannot see) — and to go green
again after restore. The tool's output hashes were confirmed to be a pure
function of the SVN source: a `repo.svn` committed to git, cloned to a fresh
location and re-read produced an identical 18/18 object set.

## Layout

```
tests/
  run_tests.py        # harness entry point (stdlib only, runs the tool as a subprocess)
  svn_fixture.py      # authoring helper (builds a fixture repo.svn from a scripted history)
  repos/
    <name>/
      repo.svn/         # committed SVN source (svnadmin layout; read at test time)
      build_svn_repo.py # authoring script: replays the history into a fresh repo.svn
      expected*.json    # structural assertions, one per (config, expectation) pair
      <stem>.git        # committed golden git reference (bare clone of the tool's output)
.github/workflows/ci.yml  # install deps, run harness
docs/plans/test-harness.md  # this file
```

`repo.svn/` and `<stem>.git` are the committed test data. `build_svn_repo.py`
and `svn_fixture.py` are the authoring tools used to (re)create them — they
are not run by the harness at test time.

## Design

- **Static sources, committed.** Each fixture's SVN repository is a committed
  `repo.svn/` directory (a plain `svnadmin` layout, ~200-250 KB, no
  symlinks), read at test time via a `file://` URL. Nothing is generated at
  test time, so the input is identical on every machine and every run — the
  premise that makes hash comparison possible.
- **Golden git reference, committed.** `<stem>.git` is a bare clone of the git
  repository the tool is expected to produce. The harness compares the
  produced mirror to it by identity: same ref map (all heads and tags) and the
  same reachable object set via `git rev-list --objects --all` — i.e. every
  commit, tree and blob hash matches, commit hashes included. This is the
  guard: it pins the exact output so any change between tool versions that
  alters the result is caught.
- **Structural check (the diagnostic).** The produced mirror is also checked
  against `expected*.json`: ref set (incl. `del/...` refs when `keepDeleted*`
  is on); per ref, oldest first — SVN revision (Svn-Revision trailer), subject,
  parent sequence (branch+revision pairs from the parents' trailers), optional
  per-commit tree contents; both trailers present and Svn-Revision unique per
  branch; exact `[copy-ancestor]` INFO/WARNING counts and exit code. When the
  golden check fails it only says "hashes differ"; the structural check names
  the branch, commit and parent that drifted. The two are complementary.
- **Pre/post forms per feature.** Each fixture contains scenarios in both forms
  in the SAME run: the pre-conversion form (branch created without copy-from →
  legacy parent preserved) and the post-conversion form (branch created via
  `svn cp` → parented to the copy-from source), so one run proves both that the
  new behavior works and that it does not leak into the old one.
- **One expectation file = one (config, golden) pair.** Fixtures may have
  several (e.g. `lifecycle` runs with keep-deleted on and off, each with its
  own `<stem>.git`).
- **Regeneration.** After an intentional behavior change, update the matching
  `expected*.json` first, then `python tests/run_tests.py --regen` rewrites the
  goldens. The structural check runs BEFORE the golden is rewritten, so a
  golden is always consistent with its committed expectation file.

## Test repos

1. **basic** — plain branch (pre form: no copy-from, legacy parent, behavior
   unchanged), copied branch (post form: parent = origin's largest commit at or
   before `copyfrom-rev`, which is deliberately NOT the currently active
   branch), copied tag, trunk modifications interleaved.
2. **sticky-add** — one revision that copies a branch root AND adds several
   descendants (root entry listed first in the revision's path list): the
   branch must still be recognized as a creation and parented to the origin,
   not downgraded to MODIFIED.
3. **lifecycle** — branch created via copy, deleted, later RE-CREATED under the
   same name (fresh branch, parented to the origin — not re-parented onto the
   old lifetime); deleted tag; run with `keepDeleted*` on (expects `del/...`
   refs) and off (expects them gone).
4. **deviation** — a branch copied from a source that is in `ignoredFolders`
   (no mirrored origin commits) and one copied from a path outside the
   configured folders (unmappable origin): both must fall back to legacy
   parenting, log exactly one `[copy-ancestor]` WARNING each naming the
   assigned parent, and the run must still exit 0.
5. **tag-recreate** — a tag cut, deleted and cut again under the same name
   (the tag-side counterpart of the lifecycle re-creation): the new tag
   starts a fresh `tag/<name>` lifetime parented to the origin, the deleted
   lifetime is preserved as `del/tag/<name>@<rev>` (keep on) or gone (keep
   off), and the live tag ref is force-updated to the new lifetime's tip.
6. **branch-copy** — branches copied from another branch (not trunk) as a
   two-hop chain (b1 from trunk, b2 from b1, b3 from b2), with trunk updates
   interleaved so the active branch at each copy is never the copy-from
   source: every copy must be parented to its origin (b2 to b1, b3 to b2),
   not to the active branch tip, and origin resolution must work through a
   branch that was itself copy-created.
7. **subset-copy** — a branch created by copying a SUBFOLDER of another
   branch's root (`branches/feature/sub` -> `branches/fromsubset`): the
   copy-from path inside the feature branch must resolve to the containing
   branch and parent the new branch to `feature`'s commit at or before the
   `copyfrom-rev`, and the new branch's content is the subfolder's content
   at its root.
8. **plain-delete** — a branch created WITHOUT a copy (plain mkdir + files)
   that is later deleted: the deletion handling is the same as for
   copy-created branches (`del/<name>@<rev>` / gone), but the commits keep
   the legacy parent and no `[copy-ancestor]` log lines are produced.
9. **replace-root** — the root of an existing branch is REPLACED by a copy of
   another branch in one revision (root `R` with copy-from). Consciously
   pinned as a continuation (the open B4/B5 question): a normal modification
   commit on the branch, same lineage, no re-parenting, no `[copy-ancestor]`
   log, content swapped wholesale to the source's content. The build script
   needs a working-copy `delete` before the `cp` (a working-copy copy into an
   existing path would copy INTO it); `SvnFixture.wc_replace` encodes that.
10. **skip-revisions** — two revisions skipped via `svn.skipRevisions` (a
    trunk change and a branch change): no commit is produced for them, and a
    branch copy whose `copyfrom-rev` IS the skipped trunk revision still
    resolves to the largest MIRRORED trunk commit at or before it.

## Why hash comparison is valid (and a leak is a tool flaw)

A commit hash is a function of (tree, parents, author name+email, committer
name+email, author date, committer date, message). The tool pins every one of
these to the SVN source: the author/committer email is derived
(`{author}@example.invalid`, not ambient config), the dates are the SVN log
entry dates (frozen in a committed repo), the message is the SVN commit
message, and `LC_ALL=C` is forced for deterministic text. There is no
wall-clock or machine identity in the output. Therefore the produced git repo
is a pure function of the SVN source — verified empirically (a `repo.svn`
through a git commit→clone round-trip re-read produced an identical object
set). **Any future change that makes the output differ across runs or
machines for the same source is a fundamental flaw of the tool**, and the
golden check is the regression test for exactly that property.

## CI

`ci.yml` (ubuntu-latest): install `subversion` via apt, `uv sync`, then run
the harness. One matrix job per fixture (`tests / <fixture>` over
`basic`, `deviation`, `lifecycle`, `sticky-add`, `tag-recreate`,
`branch-copy`, `subset-copy`, `plain-delete`, `replace-root`,
`skip-revisions`), `fail-fast` off so a
failure is visible per fixture and the rest still run; the trigger is every
push plus `workflow_dispatch`. The committed SVN sources are read in place
(no generation step). The `svn` (PySvn) dependency is pure Python and shells
out to the `svn`/`svnadmin` binaries, so only the `subversion` apt package
is required — no C compiler or `-dev` headers.

## Findings (non-obvious behaviors the authoring tools encode)

These were discovered while making the fixtures deterministic; each is pinned
by the committed `repo.svn` + golden, so a regression is caught.

- **Working-copy copies record the SOURCE DIRECTORY's peg revision**, not the
  repository HEAD, as `copyfrom-rev`. A URL copy (`svn cp URL URL`) records
  the repo HEAD. After a commit, the working copy's source-dir peg is one
  revision behind the HEAD, so a working-copy copy made without an explicit
  `svn update` records a stale peg. `svn_fixture.SvnFixture.update()` exists
  for exactly this; the sticky-add build script calls it so the branch forks
  trunk at its tip.
- **`svn add` is recursive by default.** Adding a parent directory also adds
  any already-added children, so a follow-up `svn add` on a child that is
  already versioned fails with `E200009`. Build scripts add directories and
  files in one call, not separately.
- **A checkout of an empty repository has base revision 0.** The fixture
  commits a `.root` marker first so the working copy starts at r1; this also
  keeps `copyfrom-rev` values from being pegged at r0.
- **A `repo.svn` is a portable committed artifact.** A plain `svnadmin`
  repository directory (db/conf/hooks/locks, no symlinks, no sockets after a
  clean run) commits to git and is fully readable after a fresh checkout:
  `svn log`, `svn log -v --xml` (copyfrom data) and `svnadmin verify` all
  work on the cloned copy. This is what makes static committed sources
  practical.
- **A branch's first-parent walk includes its ancestors.** The mirror's
  `trunk` commit appears in a copied branch's `git log`, so the per-ref
  sequence check filters to the commits carrying that ref's own
  `Svn-Branch` trailer; the ancestry is pinned separately by the per-commit
  parent-identity assertions.
