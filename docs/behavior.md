# Expected behavior of svn-replicator

This is the normative specification of what the tool is supposed to do: given an SVN repository and a configuration, exactly what Git repository it should produce. It is written for a human reader — it states the intent behind each behavior, and where a decision is a judgment call rather than an obvious consequence, it says so.

The golden test harness (see `test-harness.md`) pins this behavior: every rule below that has a fixture is regression-tested, and the last section maps each section of this document to the fixture(s) that pin it. Where the harness and this document disagree, the document is the intent and the fixture encodes what the tool currently does — surface the mismatch, do not silently pick a side.

## 1. What the tool does

svn-replicator mirrors an SVN repository into a Git repository. It replays every SVN revision from 1 to HEAD in order and produces a **branch-aware transcript** of the SVN history: one Git commit for every change to every mirrored branch, so the Git graph preserves *which* branch each change landed on and, for copies, *where a branch came from*.

The tool is a **full replay with no persistent state.** Every run wipes the Git working directory and rebuilds the whole mirror from the SVN source. The Git repository itself is the only "index" the tool keeps (see §6) — there is no external database or sidecar that records what has already been mirrored. Consequences:

- Re-running the tool against an unchanged SVN source produces an identical Git repository (same commits, same hashes — see §2.4).
- Re-running it after the SVN source grew re-derives everything; the result is whatever the full replay of the current source yields.
- The tool maintains a local Git mirror. It does **not** push to a Git remote; pushing the mirror is the operator's job.

## 2. The mirror model

### 2.1 Branch layout

SVN is organized into three folder roots, given by the config (`trunkFolder`, `branchFolder`, `tagFolder`, defaulting to `/trunk/`, `/branches/`, `/tags/`). The mapping to Git is:

| SVN path | Git branch | Git tag |
|---|---|---|
| `/trunk` (or below) | `trunk` | — |
| `/branches/<name>` (or below) | `<name>` | — |
| `/tags/<name>` (or below) | `tag/<name>` | `<name>` (lightweight) |

The **unit of mirroring is the first-level folder** under `/branches/` and `/tags/`, plus `/trunk` itself. There is no nested-branch concept: a change under `/branches/a/b/...` belongs to the branch `a`, not to a branch `a/b`. Any SVN path that does not fall under one of the three roots is **not mirrored** (it produces no commit on any branch).

Each `tags/<name>` folder yields **two** Git objects: a branch `tag/<name>` that carries the tag's history, and a lightweight tag `<name>` (see §4).

### 2.2 One commit per branch per revision

For a revision that touches *k* mirrored branches, the tool creates *k* Git commits — one per branch. Each commit's **tree is the full contents of that branch's root at that revision** (the branch root is checked out at the revision, then committed). Files appear rooted at the branch: a change to `/branches/b1/sub/s1.txt` shows up as `sub/s1.txt` in the branch's tree, not as `branches/b1/sub/s1.txt`.

Because commits are keyed by (branch, revision) and there is at most one change aggregate per branch root per revision, **a given branch never has two commits for the same SVN revision.** This uniqueness is what makes revision-based origin lookup (§6) unambiguous.

### 2.3 Commit identity

Every mirrored commit carries:

- **Subject** — `r<rev>: <svn message>` when `git.commitMsgPrefix` is true (the default); just `<svn message>` when false.
- **Two trailers**, in the message's final paragraph, in this order: `Svn-Revision: <rev>` and `Svn-Branch: <git-branch-name>`. These are attached in both prefix modes. They are the tool's self-annotation: the mapping from a Git commit back to its SVN source (which revision, on which branch) is recoverable from the Git repository alone.
- **Author and committer name** — the SVN commit's author.
- **Author and committer email** — derived as `<author>@example.invalid`, independent of any ambient Git identity.
- **Author and committer dates** — the SVN log entry's date (frozen in the committed source), not the wall clock.

### 2.4 Determinism

A commit hash is a function of (tree, parents, author/committer name+email, author/committer dates, message). The tool pins every one of those to the SVN source (§2.3) and forces `LC_ALL=C` for deterministic text processing. There is no wall-clock or machine identity in the output.

