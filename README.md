# SVN-Replicator
This project aims to provide a tool to continuously mirror svn repos into a git repository. I've developed this since the existing tools I found are either expensive or do not fulfill my expectations on how the history of the original svn repository should be conserved through mirroring.

To use this tool you set up a config for an SVN repository you want to mirror once. Regularly calling this tool will create a git repository that mirrors the SVN repository, including the history. It will keep the mirrored repo up to date automatically. The tool maintains a local Git mirror; it does not push to a Git remote — you push the mirror yourself when you want to.

## AI in development
Large language models (LLMs) were used in the development process of this tool — for design, implementation, review and documentation. Commits created with AI assistance are recorded with an `Assisted-by: LLM` trailer, so the contribution can be audited in the history; see `AGENTS.md` for the convention. The design decisions and the review of the result are the maintainer's.

## How it preserves history
The tool replays every SVN revision from 1 to HEAD and creates one Git commit per affected branch, so the Git history is a branch-aware transcript of the SVN history. Two behaviors worth knowing:

- **Copied branches keep their origin.** When a branch or tag is created with `svn cp`, its first Git commit is parented to the mirrored commit of the copy-from source (at `copyfrom-rev`) rather than to whichever branch the replay loop happened to be on. If the origin cannot be resolved, the tool falls back to the previous parent and logs a WARNING that names the parent it actually assigned.
- **Every commit is annotated.** Each mirrored commit carries two trailers — `Svn-Revision: <rev>` and `Svn-Branch: <git-branch>` — so the mapping back to the SVN source is always recoverable from the Git repository alone, with no extra state.

## Testing
A deterministic test harness pins the exact Git output of the tool. Each fixture is a committed SVN source plus a committed golden Git reference; the harness runs the tool on the source and requires the produced mirror to match the golden object-for-object (commit hashes included), in addition to a structural check that names any drifted branch, commit or parent. Run it with:

    .venv/bin/python tests/run_tests.py [fixture_name ...]

`fixture_name` is one of `basic`, `deviation`, `lifecycle`, `sticky-add` (omit it to run all). See `docs/plans/test-harness.md` for the design.

## Contributing
If you want to contribute, I'm happy to include your suggestions. A good starting off point would be to look at the open issues, or to create one for your idea. See `AGENTS.md` for repository conventions, including how AI-assisted commits are marked.

## Known issues:
- `client.info()` reports a parsing error, when the call to the local svn instance results in an error. In those cases the result looks like following: 
    ``` xml
    svn: warning: cannot set LC_CTYPE locale
    svn: warning: environment variable LANG is en_US.UTF-8
    svn: warning: please check that your locale name is correct
    <?xml version="1.0" encoding="UTF-8"?>
    ...
    ```
    The tool now sets `LC_ALL=C` before calling `client.info()`, which covers most locale-related failures; if it still reproduces on your system, `sudo dpkg-reconfigure locales` and selecting an appropriate language (i.e. `C.UTF-8`) is the underlying fix.
