# CLI Handoff Workflow

This project may have multiple agents working concurrently. Use this workflow to avoid deleting each other's commits or changing CLI behavior without a shared contract.

## Branching

- Contract docs live on `main` under `.docs/cli/`.
- Each feature should be implemented on a feature branch.
- Branch name pattern:

```text
feature/<area>-<short-name>
```

Examples:

```text
feature/cli-parse-book
feature/parser-mineru-runner
feature/segmenter-sections-jsonl
feature/wiki-compile
feature/mcp-reading-tools
```

## Starting work

1. Check current state:

```bash
git status --short --branch
git branch --list
```

2. If a similar feature branch exists, reuse it:

```bash
git checkout feature/<existing-branch>
git merge main
```

3. Otherwise create a branch from latest `main`:

```bash
git checkout main
git checkout -b feature/<area>-<short-name>
```

4. Read CLI contracts before editing code:

```bash
ls .docs/cli
```

## Editing rules

- If the contract is missing or ambiguous, edit `.docs/cli/` first.
- If a command fails because behavior is missing, read `.docs/cli/` before changing code.
- Implementation must add tests for the behavior it claims.
- Keep stages separate unless a contract explicitly says a command orchestrates multiple stages.

## Merging back

Before merging to `main`:

```bash
uv run --extra dev pytest -q
uv run --extra dev ruff check .
codegraph sync
```

Then:

```bash
git checkout main
git merge --ff-only feature/<branch>
```

If fast-forward fails:

1. Stop.
2. Inspect why:

```bash
git status --short --branch
git log --oneline --decorate --graph --all --max-count=20
```

3. Merge `main` into the feature branch and resolve conflicts there:

```bash
git checkout feature/<branch>
git merge main
# resolve conflicts, test/lint/codegraph sync, commit
```

4. Then fast-forward `main` again.

## Prohibited without explicit user approval

Do not run these on `main` or another agent's branch unless the user explicitly asks:

```bash
git reset --hard
git clean -fd
git rebase
git push --force
git branch -D <branch>
git stash drop
git checkout -- <path>
```

Safe recovery for an accidentally dropped commit, after verifying it is a direct descendant of current `main`:

```bash
git cat-file -p <commit>
git merge --ff-only <commit>
```

## Review checklist

Before saying a CLI feature is done, report:

- branch and commit id
- changed contract files
- changed implementation files
- test command/output
- lint command/output
- smoke command/output
- codegraph sync/status if code changed