Therefore **the produced Git repository is a pure function of the SVN source and the configuration.** Any change that makes two runs over the same source differ — a hash, a parent, a ref — is a defect in the tool, and the golden check in the test harness is the regression test for exactly that property.

## 3. Revision replay and branch-change classification

The replay loop walks SVN revisions 1 to HEAD in ascending order.

- **Skipped revisions** (`svn.skipRevisions`) produce no commits at all. They leave gaps in the mirror: the commits that would have landed at those revisions are absent, and the surrounding commits parent across the gap. (See §8.)
- For each remaining revision the tool reads the revision's changed paths (with their SVN action and, for copies, the copy-from source and revision) and aggregates them into **one branch change per affected branch root**:

  | SVN observation on a branch root | Classification | Meaning |
  |---|---|---|
  | root directory added, with copy-from | ADDED (copy-creation) | branch created by `svn cp` |
  | root directory added, no copy-from | ADDED (plain) | a brand-new directory |
  | any change at or below the root | MODIFIED | the branch changed |
  | root directory deleted | DELETED | the branch was removed |

  Two details matter:

  - **A root `R` (replace) classifies as MODIFIED, not as a new branch.** The tool maps SVN action `R` to `M`, so replacing an existing branch's root is treated as a change to that branch — a continuation, not a re-creation (§9 pins this deliberately).
  - **A copied-root ADDED is "sticky."** In a single revision the copied root directory may be listed together with several descendant adds. No matter the order the paths arrive in, a root add that carries copy-from must win the classification: the branch is a copy-creation, and later descendant changes in the same revision must not downgrade it to MODIFIED. (A plain ADDED with no copy-from is *not* sticky and downgrades to MODIFIED if a descendant change is aggregated — preserved from the original behavior.)

- A DELETED-with-simultaneous-inner-change in one revision is not expected to occur; the aggregator raises rather than guess. No real repository exhibits it (see §10, known open behaviors).

## 4. Branches and tags

- **Branches** are plain Git branches named per §2.1.
- **Tags** are represented by a `tag/<name>` branch *and* a lightweight Git tag `<name>`. Every time the `tag/<name>` branch receives a commit, the tag is (re)created with force to point at the branch's newest commit. The tag therefore always points at the tip of its `tag/<name>` branch. It is a lightweight tag (points directly at a commit, no tag object).

## 5. Parenting

The parent of a new commit is decided by the branch's state and how the change was made. This is the heart of the tool.

### 5.1 Existing branch — continuation

If the Git branch already exists, the new commit is parented to the branch's current tip. **An existing branch is never reset or re-parented**, regardless of how the SVN change was made (including a root replace — §9). This is what keeps a branch's Git history a single linear line matching its SVN life.

### 5.2 New branch, created by copy — origin parenting

If the change is an ADDED whose root carries a copy-from, **and** the destination Git branch does not exist yet, the first commit is parented to the **origin**: the mirrored commit of the copy-from source at (or before) the `copyfrom-rev`.

- **Origin branch** — the copy-from path is mapped through the §2.1 layout to the Git branch it points at. A copy from a *subfolder* of a branch resolves to the **containing branch** (e.g. copying `branches/feature/sub` to a new branch resolves to `feature`). A copy from a tag resolves to the `tag/<name>` branch.
- **Origin commit** — the largest-mirrored commit on that origin branch whose `Svn-Revision` is ≤ the `copyfrom-rev` (an exact match wins when present).
- **Content** — the new branch's root tree is the **source's content at the copy point**, placed at the new branch's root. A subfolder copy places that subfolder's content at the new root.

This is what makes a copied branch fork from the right place in the graph instead of from whatever branch the replay happened to have active.

### 5.3 New branch, created without copy — legacy parenting

If the change is an ADDED with **no** copy-from (a plain new directory), the branch is created at the current HEAD — i.e. it is parented to the commit the replay was on at that moment. This is the original, pre-copy-origin behavior and is preserved unchanged.

### 5.4 Fallback when the origin is unresolvable

