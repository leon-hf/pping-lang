# pping-lang Agent Notes

Read the project-local imported Claude handoff first for Autopilot, runw, diagnosis-engine, and live GPU validation context:

- `.codex/memories/pping-lang-claude-handoff.md`

## Key Local Docs

- `_design-notes/autopilot-自动调优agent-设计.md`
- `_design-notes/autopilot-任务清单.md`
- `deploy/runw/README.md`
- `.codex/skills/deploy-runw/SKILL.md`
- `.codex/skills/runw-diagnose/SKILL.md`
- `src/pping_lang/autopilot/`
- `src/pping_lang/rules/`

## Working Notes

- Use explicit UTF-8 when reading Chinese markdown in PowerShell:
  `Get-Content -LiteralPath '<path>' -Raw -Encoding UTF8`
- `deploy/runw/` is intentionally local/deploy-specific and ignored by Git.
- `.codex/` is intentionally project-local/private and ignored by Git; it contains migrated Claude handoff notes and local secrets.
- For "deploy to runw", "publish dashboard", or remote runw validation, read `.codex/skills/deploy-runw/SKILL.md` first. For "drive traffic / inspect diagnoses on runw", read `.codex/skills/runw-diagnose/SKILL.md` first.
- runw is the live GPU validation machine: RTX 5060 Ti 16GB, vLLM 0.21, default model `Qwen/Qwen2.5-0.5B-Instruct`.
- runw deployment intentionally avoids `--enforce-eager` so validation follows the production CUDA graph path; keep `--enable-mfu-metrics` for diagnosis.
- Autopilot M0 is mostly implemented and true-run validated; known remaining gaps are resume, D/capacity-wall end-to-end with a larger model, `applies_to` completion, mid-bench budget interruption, explicit teardown verification, constraint/live-effective-config completion, and T2 equivalence checks.
