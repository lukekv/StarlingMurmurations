# Scratchpads

Planning documents for the issue-driven development workflow. One file per GitHub issue, written during the PLAN phase of `/resolve-issue` and committed together with the implementation.

## Naming

```
issue-<number>-<short-slug>.md
```

Example: `issue-3-seed-test-suite.md`

## Required contents

Each scratchpad contains:

1. **Issue link** — `https://github.com/lukekv/StarlingMurmurations/issues/<number>`
2. **Task breakdown** — the issue decomposed into small, atomic steps
3. **Files touched** — what will be created or modified
4. **Test / verification strategy** — how we'll know it works

## Why these exist

Scratchpads are the searchable record of *intent*. When planning a new issue, search this directory (and merged PRs) for prior art: what was done before in this area, and why. They make every issue workable from a cold start — no conversation context required.