Origin parenting applies only when the origin can actually be resolved. When it cannot, the tool **falls back to legacy parenting (§5.3)** — the branch is parented to the current HEAD — and **announces the deviation with a WARNING** that names the parent it actually assigned. The fallback never aborts the mirror.

The origin is unresolvable, and the run is a warning, in exactly these cases:

| Reason | What it means |
|---|---|
| copy-from path does not map to a mirrored branch | the source is outside `/trunk`, `/branches`, `/tags`, or is a deleted lifetime not preserved |
| `copyfrom-rev` missing or unparseable | the SVN copy record is incomplete |
| `copyfrom-rev` ≥ current revision | a copy that points at the same or a later revision (not present in practice; guarded) |
| no mirrored commit on the origin at or before `copyfrom-rev` | the source was ignored/skipped, predates the mirror, or its deleted lifetime is not preserved (`keepDeletedBranches` off) |

Log lines (prefix `[copy-ancestor]`, emitted **after** the commit so the parent reported is the one actually written):

- Success: `[copy-ancestor] r<rev>: branch '<name>' copied from '<path>@r<n>' → parented to <sha8> (origin '<origin-branch>', mirrored r<m>)`
- Fallback: `[copy-ancestor] r<rev>: branch '<name>' copied from '<path>@r<n>' but origin unresolvable (<reason>) → assigned parent: <sha8> (<subject>) instead` — or `assigned parent: none (root commit)` when the fallback parent is a root commit.

The fallback must name the **actual** assigned parent (read from the commit just written), never a predicted one — the whole point is that a deliberate deviation from the SVN history is visible to the operator and says where the branch actually landed.

### 5.5 The scope gate

Origin parenting is narrow by design. It applies only when **all three** hold: the change is an ADDED, the root carries a copy-from path, and the destination Git ref does not exist yet. Any other case — a plain new directory, an existing branch, a root replace — is parented per §5.1/§5.3 and never re-parented.

## 6. Origin lookup: the Git repository as the revision index

To find "the origin commit at or before `copyfrom-rev`", the tool queries the Git repository it is building — there is no separate history map. Each commit carries the `Svn-Revision` and `Svn-Branch` trailers (§2.3); a lookup considers only commits whose `Svn-Branch` matches the origin branch and picks the largest `Svn-Revision` ≤ `copyfrom-rev`.

This works in a full replay because, by the time a copy is processed, the origin branch's earlier commits have already been written in the same pass (the `copyfrom-rev` is always < the current revision for a real copy). It is also what a future incremental mode would rely on: the origin's commits are already present from earlier runs.

Because a branch's first-parent walk in Git includes the ancestry it was forked from (its origin's commits appear in its `git log`), the lookup scopes strictly by the `Svn-Branch` trailer rather than by ref membership.

## 7. Deletion and the `keepDeleted*` settings

When a branch's root is deleted, the Git branch is either preserved or removed, per `git.keepDeletedBranches`:

- **on** — the branch is **renamed** to `del/<name>@<rev>` (for a tag: `del/tag/<name>@<rev>`, where `<rev>` is the deletion revision). Its commits are retained and remain queryable — which is what lets a later copy resolve an origin into a deleted lifetime.
- **off** — the branch is **deleted** from Git. No trace remains, and any later copy from it is an unresolvable origin (§5.4).

There is a **deliberate asymmetry** for tags, which is easy to trip over:

- The **`tag/<name>` Git branch** behind a tag is governed by `keepDeletedBranches` (renamed to `del/tag/<name>@<rev>`, or deleted).
- The **lightweight tag object** `<name>` is governed by `keepDeletedTags` (kept, or removed).

So the two settings can independently preserve the tag's history-branch and the tag pointer.

**Re-creation under the same name** (a branch or tag deleted and later created again) is a **fresh lifetime**, not a continuation: the new branch is parented per §5.2/§5.3 to its origin (or current HEAD), and the old lifetime is preserved as `del/<name>@<rev>` when the corresponding `keepDeleted*` setting is on. The live ref and (for tags) the tag pointer move to the new lifetime's tip. A re-created branch is therefore *not* re-parented onto the deleted lifetime.

