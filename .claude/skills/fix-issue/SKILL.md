# Fix Issue

Implement a confirmed issue with focused changes and evidence of behavior, compatibility, risk, and recovery. Write user-facing artifacts in Chinese.

Repository: https://github.com/ZhuLinsen/daily_stock_analysis
Invocation: `/fix-issue <issue_number>`

## Establish scope and baseline

Read `AGENTS.md` and the existing `.claude/reviews/issues/issue-<number>.md`. If analysis is missing, establish the concrete problem, cause, affected data contract, and acceptance criteria before editing.

Inspect `git status --short` and run `git fetch --all --prune` before issue work or PR preparation. Run `git pull --ff-only` only for a clean worktree with a fast-forwardable upstream.

Preserve local changes, conflicts, untracked risk files, and divergent history. Do not stash, reset, overwrite work, or switch branches by default. Record the local HEAD, reviewed remote baseline, and why the checkout was not updated.

Use the current worktree for the authorized change. Follow explicit user instructions for branches and PR preparation; explain any material baseline divergence before a dependent operation.

## Implement the contract

Locate the complete affected path and reuse existing data modules, configuration, scripts, repositories, and tests. Preserve API/Schema consumers and explicit failure/unknown semantics. Source enablement, credentials, policy, and admission must remain separate.

For configuration, data API, storage, task lifecycle, or deployment changes, update the relevant topic documentation, `docs/CHANGELOG.md`, and configuration examples as applicable.

Keep `[Unreleased]` flat: one `- [type] description` line, with type chosen from 新功能/改进/修复/文档/测试/chore. Use 修复 only for an actual bug fix. Do not add category headings.

Keep README focused on positioning, capabilities, quick start, and primary entry points. Put detailed contracts, configuration, diagnostics, and edge cases in the existing topic documents.

## Verify by impact

Start with targeted tests and the smallest meaningful check. Use the data boundary, lint, deterministic checks, and offline tests provided by `scripts/ci_gate.sh` as needed. Default pytest and CI use the explicit data test inventory; do not hide unregistered tests.

Keep local tests, physical standalone data environments, real providers, native SDK/image builds, and target upgrade/recovery evidence distinct. Use the formal workspace Docker scripts and preserve persistent data. Record incomplete checks and their concrete reason.

## Update the issue analysis

Append the implementation date, behavior and affected files, executed checks, unverified gates, risks, and recovery to `.claude/reviews/issues/issue-<number>.md`. Use Chinese headings and evidence-grounded status.

If PR preparation is requested, follow the current PR template and recommend a concrete `<type>: <change>` title without tool/agent provenance. Title style alone is not a process blocker.

## Authorization

Code inspection, safe baseline refresh, focused authorized edits, non-destructive local verification, and local analysis updates are allowed within the request. Respect existing user authorization. Obtain any missing authorization before branch changes, commits, pushes, PR creation, or replying to/closing an issue.
