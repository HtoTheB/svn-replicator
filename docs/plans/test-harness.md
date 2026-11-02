# Test harness plan

Goal: a deterministic test harness runnable on GitHub Actions. It builds a small
set of purpose-built SVN repositories, runs the tool on them, and checks that
the resulting Git mirrors are exactly as specified (refs, per-ref commit
sequence, subjects, parents, trailers, log output).

**Status: DONE 2026-11-02.** All four fixtures green. A mutation test confirmed
the harness FAILS when the copy-ancestor feature is broken (origin resolution
disabled, or the sticky-ADDED aggregation reverted) and PASSES again after
restore. See "Findings" for the non-obvious Subversion behaviors the fixtures
had to account for.

## Layout

```
tests/
  run_tests.py        # harness entry point (stdlib only, runs the tool as a subprocess)
  repos/
    <name>/
      build_svn_repo.py   # builds the SVN fixture from scratch (svnadmin + scripted history)
      expected*.json      # assertion files, one per (config, expectation) pair
.github/workflows/ci.yml  # install deps, run harness
docs/plans/test-harness.md  # this file
```

## Design

- **Fixtures are generated, not stored.** Each test repo has a `build_svn_repo.py`
  that creates an `svnadmin create` repository and replays a small scripted
  history with pinned commit dates (`--date`) and fixed messages/author. The
  generated repo is stable across machines; nothing is committed from the harness.
- **Pre/post forms per feature.** Each fixture contains scenarios in both forms
  where applicable, in the SAME run: the *pre-conversion form* (a branch created
  without copy-from → must keep the legacy parent; old behavior preserved) and
  the *post-conversion form* (a branch created via `svn cp` → must be parented
  to its copy-from source) — so one run proves both that the new behavior works
  and that it does not leak into the old one.
- **Deterministic check.** The harness runs the tool on the generated repo
  (generated config, working dirs under the repo), then walks every
  `refs/heads/*` of the produced mirror with `git log` and compares against the
  expectation file:
  - ref set (incl. `del/...` renamed refs when `keepDeleted*` is on)
  - per ref, oldest first: SVN revision (Svn-Revision trailer), commit subject,
    parent sequence (as branch+revision pairs read from the parents' trailers)
  - optional per-commit file contents (the git tree is compared by content)
  - both trailers present on every commit; `Svn-Revision` unique per branch
  - exact `[copy-ancestor]` INFO/WARNING counts and the run's exit code
- **One expectation file = one (config, expected-mirror) pair.** Fixtures may
  have several (e.g. `lifecycle` runs with keep-deleted on and off).

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

## CI

`ci.yml` (ubuntu-latest): install `subversion` via apt, `uv sync`,
`.venv/bin/python tests/run_tests.py`. Single job; fixtures are generated in
the runner's temp dir. The `svn` (PySvn) dependency is pure Python and shells
out to the `svn`/`svnadmin` binaries, so only the `subversion` apt package is
required — no C compiler or `-dev` headers.

## Findings (non-obvious behaviors the fixtures encode)

These were discovered while making the fixtures deterministic; each is now
pinned by an expectation so a regression is caught.

- **Working-copy copies record the SOURCE DIRECTORY's peg revision**, not the
  repository HEAD, as `copyfrom-rev`. A URL copy (`svn cp URL URL`) records
  the repo HEAD. After a commit, the working copy's source-dir peg is one
  revision behind the HEAD, so a working-copy copy made without an explicit
  `svn update` records a stale peg. `svn_fixture.SvnFixture.update()` exists
  for exactly this; the sticky-add fixture calls it so the branch forks trunk
  at its tip.
- **`svn add` is recursive by default.** Adding a parent directory also adds
  any already-added children, so a follow-up `svn add` on a child that is
  already versioned fails with `E200009`. Build scripts add directories and
  files in one call, not separately.
- **A checkout of an empty repository has base revision 0.** The fixture
  commits a `.root` marker first so the working copy starts at r1; this also
  keeps `copyfrom-rev` values from being pegged at r0.
- **Commit SHAs are not compared.** `svn 1.14` has no `--date` on commit, so
  commit timestamps are wall-clock and differ between runs. The harness
  compares tree object contents (content-addressed), the parent graph
  (branch+revision identity via trailers), subjects, and log counts instead.
- **A branch's first-parent walk includes its ancestors.** The mirror's
  `trunk` commit appears in a copied branch's `git log`, so the per-ref
  sequence check filters to the commits carrying that ref's own
  `Svn-Branch` trailer; the ancestry is pinned separately by the per-commit
  parent-identity assertions.