## 8. `skipRevisions` and `ignoredFolders`

- **`svn.skipRevisions`** — a list of SVN revisions that produce no commits. They create gaps: no commit exists for a skipped revision, and the commits around it parent across the gap. Because origin lookup only ever sees *mirrored* commits, a copy whose `copyfrom-rev` lands on a skipped revision resolves to the largest **mirrored** origin commit at or before it — never to the skipped one.
- **`svn.ignoredFolders`** — a list of SVN folder roots that are not mirrored at all. A branch root under an ignored folder produces no commits. It also makes any copy whose origin is inside an ignored folder an **unresolvable origin** (§5.4) → legacy fallback + WARNING, because there are no mirrored origin commits to land on.

## 9. Replacing a branch root (root `R`)

When an existing branch's root is **replaced** by a copy of another branch in a single SVN revision, the SVN record is a root `R` (with copy-from) plus the copied children. The tool classifies the root `R` as MODIFIED (§3), so the mirror treats it as a **continuation of the existing branch**:

- the replace revision is an ordinary modification commit **on the existing branch** — same lineage, parent = the branch's own previous commit;
- the branch is **not** re-parented to the copy source, and **no** `[copy-ancestor]` line is emitted;
- the branch's content is **swapped wholesale** to the source's content at the replace revision, and subsequent edits build on top of the swapped content.

This is a **consciously pinned decision**, not an accident: whether a root replace should instead re-parent the branch to its new source is an open question (tracked as B4/B5 in the analysis plan). The current behavior — treat it as a change, keep the old lineage — is the pinned expectation and is regression-tested; changing it is a deliberate spec change, not a bug fix.

## 10. Known open behaviors

These are current, tested (or documented) behaviors that are not settled as "obviously right" and are called out so a reader does not mistake them for overlooked bugs:

- **Root replace keeps its lineage** (§9). The re-parenting question is open.
- **A root-delete together with an inner change in one revision raises** rather than guessing. No real repository exhibits the shape; if one appears, the classification needs a decision.
- **Nested paths are not branches.** `branches/a/b` is part of branch `a`. If nested independent branches are ever a requirement, this is a design change.

## 11. How this behavior is pinned

The golden harness (`tests/run_tests.py`, design in `test-harness.md`) makes the rules above executable. Each fixture is a committed SVN source plus a committed golden Git reference; the harness replays the source and requires the produced mirror to match the golden object-for-object (commit hashes included), on top of a structural check that names any drifted branch, commit or parent. The mapping from this document to the fixtures that pin it:

| Section | Behavior | Fixture(s) |
|---|---|---|
| §2.1, §2.2 | layout, one commit per branch, branch-rooted trees | `basic` |
| §3 | ADDED/MODIFIED/DELETED classification | `basic`, `plain-delete` |
| §3 (sticky) | copied-root add survives descendant changes | `sticky-add` |
| §5.2 | copy-origin parenting | `basic` |
| §5.2 (branch→branch, chain) | origin through a copy-created branch | `branch-copy` |
| §5.2 (subfolder) | copy from a subfolder resolves to the containing branch | `subset-copy` |
| §5.3 | plain (no-copy) new branch keeps legacy parent | `basic`, `plain-delete` |
| §5.4 | unresolvable origin → fallback + WARNING | `deviation` |
| §5.5, §4 | scope gate; tag ref force-updated | `basic`, `tag-recreate` |
| §7 | deletion, `keepDeleted*`, `del/` refs, tag asymmetry | `lifecycle`, `plain-delete` |
| §7 (re-create) | fresh lifetime on re-creation, old lifetime preserved | `lifecycle`, `tag-recreate` |
| §8 | skipped revisions leave gaps; origin across a gap | `skip-revisions` |
| §9 | root `R` replace → continuation, content swapped | `replace-root` |
| §2.4 | output is a pure function of the source | all (golden guard) |

When a rule here is changed on purpose, update the matching `expected*.json` first, then regenerate the golden (`python tests/run_tests.py --regen`); the structural check gates the regeneration so a golden is always consistent with its expectation file.
