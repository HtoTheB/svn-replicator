# Copy-ancestor branch parenting (svn cp → correct git parent)

Implementation plan + detailed instructions.
Branch: `copy-ancestor-parenting` (from `keepAncestorOnCopy` @ 3e5d9b1).
State: **implemented + verified 2026-10-30** — plan committed as `1961998`, feature as `f22057d` on branch `copy-ancestor-parenting`. All §8 assertions pass (see §11). 2026-10-30 follow-up (owner): the `copyAncestors` flag was REMOVED — copy-origin parenting is now unconditional default behavior (verification is done; the old parenting was plainly wrong 3 of 5 times, one branch even parented from the wrong branch). The remaining safety rails are unconditional: missing-destination-ref gate + D5 fallback-with-WARNING. All new code comments were also rewritten to a neutral, reader-facing style (no plan refs, no conversational tone).

## 0. Process & decisions log (append-only — keep current across compactions)

| Date | Source | Entry |
|---|---|---|
| 2026-10-28 | me + @sol (1/30 rounds) | Draft: single-phase resolve-before-commit; parent = origin commit at-or-before `copyfrom-rev`. |
| 2026-10-29 | owner | **No in-memory history map** — tool must track no persistent state (future incremental monitor). Origin lookup = query of the git repo itself. |
| 2026-10-29 | me | Evaluated git-notes vs commit-trailers (both tested in env). Trailers chosen. |
| 2026-10-29 | owner | **D3a: `Svn-Revision` trailer attached to EVERY mirrored commit unconditionally**, regardless of `commitMsgPrefix` (prefix+trailer redundancy accepted; no external consumers yet). |
| 2026-10-29 | owner | **D5: whenever the mirror deliberately deviates from SVN history (esp. unknown/unresolvable copy origin), log a WARNING stating EXPLICITLY which parent was actually assigned.** Mirror should be as close to SVN as possible without hurting usability; deliberate edge-case decisions must be visible to the end user. |
| 2026-10-29 | owner | New branch `copy-ancestor-parenting`; plan verified with @gpt-6-sol, detailed instructions checked with @gpt-6-astra (≤ few calls, 10 available); review gate before implementation. Record all decisions/findings here. |
| 2026-10-29 | @gpt-6-sol (1/15 calls) | Verdict **APPROVE-WITH-CHANGES**, 5 required changes (S1–S5 below). All verified against code/data by me; all incorporated. |
| 2026-10-29 | @gpt-6-astra (2/10 calls; 3rd retry hit a provider quota error and returned no content, but the 2nd call's full verdict was recovered) | Verdict **BLOCK**, 11 required fixes + 3 nits (A1–A11, N1–N3 below). All verified against code/data by me; **all incorporated** into §4, §6, §7, §8. |
| 2026-10-29 | me (verification) | Empirical results §3 (trailer parsing, WC pinning, dataset scan, path matching, ref API). |
| 2026-10-30 | owner | **`copyAncestors` flag removed** — the old (HEAD-parented) behavior was plainly wrong (TRDPSec2.1.0.0 parented from a different branch), not an optional style choice. Parenting is now unconditional default behavior; the missing-destination-ref gate and the D5 fallback-with-WARNING remain as safety rails. All new-code comments rewritten to a neutral reader-facing style. |
| 2026-10-30 | owner | **`r<rev>:` subject-line fallback in `svn_revision_of` removed** — no backward compatibility with pre-trailer commits needed (the tool rebuilds the mirror every run; branch-scoping already made such commits unusable for lookup). The trailer is now the sole source for `svn_revision_of`; absent/malformed → `None` → D5 fallback. |

### Sol's required changes and their disposition (S1–S5)

- **S1 — `getRevisionMetadata` must use its `revision` parameter, not `info()["commit#revision"]`.**
  *My verification:* PySvn `commit#revision` empirically equals the pinned revision at 10 pins (r1…r2000) in a throwaway WC, and the existing 1536-commit mirror has 1536 **unique** r-numbers — so the current code happened to work. *Disposition: ACCEPTED anyway* — using the passed `revision` removes the dependency on PySvn field semantics and drops one `info()` call per commit. §7.4.
- **S2 — `iter_commits(refs)` + "first ≤ copyfrom_rev" is NOT a correct branch index**: git also reaches *other branches' ancestors* (a branch's history includes its origin's ancestry); with multiple `del/...` refs there is no global newest-first guarantee. **Add a branch-identity annotation: `Svn-Branch: <git-branch-name>` trailer on every commit**; lookup only considers commits whose `Svn-Branch == branch_name`, picks the **largest** `Svn-Revision ≤ copyfrom_rev`; don't silently resolve ambiguity.
  *My verification:* ancestor-leakage confirmed real; confirmed the trailer mechanism — **critical detail: all trailers must live in the message's final paragraph with NO blank lines between them** — a blank line between `Svn-Revision:` and `Svn-Branch:` makes GitPython `trailers_dict` silently drop the first (tested: returned `{'Svn-Branch': ['trunk']}` only). Same-paragraph two trailers parse correctly in both `trailers_dict` and `git interpret-trailers`. *Disposition: ACCEPTED.* §7.4, §7.6.
- **S3 — Incomplete copy metadata must hit the D5 fallback, not slip past the gate.** Gate = presence of `copyfrom_path` (not both fields); missing/invalid `copyfrom-rev` or `getBranchInfos() is None` ⇒ unresolvable origin ⇒ D5 warning. The warning must be emitted **after the commit** and report the **actual** parent from `new_commit.parents` (handles the unborn-HEAD root-commit case: "none (root commit)"). *Disposition: ACCEPTED.* §7.5.
- **S4 — `switch_branch` must honor `base_commit` even when HEAD is unborn** (the current early-return at `my_git.py:79-82` would drop it). Existing refs are never reset; a `del/...` ref is a different name (no collision with its original). *Disposition: ACCEPTED, with A10's correction that the rewrite must also preserve the unborn-HEAD + no-base + `create_if_missing=False` retarget-today path.* §7.6.
- **S5 — Verification commands were wrong**: typo `--commit-ancestors` (is `--copy-ancestors`); `--grep 'Svn-Revision: 1821'` alone doesn't prove branch membership. Use the branch-scoped lookup's own criteria + `new_commit.parents`. *Disposition: ACCEPTED, superseded by the executable Python verifier in §8.* §8.

### Astra's required changes and their disposition (A1–A11)

Astra did a final line-by-line check of §7 against the actual source (7 read-only calls; verdict **BLOCK**). All fixes verified by me against code/data and incorporated.

- **A1 — §7.2 replacement range was wrong.** Stated range "lines 111–127" includes the `for p in xml_changes:` loop and the `if not p.text: continue` guard, which the replacement omitted. → *Fix:* replace lines **115–127 inside the existing loop**, retaining 111–113. §7.2.
- **A2 — §7.3 named the wrong function location and missed a behavior bug.** `BranchChange` (the dataclass) is in `src/branchChange.py`, but **`findAffectedBranches` (the aggregation) is in `src/my_svn.py:132`** — the aggregation edit belongs there. The existing root-ADD arm does **not** fall through when `current is not None`. The proposed inner `elif` is correct but **insufficient on its own**: a descendant change arriving *after* a copied root A still downgrades it to MODIFIED in the final `else`. **This is the real r1823 XML shape** (the copied root `/branches/trdp-mm` precedes five descendant `A` entries in the same revision), so the feature would silently miss that creation. → *Fix (two edits):* (a) root-ADD arm carries copy fields and upgrades a MODIFIED→ADDED when a copied root A follows a descendant; (b) final `else` becomes conditional — preserve `ADDED` when `copyfrom_path is not None`. Verified behavior matrix in §7.3. **This is the single most important implementer trap.**
- **A3 — §4/§7.6 candidate-ref enumeration was broken.** Both `repo.references` and `repo.refs` exist, but **`str(ref)` returns the name (`tag/trdp`), not the full ref (`refs/heads/tag/trdp`)** — so the `del/...` prefix test on `str(ref)` never matches. Also, unconditionally walking a **nonexistent** live ref raises `GitCommandError`, violating D5's never-abort guarantee. → *Fix:* build candidates from **existing** references using `r.path`; dedupe visited commit SHAs before tie detection (the same commit reached via several refs is **not** an ambiguous revision; only warn on *distinct* commits tied at the chosen rev). §7.6.
- **A4 — §4/§7.5 omitted D6's missing-destination-ref gate.** Without it, `switch_branch` correctly preserves an existing branch, but the processor would falsely report that it parented the new commit to the resolved origin. → *Fix:* extend the resolution condition with `and branchName not in {h.name for h in gitRepo.heads}`. §4, §7.5.
- **A5 — §6/§8/§9 had the folder-config advice backwards (B1 was a false positive).** Verified: `PurePath("/trunk/").is_relative_to`-style matching via `getBranchInfos` **does** match the real root-absolute log paths (`/trunk/trdp` → `trunk`, etc.); a slash-free config (`trunk`) matches **nothing**. The existing mirror was itself built with the shipped `/trunk/` config. → *Fix:* **B1 retracted** from the bug list; §8 uses the **shipped** folder config (no workaround). §3, §6, §8.
- **A6 — §8 shell locator was broken.** `%s` is the subject, so `--format='%H %s'` never exposes the appended trailers; `c=rev_commit …` was missing `$()`; the locator must enforce branch identity + largest-rev-≤. → *Fix:* replaced with an executable Python verifier that mirrors `svn_revision_of`/`svn_branch_of`/`find_origin_commit`. §8.
- **A7 — §8 baselines were stale.** The old mirror's 1536 trunk commits are **not** a valid full-replay expectation: the SVN log now reaches **r2480** with **2358 trunk-changing revisions**. Raw `%P` equality can't survive changed commit messages and ancestor SHAs. → *Fix:* Run A/B are both **fresh full replays** (the tool wipes the git dir per run); compare **mapped parent identities / graph structure** (parent's own branch+rev via trailers), not raw SHAs. §8.
- **A8 — §8 D5 negative test was ineffective.** `trdp-mm` is a *destination*, not a source, of any copy; and `ignoredFolders` compares **full branch-root paths**, so `[trdp-mm]` wouldn't match anyway. Skipping one origin rev still leaves older eligible commits. → *Fix:* negative run = `--ignored-folders /trunk --copy-ancestors true` ⇒ the 4 trunk-sourced copies (r1809, r1823, r2269, r2428) become unresolved (no mirrored trunk commits) → D5 WARNINGs naming the actual parent; r1824 (from `tag/trdp`, still mirrored) still resolves → INFO. Exit 0. §8 Run C.
- **A9 — resolver contract and D5 messages were inconsistent.** §4 showed a two-value result, §7.5 returned three. → *Fix:* consistent three-tuple `(base, reason, origin_branch)` everywhere; warning includes `@r<n>`; success log uses `svn_revision_of(base_commit)` (not a literal `r{...}`). §4, §7.5.
- **A10 — §7.6 `switch_branch` rewrite changed one more path than claimed.** Today, unborn HEAD + no base + `create_if_missing=False` **retargets HEAD and returns** (the unborn early-return precedes the create check); the proposed rewrite would have raised. → *Fix:* insert an early return for `unborn + base_commit is None` before the creation-permission check, preserving that path. §7.6.
- **A11 — §7's "each step compiles/runs independently, no behavior change until the flag" was false.** §7.4 changes a required signature before its caller is updated; §7.5 imports helpers only introduced in §7.6 and reads config introduced in §7.7; trailers change every message unconditionally. → *Fix:* §7 now states it is **one coordinated change**; trailer emission + `commitMsgPrefix` behavior change unconditionally, only the copy-origin parenting is gated by `copyAncestors`. §7 intro.

### Astra's nits (all applied)

- **N1:** `Optional` reaches `my_svn.py` via `from pathChange import *` (not an explicit typing import) — add `Optional` to the explicit `typing` import for clarity. §7.2.
- **N2:** `copyfrom_rev` field comment = "source snapshot revision (`copyfrom-rev`)" — it is *not* the revision the copy occurred at. §7.1.
- **N3:** guard empty messages before `splitlines()[0]`; a malformed `Svn-Revision` trailer value returns `None` immediately (no subject-prefix fallback in that case). §7.6.

## 1. Goal

When an SVN branch/tag is created via `svn cp`, parent its first Git commit to the mirrored commit of the copy-from source at `copyfrom-rev` — instead of to whichever branch tip the replay loop happened to have active.

## 2. Design decisions (final)

- **D1 — Single-phase, resolve-before-commit.** Resolve source before the branch's first commit; `create_head(name, commit=origin.hexsha)`; normal `commit()` path. Rejected: `commit-tree -p origin -p oldparent` rewrite (duplicate objects; two parents = wrong merge; descendant rewrites + retagging).
- **D2 — No persistent state; the git repo IS the revision index.** Lookup queries the mirror repo; annotations live in the commit objects themselves. Works in full-replay mode (origin commits written earlier in the same pass, `copyfrom-rev < current rev`) and the planned future incremental mode (origin commits already present from earlier runs).
- **D3 — Two commit trailers, always attached (D3a per owner, S2 per Sol):**
  - `Svn-Revision: <rev>` — the mirrored SVN revision.
  - `Svn-Branch: <git-branch-name>` — the git branch (incl. `tag/` prefix) the commit was made on; **required for correct lookup scoping** (S2).
  - Message assembly (trailers in ONE final paragraph, no blank lines between them):
    ```python
    subject_and_body = f"r{rev}: {msg}" if use_msg_prefix else msg
    message = f"{subject_and_body}\n\nSvn-Revision: {rev}\nSvn-Branch: {git_branch_name}"
    ```
  - `commitMsgPrefix: true` (default) → subject `r<rev>: <msg>` + trailer block. `false` → plain `<msg>` + trailer block. Redundancy is deliberate/accepted.
  - Consequence: **every commit message changes** (trailers added) → all commit hashes change vs any pre-existing mirror — accepted.
  - Trailer key matching at lookup is case-insensitive, first value wins.
- **D4 — Parent rule.** Origin branch's mirrored commit with the **largest `Svn-Revision` ≤ `copyfrom-rev`**; exact match naturally wins. One commit per branch per revision (guaranteed by `findAffectedBranches` → unique `BranchChange` per branch root → one commit), so revisions are unique per branch — no tie is possible; a detected tie is warned, not silently picked.
- **D5 — Deviations are always announced (owner).** If the origin is unresolvable (unmapped path, missing/bad `copyfrom-rev`, `copyfrom_rev >= current`, no mirrored commit ≤ rev, source ignored/skipped, deleted lifetime not preserved), the branch is parented the legacy way (current HEAD) and a WARNING is logged **after the commit** with the **actually assigned parent** read from `new_commit.parents`:
  ```
  WARNING [copy-ancestor] r<rev>: branch '<name>' copied from '<path>@r<n>' but origin unresolvable (<reason>) → assigned parent: <sha8> (<subject>) instead
  ```
  Special cases: no parents at all → `assigned parent: none (root commit)`; parent's branch unknown → at least the SHA. On success:
  ```
  INFO [copy-ancestor] r<rev>: branch '<name>' copied from '<path>@r<n>' → parented to <sha8> (origin '<origin-branch>', mirrored r<m>)
  ```
  Log prefix `[copy-ancestor]` for greppability. Fallback NEVER aborts the mirror.
- **D6 — Scope gates.** Only `ADDED` branch changes whose root-dir `PathChange` carries a copy-from **path**, on a **missing** git ref (the destination gate, A4), with `git.copyAncestors` enabled. Never reset existing refs. No root-`R` reparenting (deferred to B5). `A` root without copyfrom = fresh dir = legacy parenting.
- **D7 — Config.** `git.copyAncestors` (bool, CLI `--copy-ancestors`, schema default **false**; flip user config after §8 passes). `commitMsgPrefix` becomes live; align its schema default to `true` (matches today's hardcoded behavior and both config files). *(Post-verification update 2026-10-30: the `copyAncestors` flag was removed per owner — parenting is unconditional default behavior; see §0.)*

## 3. Verified facts & empirical results (2026-10-29)

### 3.1 Source-repo dataset (`file:///home/henrik/trdp/repo-sourceForge`, full `svn log -v --xml -q -r 1:HEAD` scan)
- **Exactly 5 branch/tag root copy events** (the only events this feature acts on):
  | rev | created | copied from | copyfrom-rev |
  |---|---|---|---|
  | 1809 | `/tags/1.4.1.0` | `/trunk/trdp` | 1808 |
  | 1823 | `/branches/trdp-mm` | `/trunk/trdp` | 1821 |
  | 1824 | `/branches/trdp-mm-1.3.1.0` | `/tags/trdp/1.3.1.0` | 1823 |
  | 2269 | `/branches/TRDPSec2.1.0.0` | `/trunk/trdp` | 2268 |
  | 2428 | `/tags/3.0.0.0` | `/trunk/trdp` | 2427 |
- **0** root copies with `copyfrom-rev >= own revision`. 151 additional file/subfolder copies (out of scope — future work).
- **r1823 XML shape (A2):** the copied root `<path>` (`A /branches/trdp-mm`, with copyfrom) is the **first** of six `A` entries; five descendant `A` entries (`/branches/trdp-mm/Xcode/...`, `/config/...`, `/spy/...`) follow in the same revision. Aggregation must survive that order (it does not today — see A2/§7.3).
- Copy targets are **subfolders, not roots** (`trunk/` holds `SDTv2/`+`trdp/`) → new branch's root tree generally differs from origin's root tree; Git fine, graph correct, `--follow` across prefix not guaranteed.
- `getBranchInfos` resolves the targets: `/trunk/trdp` → `trunk`; `/tags/trdp/1.3.1.0` → tag `trdp`; `/tags/1.4.1.0` → tag `1.4.1.0`; `/tags/3.0.0.0` → tag `3.0.0.0` (first-level-under-tags rule).

### 3.2 Path matching & folder config (A5 — B1 retraction)
- WC-form log paths (the tool's actual call: `svn log --xml -v -r <rev> <wcpath>`) are **root-absolute**: 11184/11184 path entries across r1:HEAD start with `/` (e.g. `/trunk/trdp/...`).
- `getBranchInfos` with the **shipped** config values (`/trunk/`, `/branches/`, `/tags/`) matches every real path: `/trunk/trdp`→`trunk`, `/branches/trdp-mm`→`trdp-mm`, `/tags/trdp/1.3.1.0`→tag `trdp`, `/tags/1.4.1.0`→tag `1.4.1.0`, `/tags/3.0.0.0`→tag `3.0.0.0`.
- A **slash-free** config (`trunk`, `branches`, `tags`) matches **nothing** against root-absolute paths (all five → `None`).
- Decisive: the existing 1536-commit mirror was built with the shipped config. **B1 was a false positive; the shipped folder config is correct and is used as-is in §8.** (My earlier B1 test had used relative paths, not the tool's actual root-absolute log paths.)

### 3.3 Old mirror state (why it is not a baseline — A7)
- Existing mirror: 1536 trunk commits, trunk tip **r1557** (newest r-number across all refs r1558) — i.e. built when the repo HEAD was ≈ r1558. The repo has since grown to **r2480**; a fresh full replay covers 2480 revisions, **2358 of which touch `/trunk`** (expected fresh trunk commit count). All hashes change anyway (trailers). → The valid comparison is **Run A vs Run B, both fresh full replays** (§8); the old mirror is referenced only as the pre-trailer format.

### 3.4 Trailer mechanism (git 2.34.1, GitPython 3.1.45 in `.venv`, written via `repo.index.commit(...)`)
- One trailer: `trailers_dict == {'Svn-Revision': ['1821']}` ✓.
- Two trailers SAME final paragraph: both parse (`{'Svn-Revision': ['125'], 'Svn-Branch': ['tag/trdp']}`) ✓ — **required format**.
- Two trailers in SEPARATE paragraphs: `trailers_dict` returns ONLY the last (`{'Svn-Branch': ['trunk']}`) ✗ — **blank line between trailers is forbidden**.
- Key:value-looking body line (`Refs: #123`) in a non-final paragraph: no pollution ✓.
- Empty SVN message, prefix off → message `"\n\nSvn-Revision: 127\nSvn-Branch: trunk"`: parses correctly, subject `` ✓. Prefix on → subject `r127: ` ✓.

### 3.5 WC/metadata (S1 verification)
- PySvn `info()["commit#revision"]` == pinned revision at all 10 test pins (r1…r2000); `commit#date` is `None` (date comes from `log_default`, unchanged).

### 3.6 GitPython API (`.venv` 3.1.45)
- `Repo.create_head(path, commit=<sha-or-ref>, force=False)` — signature `(path: PathLike, commit: Union[SymbolicReference, str]='HEAD', force=False, ...)`; SHA strings resolve internally; safe with unborn HEAD (path doesn't read HEAD); doesn't touch the index.
- **`repo.references` and `repo.refs` both exist; `str(ref)` is the NAME (`tag/trdp`), `ref.path` is the full ref (`refs/heads/tag/trdp`)** — ref enumeration MUST use `.path` (A3).
- `Commit.trailers_dict` / `trailers_list` (`.trailers` deprecated). `Repo.iter_commits(ref, first_parent=True)` newest-first. `Commit.parents`/`.hexsha`/`.summary` as expected.
- `log_default` is a generator yielding entries with `.msg/.author/.date/.revision` (source inspected) — the §7.4 usage is correct.

### 3.7 Existing mirror's commits carry NO trailers
Lookup against pre-trailer commits finds no `Svn-Branch` match → `None` → D5 fallback. Acceptable: the tool rebuilds the mirror each run. (The former `r<rev>:` subject fallback in `svn_revision_of` was removed after verification per owner — no backward compatibility; commits without the `Svn-Branch` trailer are unresolvable by design, per S2.)

## 4. Architecture (final)

```
per revision i (processor loop, unchanged skeleton):
  changes = getChangesInRevision(i)                 # [PathChange] (now carries copyfrom_path/rev)
  branchChanges = findAffectedBranches(changes)     # [BranchChange] (root-A copy-from propagated; ADDED-with-copyfrom sticky)
  if any: switchRevision(i)                         # rewind shared SVN WC
  for br in branchChanges:
      branchName = "tag/"+br.name if br.isTag else br.name
      if br.type in (MODIFIED, ADDED):
          base_commit = deviation_reason = origin_branch = None
          if (br.type == ADDED
              and br.copyfrom_path is not None
              and branchName not in {h.name for h in gitRepo.heads}      # D6 destination gate (A4)
              and cfg["git"]["copyAncestors"]):
              base_commit, deviation_reason, origin_branch = \
                  _resolve_origin(br, i, gitRepo, trunkRoot, branchesRoot, tagsRoot)   # 3-tuple (A9)
          switch_branch(gitRepo, branchName, True, base_commit=base_commit)
          new_commit = commit(gitRepo, getRevisionMetadata(client, i, branchName, cfg["git"]["commitMsgPrefix"]), wc/br.path)
          if base_commit is not None:
              INFO  "[copy-ancestor] r<i>: branch '<name>' copied from '<path>@r<n>' → parented to <sha8> (origin '<ob>', mirrored r<svn_revision_of(base_commit)>)"
          elif deviation_reason is not None:
              parent = new_commit.parents[0] if new_commit.parents else None
              actual = f"{parent.hexsha[:8]} ({parent.summary})" if parent else "none (root commit)"
              WARNING "[copy-ancestor] r<i>: branch '<name>' copied from '<path>@r<n>' but origin unresolvable (<reason>) → assigned parent: <actual> instead"
          if br.isTag: set_git_tag(...)
      elif br.type == DELETED: (unchanged)
```

`_resolve_origin` (processor, three-tuple per A9): `getBranchInfos(br.copyfrom_path, …)` → origin git name (`tag/`-prefixed if tag); reject `copyfrom_rev is None` / `>= i`; `find_origin_commit(gitRepo, origin_git_name, br.copyfrom_rev)` (my_git.py). Any failure → `(None, reason, origin_name_or_None)`.

`find_origin_commit` (my_git.py, S2-correct + A3):
```python
def find_origin_commit(repo: Repo, git_branch_name: str, copyfrom_rev: int) -> Commit | None:
    """Largest-Svn-Revision-≤-copyfrom_rev commit committed ON git_branch_name, else None."""
    live = f"refs/heads/{git_branch_name}"
    refs = [r.path for r in repo.references
            if r.path == live or r.path.startswith(f"refs/heads/del/{git_branch_name}@")]
    best = None                      # (rev, commit)
    seen = set()
    for ref in refs:                 # only refs that EXIST — never raises (D5)
        for c in repo.iter_commits(ref, first_parent=True):
            if svn_branch_of(c) != git_branch_name:      # S2: branch scoping via trailer
                continue
            rev = svn_revision_of(c)
            if rev is None or rev > copyfrom_rev:
                continue
            if c.hexsha in seen:      # same commit via several refs is not a tie (A3)
                continue
            seen.add(c.hexsha)
            if best is None or rev > best[0]:
                best = (rev, c)
            elif rev == best[0]:
                logger.warning(f"[copy-ancestor] duplicate Svn-Revision {rev} on '{git_branch_name}'; keeping first found")
    return best[1] if best else None
```
`svn_revision_of(c)`: `Svn-Revision` trailer (case-insensitive key, first value, `int()`) — absent or malformed value → `None`. (The `r<rev>:` subject-line fallback was removed after verification per owner — no backward compatibility with pre-trailer commits is needed; the tool rebuilds the mirror every run, and branch-scoping makes such commits unusable for lookup anyway.)
`svn_branch_of(c)`: `Svn-Branch` trailer first value → `None`.
(`first_parent=True` is an optimization only — the tool never creates merges; scoping is the trailer, per S2.)
Performance: called ≤ once per branch/tag creation (5× in this repo); walks ≤ ~2400 commits with string checks — negligible vs SVN updates.

## 5. Edge cases (final)

| Case | Decision |
|---|---|
| Subfolder copy target, origin root tree differs | Allowed; graph correct; `--follow` across prefix not guaranteed. |
| `copyfrom_rev` predates origin's first mirrored commit | `None` → D5 warning (names assigned parent) + legacy parent. |
| Origin deleted & name later reused | `del/<name>@*` refs keep the old lifetime; branch-scoped rev lookup lands in the right lifetime. If `keepDeletedBranches=false` (del refs gone) → D5 fallback. |
| Pre-trailer repo (legacy mirrors) | Branch-scoped lookup can't identify branch → `None` → D5 fallback (§3.7). |
| First commit overall (unborn HEAD, trunk) | Unchanged; root commit; D5 warning prints "none (root commit)" if triggered. |
| Root-dir `R` of existing branch | NOT reparented (stays MODIFIED per C/R mapping); deferred to B5. |
| Tag/branch copied from a sibling created in the SAME revision | Not present in dataset (verified: 0 cases); still guarded — `copyfrom_rev >= current` ⇒ D5 fallback. No iteration-order dependence. |
| `A` root without copy-from (fresh dir, e.g. r1822 `/branches`) | Legacy parenting; ADDED status may still be downgraded to MODIFIED by later descendants (today's behavior, preserved — no copy gate applies). |
| Source in `ignoredFolders` / rev in `skipRevisions` | `None` → D5 warning + fallback. |
| Same branch committed twice in one revision | Impossible: one `BranchChange` per branch root per revision ⇒ one commit ⇒ unique `Svn-Revision` per branch. |
| Descendant change AFTER a copied root-A (the real r1823 shape) | ADDED-with-copyfrom is sticky (A2); plain ADDED (no copyfrom) still downgrades to MODIFIED as today. |

## 6. Pre-existing bugs NOT fixed here

- **B1 — RETRACTED (2026-10-29, Astra A5 + my verification §3.2).** The shipped `"/trunk/"`-style folder config matches the root-absolute log paths correctly. There was no bug; the "slash-free workaround" would have matched nothing. Verification uses the shipped config as-is.
- **B3** — `findAffectedBranches` raises on root-delete + inner-change in one revision. Unchanged; dataset check says none exist (a fresh run completing is the re-confirmation).
- **B5** — root `R` stays MODIFIED.
- **B9 (found DURING verification, 2026-10-30) — FIXED as part of this change.** `processor.py` passed `map(PurePath, ignoredFolders)` into `findAffectedBranches`; the `branch_root in excluded_branches` membership test consumes an iterator, so only the first changed path per revision was ever excluded (`--ignored-folders` was almost a no-op). Run C first returned 5 INFO instead of 4 WARNING because a few trunk commits leaked into the mirror. Fix: materialize the list once before the loop. Latent until now: the user config has `ignoredFolders: []`.

## 7. Detailed implementation instructions

> **§7 is ONE coordinated change** (A11). Trailer emission and `commitMsgPrefix` behavior change **unconditionally** (every message gains the two trailers; the prefix flag now actually switches the subject format). Only the copy-origin parenting itself is gated by `copyAncestors`. The steps below are ordered but must be applied as a single logical change — intermediate states do not run.

### 7.1 `src/pathChange.py`
Append to `PathChange` (after `copyfrom_path`, both have defaults — order-safe):
```python
    copyfrom_rev: Optional[int] = None  # source snapshot revision (copyfrom-rev)
```
(`Optional` already imported in this file.)

### 7.2 `src/my_svn.py::getChangesInRevision` (prerequisite — fixes a run-blocking KeyError)
Replace **lines 115–127 inside the existing loop** (A1: retain lines 111–113 — the `for p in xml_changes:` and `if not p.text: continue` guard):
```python
        copyfrom_path = p.attrib.get("copyfrom-path")
        copyfrom_rev_raw = p.attrib.get("copyfrom-rev")
        copyfrom_rev: Optional[int] = None
        if copyfrom_rev_raw:
            try:
                copyfrom_rev = int(copyfrom_rev_raw)
            except ValueError:
                logger.warning(f"r{revision}: unparseable copyfrom-rev '{copyfrom_rev_raw}' for {p.text}")
        changes.append(PathChange(
            path=PurePath(p.text),
            action=SvnAction.fromString(p.get("action", default="?")),
            kind=SvnNodeKind.fromString(p.get("kind", default="?")),
            copyfrom_path=PurePath(copyfrom_path) if copyfrom_path else None,
            copyfrom_rev=copyfrom_rev,
        ))
```
(Delete the old `pass` placeholder and the `p.attrib['copyfrom-path']` KeyError-prone line.)
Also (N1): add `Optional` to the explicit typing import → `from typing import List, Iterable, Optional` (currently `Optional` only reaches this module via `from pathChange import *`).

### 7.3 `src/branchChange.py` (dataclass only) + `src/my_svn.py::findAffectedBranches` (aggregation)
**The dataclass is in `branchChange.py`, but the aggregation function `findAffectedBranches` is in `src/my_svn.py:132`** (A2). Both files are edited.

(a) `src/branchChange.py` — `BranchChange`: append after `isTag` (order-safe, both defaulted):
```python
    copyfrom_path: Optional[PurePath] = None
    copyfrom_rev: Optional[int] = None
```
Add `from typing import Optional` (`PurePath` already imported).

(b) `src/my_svn.py::findAffectedBranches` — two edits to the existing if/elif chain:

Root-ADD arm (currently appends ADDED only when `current is None`, and silently does nothing otherwise):
```python
        # 2) Branch newly created (add of root directory)
        elif change.action == SvnAction.ADD and is_root_dir:
            if current is None:
                result.append(BranchChange(path=branch_root,
                                           name=branch_name,
                                           type=BranchChangeType.ADDED,
                                           isTag=isTag,
                                           copyfrom_path=change.copyfrom_path,
                                           copyfrom_rev=change.copyfrom_rev))
            elif change.copyfrom_path is not None:
                # root 'A' with copy-from is authoritative even if a descendant change
                # already created the aggregate as MODIFIED (XML order can vary)
                current.type = BranchChangeType.ADDED
                current.copyfrom_path = change.copyfrom_path
                current.copyfrom_rev = change.copyfrom_rev
```

Final else (currently: `current.type = BranchChangeType.MODIFIED` unconditionally) becomes:
```python
        elif current is None:
            result.append(BranchChange(path=branch_root,
                                       name=branch_name,
                                       type=BranchChangeType.MODIFIED,
                                       isTag=isTag))
        elif not (current.type == BranchChangeType.ADDED
                  and current.copyfrom_path is not None):
            # A copied-root creation (ADDED + copyfrom) is sticky: later descendant
            # changes must not downgrade it (the real r1823 XML order is root A then
            # 5 descendant A entries). Plain ADDED (no copyfrom) keeps today's behavior.
            current.type = BranchChangeType.MODIFIED
```

Verified behavior matrix (Astra simulated old vs new against the actual chain):
| scenario | old | new |
|---|---|---|
| (a) plain root A, no prior entry | ADDED | ADDED |
| (b) descendant M, then copied root A | MODIFIED (creation missed) | **ADDED + copy fields** |
| (c) descendant M, then plain root A | MODIFIED | MODIFIED (unchanged) |
| (d) repeated copied root A | ADDED, copy fields lost | ADDED + copy fields |
| (e) **copied root A, then descendant M (real r1823)** | **MODIFIED (creation missed)** | **ADDED + copy fields (sticky)** |

The DELETE arm and B3's `raise` are untouched.

### 7.4 `src/my_svn.py::getRevisionMetadata` (S1 + D3)
New signature and body (drop the `info()` read entirely; drop the stale TODO comment):
```python
def getRevisionMetadata(svnLocalClient: LocalClient, revision: int,
                        git_branch_name: str, use_msg_prefix: bool) -> CommitMetadata:
    """Commit metadata for the EXACT given revision (S1: no info() coupling)."""
    entries = svnLocalClient.log_default(revision_from=revision, revision_to=revision,
                                         changelist=False)
    entry = next(entries)  # StopIteration on empty log = crash by design (documented)

    message_body = f"r{revision}: {entry.msg or ''}" if use_msg_prefix else (entry.msg or '')
    message = f"{message_body}\n\nSvn-Revision: {revision}\nSvn-Branch: {git_branch_name}"

    author = entry.author
    return CommitMetadata(
        message=message,
        author_name=author,
        author_email=f"{author}@example.invalid",
        committer_name=author,
        committer_email=f"{author}@example.invalid",
        author_date=entry.date,
        commit_date=entry.date,
    )
```
Notes: trailers in ONE final paragraph, no blank line between them (§3.4 — splitting them breaks `trailers_dict`). `entry.msg or ''` keeps empty messages valid (`"\n\nSvn-Revision: …"` parses, subject ``). Sole call site is `processor.py:65` — updated in §7.5.

### 7.5 `src/processor.py`
1. Extract the folder roots **once before the revision loop** (they're currently rebuilt per revision inside the `findAffectedBranches` call; names don't clash — verified by Astra):
```python
    trunkRoot = PurePath(config["svn"]["trunkFolder"])
    branchesRoot = PurePath(config["svn"]["branchFolder"])
    tagsRoot = PurePath(config["svn"]["tagFolder"])
```
    and use them in the existing `findAffectedBranches(...)` call (replacing the three inline `PurePath(config[...])` constructions).
2. Add the resolver helper (module-level, three-tuple contract per A9):
```python
def _resolve_origin(br: BranchChange, rev: int, gitRepo: Repo,
                    trunk: PurePath, branches: PurePath, tags: PurePath):
    """Resolve the git commit to parent a copy-creation to.

    Returns (base_commit | None, reason | None, origin_git_name | None).
    base_commit is None iff the origin is unresolvable (reason set).
    """
    origin = getBranchInfos(br.copyfrom_path, trunk, branches, tags)
    if origin is None:
        return (None,
                f"copy-from path '{br.copyfrom_path}' does not map to a mirrored branch",
                None)
    o_root, o_name, o_isTag = origin
    o_branch = "tag/" + o_name if o_isTag else o_name
    if br.copyfrom_rev is None:
        return (None, f"copyfrom-rev missing/unparseable for '{br.copyfrom_path}'", o_branch)
    if br.copyfrom_rev >= rev:
        return (None, f"invalid copyfrom-rev {br.copyfrom_rev} (>= current rev {rev})", o_branch)
    base = find_origin_commit(gitRepo, o_branch, br.copyfrom_rev)
    if base is None:
        return (None,
                (f"no mirrored commit on '{o_branch}' at or before r{br.copyfrom_rev} "
                 f"(source ignored/skipped, predates the mirror, or its deleted "
                 f"lifetime is not preserved)"),
                o_branch)
    return base, None, o_branch
```
3. In the per-`BranchChange` loop, for `MODIFIED`/`ADDED`:
```python
                        base_commit = None
                        deviation_reason = None
                        origin_branch = None
                        if (br.type == BranchChangeType.ADDED
                                and br.copyfrom_path is not None
                                and branchName not in {h.name for h in gitRepo.heads}  # D6 destination gate (A4)
                                and config["git"]["copyAncestors"]):
                            base_commit, deviation_reason, origin_branch = _resolve_origin(
                                br, i, gitRepo, trunkRoot, branchesRoot, tagsRoot)

                        switch_branch(gitRepo, branchName, True, base_commit=base_commit)
                        new_commit = commit(
                            gitRepo,
                            getRevisionMetadata(svnLocalClient, i, branchName,
                                                config["git"]["commitMsgPrefix"]),
                            Path(svnLocalClient.path).append(br.path),
                        )
                        if base_commit is not None:
                            logger.info(
                                f"[copy-ancestor] r{i}: branch '{branchName}' copied from "
                                f"'{br.copyfrom_path}@r{br.copyfrom_rev}' → parented to "
                                f"{base_commit.hexsha[:8]} (origin '{origin_branch}', "
                                f"mirrored r{svn_revision_of(base_commit)})")
                        elif deviation_reason is not None:
                            parent = new_commit.parents[0] if new_commit.parents else None
                            actual = (f"{parent.hexsha[:8]} ({parent.summary})" if parent
                                      else "none (root commit)")
                            logger.warning(
                                f"[copy-ancestor] r{i}: branch '{branchName}' copied from "
                                f"'{br.copyfrom_path}@r{br.copyfrom_rev}' but origin unresolvable "
                                f"({deviation_reason}) → assigned parent: {actual} instead")
                        if br.isTag:
                            ...unchanged (set_git_tag)
```
   The D5 warning MUST come from `new_commit.parents` (post-commit ground truth), never from a predicted HEAD (S3).
4. `DELETED` arm: unchanged.
5. Imports: `from my_git import find_origin_commit, svn_revision_of` (explicit — the existing `from my_git import *` also covers them, but be explicit; `svn_revision_of` is needed for the success log).

### 7.6 `src/my_git.py`
1. Add imports: `from git.objects import Commit`, `import re`, `from typing import Optional` (currently `Commit` only arrives via `from commitMetadata import *` — fragile).
2. Add module constants/helpers (top, after imports; N3 applied):
```python
TRAILER_SVN_REVISION = "svn-revision"
TRAILER_SVN_BRANCH = "svn-branch"

def svn_revision_of(commit: Commit) -> Optional[int]:
    for key, values in commit.trailers_dict.items():
        if key.lower() == TRAILER_SVN_REVISION and values:
            try:
                return int(values[0])
            except ValueError:
                return None  # malformed trailer: no subject fallback (N3)
    lines = commit.message.splitlines()
    m = re.match(r"^r(\d+):", lines[0]) if lines else None
    return int(m.group(1)) if m else None

def svn_branch_of(commit: Commit) -> Optional[str]:
    for key, values in commit.trailers_dict.items():
        if key.lower() == TRAILER_SVN_BRANCH and values:
            return values[0]
    return None

def find_origin_commit(repo: Repo, git_branch_name: str, copyfrom_rev: int) -> Optional[Commit]:
    # exactly per §4 (A3-correct: r.path, existing refs only, SHA dedupe, tie warning)
```
3. `switch_branch` — new `base_commit` param, A10-correct (preserves **every** today-path, plus the two new ones):
```python
def switch_branch(repo: Repo, branch_name: str, create_if_missing: bool = False,
                  base_commit: Commit | None = None) -> None:
    ref = f"refs/heads/{branch_name}"

    # Case: ref exists -> just point HEAD there. Never reset.
    # (del/... refs are different names and can never collide here.)
    if branch_name in {h.name for h in repo.heads}:
        repo.git.symbolic_ref("HEAD", ref)
        return

    # Case: unborn HEAD and no base -> nothing to create; the first commit
    # writes the ref. Preserves today's behavior incl. create_if_missing=False (A10).
    if not repo.head.is_valid() and base_commit is None:
        repo.git.symbolic_ref("HEAD", ref)
        return

    if not create_if_missing:
        raise GitCommandError("switch_branch",
                              f"Branch '{branch_name}' does not exist and create_if_missing=False.")

    # detached check only when HEAD is valid (unborn HEAD has no commit to be detached from)
    if repo.head.is_valid() and repo.head.is_detached:
        raise GitCommandError("switch_branch", "Local Repo is corrupted, HEAD is detached")

    if base_commit is not None:
        repo.create_head(branch_name, commit=base_commit.hexsha)  # works even with unborn HEAD (S4)
    else:
        repo.create_head(branch_name)                             # at current HEAD (today's behavior)

    # Set HEAD to the new branch
    repo.git.symbolic_ref("HEAD", ref)
```
   Behavior deltas vs today: exactly (a) the `base_commit` path (new) and (b) unborn-HEAD + base now creates at base (S4). Astra's case-matrix simulation confirms all other old/new pairs are identical.
4. `commit()`: **unchanged** (keeps `head=True` default, `core.worktree` swap, `git add -A` — which also refreshes the index after `create_head`, so the stale-index state is harmless).
5. `rename_branch`/`delete_branch`/tags: unchanged. Note: `del/` rename target for a tag branch is `del/tag/<name>@<rev>` (branchName includes `tag/`) — the candidate-ref prefix in `find_origin_commit` uses the same `git_branch_name`, so it matches.

### 7.7 `src/config_manager.py` + config files
1. Schema `git` section — fix `commitMsgPrefix` default and add `copyAncestors`:
```python
        "commitMsgPrefix": {
            "type": "bool",
            "cli": "--commit-msg-prefix",
            "required": False,
            "default": True,        # was False; aligned with hardcoded behavior + both config files
        },
        "copyAncestors": {
            "type": "bool",
            "cli": "--copy-ancestors",
            "required": False,
            "default": False,       # flip in user config after §8 verification passes
        },
```
2. `config.yml.default` — `git:` section, add:
```yaml
  copyAncestors: false # Parent new branches to the svn copy-from source commit instead of the current HEAD
```
   and fix the `commitMsgPrefix` comment (the option now actually works: `true` = `r<rev>:` prefix, `false` = plain subject; the `Svn-Revision`/`Svn-Branch` trailers are added in both modes).
3. `config.yml` (user, gitignored): add the `copyAncestors: false` line (initially false).

### 7.8 No changes
`main.py`, `commitMetadata.py`, `helper.py`, `cloneRepo`/`initSvn`/`switchRevision` in `my_svn.py`.

## 8. Verification plan (real repo, after implementation)

The old mirror is **not** a baseline (§3.3: built at repo-HEAD ≈ r1558; the repo is now r2480). All runs are **fresh full replays** (the tool wipes the git dir per run). Config for all runs: the **shipped** folder config as-is (B1 retracted — §3.2), plus `--keep-deleted-branches true --keep-deleted-tags true` so every lifetime stays queryable.

### 8.1 Verifier script (write to `/tmp/opencode/verify_copy_parenting.py`)

```python
#!/usr/bin/env python3
"""Assertions for copy-ancestor parenting. Uses the tool's OWN trailer helpers
(same code path as the feature — S5). Run:
    PYTHONPATH=src .venv/bin/python -B verify_copy_parenting.py <mode> <args>
modes:
    check-structure <gitdir>
    assert-parents  <gitdir>
    fingerprint     <gitdir> <out.json>     # per-branch (rev,tree,parent(branch,rev)) map
    compare         <fpA.json> <fpB.json>
"""
import json, sys
from git import Repo
from my_git import svn_revision_of, svn_branch_of

EXPECTED_EVENTS = [  # (branch, creation_rev, copyfrom_rev, origin_branch)  — §3.1
    ("tag/1.4.1.0",     1809, 1808, "trunk"),
    ("trdp-mm",         1823, 1821, "trunk"),
    ("trdp-mm-1.3.1.0", 1824, 1823, "tag/trdp"),
    ("TRDPSec2.1.0.0",  2269, 2268, "trunk"),
    ("tag/3.0.0.0",     2428, 2427, "trunk"),
]

def ref_paths(repo, branch):
    live = f"refs/heads/{branch}"
    return [r.path for r in repo.references
            if r.path == live or r.path.startswith(f"refs/heads/del/{branch}@")]

def commits_on(repo, branch):
    """{rev: [commit,...]} for commits whose Svn-Branch == branch (A7: trailer-keyed)."""
    out = {}
    for rp in ref_paths(repo, branch):
        for c in repo.iter_commits(rp, first_parent=True):
            if svn_branch_of(c) != branch:
                continue
            rev = svn_revision_of(c)
            if rev is not None:
                out.setdefault(rev, []).append(c)
    return out

def largest_at_or_before(repo, branch, rev):
    best = None
    for r, cs in commits_on(repo, branch).items():
        if r <= rev and (best is None or r > best[0]):
            best = (r, cs[0])
    return best[1] if best else None

def fingerprint(repo, outfile):
    """Per branch: ordered [(rev, tree_sha, (parent_branch, parent_rev)|None)] — parent
    identified by ITS trailers, so fingerprints are comparable across runs (A7)."""
    fp = {}
    for head in repo.heads:
        b = head.name
        rows = []
        for c in repo.iter_commits(b, first_parent=True):
            if svn_branch_of(c) != b:
                continue
            rev = svn_revision_of(c)
            if rev is None:
                print(f"FAIL {b}: commit without parseable Svn-Revision: {c.hexsha[:8]}", file=sys.stderr); sys.exit(1)
            p = c.parents[0] if c.parents else None
            prow = (svn_branch_of(p), svn_revision_of(p)) if p is not None else None
            rows.append([rev, c.tree.hexsha, list(prow) if prow else None])
        rows.reverse()  # oldest first
        fp[b] = rows
    with open(outfile, "w") as f:
        json.dump(fp, f, indent=1)
    print(f"fingerprint: {len(fp)} branches -> {outfile}")

def check_structure(repo):
    branches = {h.name for h in repo.heads}
    trunk = commits_on(repo, "trunk")
    assert len(trunk) == 2358, f"trunk commits {len(trunk)} != 2358 (expected, §3.3)"
    assert all(len(v) == 1 for v in trunk.values()), "duplicate Svn-Revision on trunk"
    for b in branches:
        for rev, cs in commits_on(repo, b).items():
            assert len(cs) == 1, f"duplicate Svn-Revision {rev} on {b}"
    print(f"structure OK: {len(branches)} branches, trunk={len(trunk)} commits, trailers+uniqueness hold")

def compare(fpA, fpB):
    assert set(fpA) == set(fpB), f"branch set differs: {set(fpA) ^ set(fpB)}"
    diffs = []
    for b in fpA:
        a, c = fpA[b], fpB[b]
        assert len(a) == len(c), f"{b}: commit count A={len(a)} B={len(c)}"
        for ra, rc in zip(a, c):
            assert ra[0] == rc[0] and ra[1] == rc[1], \
                f"{b} rev {ra[0]}: rev/tree mismatch A={ra[:2]} B={rc[:2]}"
            if ra[2] != rc[2]:
                diffs.append((b, ra[0], ra[2], rc[2]))
    expected = {(b, r) for b, r, _, _ in EXPECTED_EVENTS}
    got = {(b, r) for b, r, _, _ in diffs}
    assert got == expected, f"parent-diff set {got} != expected {expected}"
    for b, r, pa, pb in diffs:
        print(f"  {b}@r{r}: parent A={pa} -> B={pb}")
    print(f"compare OK: exactly the {len(diffs)} copied-branch first commits differ in parent; all trees/revs identical")

def assert_parents(repo):
    for b, crev, cfrev, origin in EXPECTED_EVENTS:
        by = commits_on(repo, b)
        assert crev in by, f"{b}: no commit at r{crev}"
        c = by[crev][0]
        assert len(c.parents) == 1, f"{b}@r{crev}: expected exactly ONE parent, got {len(c.parents)}"
        oc = largest_at_or_before(repo, origin, cfrev)
        assert oc is not None, f"no origin commit on {origin} at/before r{cfrev}"
        assert c.parents[0].hexsha == oc.hexsha, \
            f"{b}@r{crev}: parent {c.parents[0].hexsha[:8]} != expected {origin}@r{svn_revision_of(oc)} {oc.hexsha[:8]}"
        print(f"  OK {b}@r{crev} parent = {origin}@r{svn_revision_of(oc)} ({oc.hexsha[:8]})")
    print("assert_parents OK: all 5 copy events parented to their copy-from source")

if __name__ == "__main__":
    mode, args = sys.argv[1], sys.argv[2:]
    if mode == "fingerprint":
        fingerprint(Repo(args[0]), args[1])
    elif mode == "check-structure":
        check_structure(Repo(args[0]))
    elif mode == "compare":
        compare(json.load(open(args[0])), json.load(open(args[1])))
    elif mode == "assert-parents":
        assert_parents(Repo(args[0]))
    else:
        sys.exit(f"unknown mode {mode}")
```

### 8.2 Run A — feature OFF (baseline of the new format)
```sh
cd /home/henrik/trdp/svn-replicator
.venv/bin/python src/main.py --copy-ancestors false --keep-deleted-branches true --keep-deleted-tags true 2>&1 | tee /tmp/opencode/runA.log
cp -a .localWCs/git /tmp/opencode/mirrorA
PYTHONPATH=src .venv/bin/python -B /tmp/opencode/verify_copy_parenting.py check-structure /tmp/opencode/mirrorA
PYTHONPATH=src .venv/bin/python -B /tmp/opencode/verify_copy_parenting.py fingerprint /tmp/opencode/mirrorA /tmp/opencode/fpA.json
grep -c '\[copy-ancestor\]' /tmp/opencode/runA.log        # expected: 0
git -C /tmp/opencode/mirrorA log -1 --format=%B trunk | tail -3   # both trailers present
```
Expected: exit 0; trunk = 2358 commits; both trailers on sampled commits; zero `[copy-ancestor]` lines.

### 8.3 Run B — feature ON
```sh
.venv/bin/python src/main.py --copy-ancestors true --keep-deleted-branches true --keep-deleted-tags true 2>&1 | tee /tmp/opencode/runB.log
cp -a .localWCs/git /tmp/opencode/mirrorB
PYTHONPATH=src .venv/bin/python -B /tmp/opencode/verify_copy_parenting.py assert-parents /tmp/opencode/mirrorB
PYTHONPATH=src .venv/bin/python -B /tmp/opencode/verify_copy_parenting.py fingerprint /tmp/opencode/mirrorB /tmp/opencode/fpB.json
PYTHONPATH=src .venv/bin/python -B /tmp/opencode/verify_copy_parenting.py compare /tmp/opencode/fpA.json /tmp/opencode/fpB.json
grep '\[copy-ancestor\]' /tmp/opencode/runB.log           # expected: exactly 5 INFO lines, 0 WARNING
git -C /tmp/opencode/mirrorB log --graph --oneline trdp-mm -6     # sanity: forks off trunk at r1821
```
Expected: exit 0; all 5 parent equalities hold with exactly one parent each; A/B diff = exactly the 5 copied-branch first commits' parent field (trees/revs identical); 5 INFO / 0 WARNING.

### 8.4 Run C — D5 negative test (deviation warning, A8)
Ignore the trunk entirely (`ignoredFolders` compares full branch-root paths, so `/trunk` is the correct value). This makes the 4 trunk-sourced copies unresolvable (no mirrored trunk commits) while r1824 (from `tag/trdp`, still mirrored) still resolves:
```sh
.venv/bin/python src/main.py --copy-ancestors true --keep-deleted-branches true --keep-deleted-tags true --ignored-folders /trunk 2>&1 | tee /tmp/opencode/runC.log
grep '\[copy-ancestor\]' /tmp/opencode/runC.log
```
Expected: exit 0; exactly **4 WARNING** lines (r1809, r1823, r2269, r2428 — all copied from `/trunk/trdp`), each containing `origin unresolvable (no mirrored commit on 'trunk' …)` and `→ assigned parent: <sha8> (<subject>)` (or `none (root commit)`); exactly **1 INFO** line (r1824, resolved via `tag/trdp`). This proves the D5 warning fires, names the actually-assigned parent, and never aborts.

## 9. Risks (final)

- **Trunk regression** — gated (missing ref + root-A + copy-from path + flag); Run A structural check (2358 commits, trailers, uniqueness); `create_head(commit=…)` doesn't touch the index.
- **Aggregation downgrade (the A2 trap)** — if only the ADD arm is fixed and the final `else` is left alone, the real r1823 creation is still missed. The §7.3 behavior matrix (esp. scenario e) must hold; the r1823 assertion in Run B is the end-to-end proof.
- **Trailer format fragility** — locked to single final paragraph, two trailers (§3.4); parser-verified both directions (GitPython + git).
- **All hashes change vs the old mirror** — expected and accepted (§3.3); the old mirror is stale anyway (repo grew r1558 → r2480). A/B fingerprint comparison replaces byte-identity.
- **Branch-scoped lookup unusable on pre-trailer repos** — D5 fallback + warning (§3.7); acceptable given the tool rebuilds mirrors. The subject-line revision fallback was removed after verification (owner: no backward compatibility needed), so commits without the `Svn-Branch` trailer are unresolvable by construction.
- **B3** — can still abort a run on root-delete + inner-change in one revision (none in dataset; a completed Run A is the re-confirmation).

## 10. Review-gate questions for the owner

1. **B1 retraction** — the shipped `"/trunk/"` folder config is CORRECT (root-absolute log paths + `getBranchInfos` matching; the old mirror was built with it). B1 is removed from the bug catalog. Confirm acceptance — and I will fix the knowledge-tab bug list accordingly.
2. **`copyAncestors` default after verification passes**: keep schema `false` and flip only the user `config.yml` (proposed) — or flip the schema too?
3. **`Svn-Branch` trailer** (Sol S2): required for correct lookup scoping; adds one line to every commit. OK as-is?
4. **D5 log format** (§2) — wording fine?
5. **Stale mirror replaced** — the next successful run replaces the 1536-commit mirror with a 2358-commit one (repo grew from r1558 to r2480). Just noting — no action needed.

## 11. Findings & decisions log (append-only)

- **2026-10-29 @gpt-6-sol verification: APPROVE-WITH-CHANGES (5 points)** — S1–S5 as in §0, all incorporated.
- **2026-10-29 my independent verification** — §3 (trailer parsing, WC pinning, dataset scan, GitPython API).
- **2026-10-29 decision: add `Svn-Branch` trailer** (consequence of Sol S2); both trailers always (D3a).
- **2026-10-29 @gpt-6-astra final verification: BLOCK (11 required fixes + 3 nits)** — A1–A11/N1–N3 as in §0, all incorporated. Full text recovered from `~/.local/share/opencode/opencode.db` (session `ses_e7728cdb4ffeZ1clKiHIPSB3Ld`, table `session_message`); a 3rd Astra retry died on a provider quota error with no content, but the 2nd call's complete verdict was retrieved, so nothing was lost.
- **2026-10-29 my verification of Astra's data-dependent claims:** (A5) `getBranchInfos` matches all real root-absolute paths with the shipped config; slash-free config matches nothing; **B1 retracted**. (A7) old mirror trunk = 1536 commits, tip r1557; repo HEAD = r2480; 2358 trunk-touching revisions → A/B fresh comparison adopted. (A2) r1823 XML: copied root-A precedes 5 descendant A's (Astra's simulation + dataset scan agree).
- **2026-10-29 process note:** subagent results are recoverable from `~/.local/share/opencode/opencode.db` (SQLite: `session_v2` + `session_message`, JSON in `data`) when the tool result is lost (quota errors, restarts) — check there before asking the user to paste.
- **2026-10-30 review gate passed** (owner accepted all 5 questions, incl. schema `copyAncestors: false` + flip user config after verification; `Svn-Branch` trailer; D5 wording; B1 retraction).
- **2026-10-30 implementation + verification (all §8 assertions PASS).** Plan committed `1961998`; feature committed `f22057d` (7 files, +217/−62). Preflight (`/tmp/opencode/preflight.py`) verified the §7.3 behavior matrix incl. scenario e (real r1823 shape), `find_origin_commit` (largest-rev-≤, branch scoping, `del/` lifetimes, missing ref → None), and the `switch_branch` path matrix (S4/A10). Run A (off): 9 branches, trunk = 2358, trailers parse, unique revs, 0 `[copy-ancestor]` lines. Run B (on): assert-parents 5/5 — tag/1.4.1.0@r1809=trunk@r1808, trdp-mm@r1823=trunk@r1820, trdp-mm-1.3.1.0@r1824=tag/trdp@r1821, TRDPSec2.1.0.0@r2269=trunk@r2267, tag/3.0.0.0@r2428=trunk@r2427, each with exactly one parent; A/B fingerprint diff = 3 genuine parent corrections (legacy had trdp-mm parented from `tag/trdp`, trdp-mm-1.3.1.0 from its sibling `trdp-mm`, and TRDPSec2.1.0.0 from the WRONG branch `TRDPSec-2.1.0.0`); the other 2 events show no diff because the legacy parent coincidentally already was the correct origin (assert-parents is the strict per-event proof); 5 INFO / 0 WARNING. Run C (`--ignored-folders /trunk`, after the B9 fix): 0 trunk commits in the mirror, 4 D5 WARNINGs each naming the actually-assigned parent (e.g. r2428 got the r2310 tip) + 1 INFO (r1824 via tag/trdp), exit 0. Verifier fixes made along the way (script only, `/tmp/opencode/verify_copy_parenting.py`): fingerprint keyed on live head names dropped the two event tags later deleted in SVN → key by `Svn-Branch` trailer value; compare changed from "exactly the 5 diffs" to "no UNEXPECTED diffs" (a coincidental legacy=origin match legitimately produces no diff). User `config.yml` flipped to `copyAncestors: true` after verification (schema default stays `false`, per gate decision).
