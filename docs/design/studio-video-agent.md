# Studio video agent: continuity-aware story projects

User-authorized implementation, 2026-10-14. Independent worktree based on the tested multishot branch (01a766a8). Goal: a self-hosted story → editable storyboard → bounded generation → review/revision → export workflow. Grok's proprietary image/video quality is a benchmark target, not a claimed reproduction.

## Public reference and boundaries

xAI's video overview documents first/last/keyframe and reference inputs, plus an image-first text-to-video path: https://docs.x.ai/developers/model-capabilities/video/overview . Its launch post documents projects and parallel agents: https://x.ai/news/grok-imagine-video-1-5 . The quoted continuity claim is not an independently reproduced benchmark. No proprietary prompts or model weights are assumed available.

## Design

Add a Studio `/agent` workspace using existing SQLite, jobs, scene queue, media and export interfaces. Keep one authoritative GPU executor. Persist story brief, style, characters/locations, ordered shots, uploaded immutable image assets, revisions, chosen takes and review verdicts. A planner proposes structured data; users review/edit before GPU work. Support a configurable OpenAI-compatible planning endpoint and ViMax structured-artifact import; no fake successful planning when credentials are missing.

Character/scene assets are explicit IDs. A shot may use direct references and a previous shot's last frame as a *reference*, using the existing deferred `job-last-frame:` mechanism. That mechanism routes to H3 Ref2VA; it is not an exact first-frame lock. FastH3 V2 is T2AV-only: reject reference-bearing projects before creating jobs, and explain the capability gap. Model runtime still performs core validation.

Reserve a deterministic batch and job IDs before calling JobRunner, then reconcile partial creation on retry. Never enqueue until all inputs pass validation. Use revision compare-and-swap for edits/planner responses; generation must refer to an approved current revision and an explicit new-job budget. Existing active tasks prevent conflicting project edits. Old takes and exports are retained. Review annotations do not claim automated image-quality verification.

## Implementation plan (inline execution)

1. Add `agent_project.py`: validated blueprint/ViMax import, reference compilation, dependency invalidation, SQLite revision/operation store. RED→GREEN tests for cycles, unknown assets, stale revisions, exact partial-edit scope and model rejection.
2. Add `agent_routes.py` + planner adapter: project CRUD, assets, planning/revision, approval, bounded generation, review, scene export binding. Wire real and mock servers. Tests use real HTTP and a recording executor; generation is labeled simulated in mock mode.
3. Add `/agent` workspace and navigation: brief/planner input, editable blueprint, character/assets list, continuity indicators, job progress, review and selected-take export. Frontend regression tests and browser mock flow.
4. Run scoped backend/frontend suites, typecheck/build/pre-commit, independent review, then real LLM/image/GPU generation on a free allocated GPU. Decode and inspect every final shot, test partial revision and restart, report unsupported model capabilities and missing resources without claiming success.

## Acceptance

Three shots with the same referenced subject; approve plan; submit once; render/review; revise shot two; preserve unaffected takes and mark dependent shots stale; refresh/restart preserves everything; export actual completed selected clips. Also test invalid references, duplicate requests, crash during job creation, cancellation/status reconciliation and stale planner output. Synthetic fixtures validate contracts only. Image generation/provider setup and actual continuity require separate real-model evidence.

The user explicitly requested continuous work and a separate worktree; proceed with implementation without repeated approval pauses. Earlier runtime and frontend branches remain unchanged.
