# Analyze PR

Review a GitHub pull request for necessity, scope, evidence, correctness, compatibility, and merge readiness. Write the review artifact in concise Chinese.

Repository: https://github.com/ZhuLinsen/daily_stock_analysis/pulls
Invocation: `/analyze-pr <pr_number>`

## Canonical rules

Read and follow the repository root `AGENTS.md` and `.github/PULL_REQUEST_TEMPLATE.md`. The repository is a ThesisLedger data service. Use its current data entry points, test collection policy, and deployment contract.

## Refresh the baseline safely

Before PR review, inspect `git status --short` and run `git fetch --all --prune`. Run `git pull --ff-only` only when the worktree is clean and the current branch has a fast-forwardable upstream.

When changes, conflicts, untracked risk files, a missing upstream, or divergent history prevent that, preserve the checkout. Review fetched refs, the PR head, or the GitHub diff. Do not stash, reset, overwrite changes, or switch branches by default.

Record the local HEAD, reviewed remote baseline, and any reason the checkout was not updated.

## Gather evidence

Use the existing GitHub repository and PR identity:

```bash
gh pr view <pr_number> --repo ZhuLinsen/daily_stock_analysis
gh pr view <pr_number> --repo ZhuLinsen/daily_stock_analysis --comments
gh pr checks <pr_number> --repo ZhuLinsen/daily_stock_analysis
gh pr diff <pr_number> --repo ZhuLinsen/daily_stock_analysis
```

Inspect failed CI with `gh run view <run_id> --log-failed` before repeating expensive local checks.

## Assess the title and description

Recommend a concrete `<type>: <change>` title using fix/feat/refactor/docs/chore/test/ci. Avoid tool or agent provenance prefixes. A title style issue alone is not a merge blocker.

Compare the description with the actual diff and current PR template. Check the problem, file scope, issue or acceptance criteria, executed validation, data and runtime evidence, compatibility, risks, and recovery.

For provider/protocol changes, verify authoritative sources, pinned dependency/runtime compatibility, the exercised call chain, and effects on existing configuration. Check that credentials, source enablement, policy, and admission remain distinct.

## Review implementation and verification

Prioritize the PR diff, CI, existing tests, and workflow logs. Add the smallest local checks needed to resolve a real gap. Do not check out the PR by default.

Use targeted data tests, then the repository's `scripts/ci_gate.sh` stages. Default pytest and CI must obey the explicit data test inventory. Keep unit tests, physical data environments, external providers, native SDK/image builds, and target upgrade/recovery evidence distinct.

Check API/Schema consumers, source identity, clocks, units, coverage, permissions, caching, persistent tasks, cancellation/resource cleanup, and storage preservation. Do not turn missing or failed input into a successful empty result. Inspect deployment and recovery contracts when affected.

## Save the review

Write `.claude/reviews/prs/pr-<number>.md` in Chinese with findings ordered by severity, necessity and scope, description completeness, validation evidence and baseline, compatibility/risks, recovery, and merge readiness. Include a draft review comment only when useful; saving it does not authorize publication.

## Authorization

Reading metadata/code, safe baseline refresh, necessary local checks, and writing the local review are allowed within the request. Respect authorization already given by the user. Obtain missing authorization before posting a comment, approving, requesting changes, merging, or closing a PR.
