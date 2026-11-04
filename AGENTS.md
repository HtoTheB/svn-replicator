# Repository conventions for AI-assisted work

## Commits

Every commit that an AI assistant helps create carries this trailer as the
last paragraph of its message:

    Assisted-by: LLM

- Value is exactly `LLM` — the specific model or tool is not recorded; the
  trailer documents that AI was involved at all (kernel-style convention).
- Placement: a blank line, then the trailer, at the end of the message.
- Applies retroactively when history is local (unpushed): rewrite in place,
  preserving tree, author, committer and dates; change the message only.
- Public (pushed) commits are never rewritten; the trailer starts with the
  next local commit on top.

## Comments and code

- Comments describe what the code does, for a reader. No conversational
  tone, never addressed to a person, no references to plans, datasets,
  sessions or review rounds.
- Keep the existing structure, naming and style of the surrounding code.
