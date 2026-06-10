# Research: GitHub Copilot Model Swapping and AI_IDENTITY

**Date:** 2026-06-10
**Status:** Research complete — proposals pending review
**Context:** Book Dragon supports 14 AI coding tools. Each identifies itself during session startup with an `AI_IDENTITY` line. GitHub Copilot can use multiple underlying models. This note investigates the implications.

---

## 1. Which models can GitHub Copilot use as of mid-2026?

Copilot supports 23 models across four providers as of June 2026:

| Provider | Models | Notes |
|---|---|---|
| **OpenAI** | GPT-5 mini, GPT-5.3-Codex, GPT-5.4, GPT-5.4 mini, GPT-5.4 nano, GPT-5.5 | GPT-5.5 at 7.5x multiplier |
| **Anthropic** | Claude Haiku 4.5, Sonnet 4.5, Sonnet 4.6, Opus 4.5, Opus 4.6, Opus 4.6 (fast), Opus 4.7, Opus 4.8, Fable 5 | Nine Claude models; Fable 5 GA as of 2026-06-09 |
| **Google** | Gemini 2.5 Pro, Gemini 3 Flash, Gemini 3.1 Pro, Gemini 3.5 Flash | Removed from web chat (May 2026) but still available in VS Code, JetBrains, CLI |
| **Microsoft** | MAI-Code-1-Flash | First-party; Azure-hosted |

Additionally, Raptor mini (fine-tuned GPT-5 mini) exists as a specialised model.

**Auto model selection** is available across Chat, CLI, and the cloud agent. When enabled, Copilot routes requests based on real-time system health, task complexity, and cache boundaries. The system avoids switching models mid-conversation. Users can override auto-selection by choosing a specific model.

**Source:** [GitHub Docs — Supported models](https://docs.github.com/en/copilot/reference/ai-models/supported-models), [Auto model selection](https://docs.github.com/en/copilot/concepts/models/auto-model-selection), [May 2026 model changes](https://github.blog/changelog/2026-05-20-updates-to-available-models-in-copilot-on-web/)

---

## 2. Can the underlying model be detected programmatically?

**Yes, partially.** Detection depends on the Copilot surface:

### Copilot CLI
- The model name is shown in terminal output for every response (unless `-s`/`--silent` is used).
- The `COPILOT_MODEL` environment variable can be set to force a specific model (e.g. `claude-sonnet-4.5`, `gpt-5.2`).
- Model priority order: custom agent definition > `--model` CLI flag > `COPILOT_MODEL` env var > `model` key in `~/.copilot/settings.json` > CLI default.

### Copilot Chat (IDE)
- Hover over any response to see which model generated it.
- The model picker in the UI shows the currently selected model.
- No programmatic API or environment variable exposes this to instruction files or scripts during a session.

### Copilot Cloud Agent
- The model used is shown at the end of each response.

### Key limitation
There is **no API, environment variable, or response header** that instruction files (AGENTS.md, copilot-instructions.md) can query at runtime to detect the active model. The model is visible to the *user* via the UI, but not to the *instruction file* or any script running within the session. A model can self-report its identity if asked (e.g. Claude will say it is Claude), but this is unreliable for structured identification — models may hallucinate, and the response is not machine-parseable.

**Source:** [Copilot CLI programmatic reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference)

---

## 3. Should AI_IDENTITY capture the underlying model?

### Current state
`AI_IDENTITY: GitHub Copilot` — the identity is the *tool*, not the model.

### Three options considered

**Option A: Keep `GitHub Copilot` only (recommended)**

Rationale:
- AI_IDENTITY identifies the *tool environment*, not the intelligence layer. Just as `Claude Code` doesn't say `Claude Code (Opus 4.6)` — it could be running Opus, Sonnet, or Haiku depending on settings — Copilot shouldn't identify its model either.
- The model can change between sessions, mid-session (with auto-selection), or even per-request. Embedding it in AI_IDENTITY creates a moving target.
- Session-search already captures the tool identity for filtering purposes. The model name is metadata, not identity.
- There is no reliable programmatic way to detect the model from within instruction files, making a composite identity impractical to produce consistently.

**Option B: Composite identity `GitHub Copilot (Claude Sonnet 4.6)`**

Rationale:
- Captures richer information for session-search queries.
- Helpful for debugging when model-specific behaviour differences are suspected.

Problems:
- No reliable detection mechanism from within instruction files.
- Would require the model to self-identify, which is unreliable.
- Breaks the consistent `AI_IDENTITY` contract — other tools don't include their model version.
- Auto-selection makes the model non-deterministic even within a single session.

**Option C: Separate field `AI_MODEL` alongside `AI_IDENTITY`**

Rationale:
- Clean separation of tool vs. model.
- Could be useful as a future extension for all AIs (e.g. Cursor also supports multiple models).

Problems:
- Same detection problem as Option B.
- Adds complexity to session-search schema, archive format, and all wrapper files.
- Premature — other multi-model tools (Cursor, Continue, Aider) would need the same treatment.

### Recommendation

**Option A.** Keep `AI_IDENTITY: GitHub Copilot`. The identity system identifies the tool, not the model. This is consistent with how Claude Code works (never reports which Claude model is active) and avoids an unreliable detection mechanism.

If model tracking becomes important in future, Option C is the right long-term path — but it should be designed as a project-wide feature for all multi-model tools, not a Copilot-specific hack.

---

## 4. Does the underlying model affect instruction following?

**Yes, significantly.** This is the most consequential finding.

### Observed differences
- Each model family (GPT, Claude, Gemini) has different strengths in instruction following, context handling, and reasoning depth.
- GitHub's own model comparison notes that models vary in speed, hallucination rates, and task-specific performance.
- Some models are better at working with certain languages than others.
- MAI-Code-1-Flash is noted for "strong instruction following"; other models are not explicitly rated on this dimension.

### What this means for AGENTS.md
AGENTS.md is a 487-line instruction file with complex rules (session startup, CONTEXT.md schema, logging format, propagation rules, etc.). Its effectiveness depends heavily on:

1. **Context window** — all models in the Copilot pool support large context windows (100K+), so AGENTS.md fits comfortably. Not a differentiator.
2. **Instruction adherence** — Claude models are generally strong at following detailed, multi-step instructions. GPT models vary by version. Gemini models have historically been weaker at strict rule compliance.
3. **Self-identification** — Claude models will correctly identify themselves as Claude if asked, but within Copilot they should identify as "GitHub Copilot" per the wrapper file. Whether models reliably suppress their native identity in favour of the wrapper instruction depends on the model.
4. **Tool use** — MCP tool calling behaviour may vary between models when accessed through Copilot.

### Practical impact
A user running Copilot with Claude Opus 4.7 will likely get near-perfect AGENTS.md compliance. The same user switching to GPT-5 mini or Gemini 3 Flash may see degraded compliance with complex multi-step procedures (session startup, build close-out, propagation chains).

**This is not something the wrapper file can fully compensate for.** Different models have fundamentally different instruction-following capabilities. The wrapper file should acknowledge this honestly rather than pretending all models are equivalent.

---

## 5. How should the Copilot wrapper file account for model differences?

### Current state
`.github/copilot-instructions.md` is 28 lines — a thin wrapper that points to AGENTS.md and adds Copilot-specific tool mappings. It makes no mention of model differences.

### How Copilot loads instruction files
Copilot loads instruction files in this priority order:
1. Personal instructions (highest)
2. Repository instructions: `.github/copilot-instructions.md`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`
3. Path-specific instructions: `.github/instructions/*.instructions.md`
4. Organisation instructions (lowest)

**Critical finding:** CLAUDE.md and GEMINI.md are loaded as "always-on instructions" — the documentation does not confirm that they are conditionally loaded based on the active model. This means Copilot may load CLAUDE.md (Book Dragon's Claude-specific wrapper with session-search hooks and settings-check) even when running a GPT or Gemini model.

This is a potential problem: CLAUDE.md references Claude-specific features (`.claude/settings.json`, Claude MCP tool permissions, session-search scheduled tasks). If loaded when a non-Claude model is active, these instructions would be confusing or misleading to the model.

### Proposed changes to the Copilot wrapper

**Add a model-awareness note.** The wrapper should acknowledge that Copilot runs multiple models and set expectations:

```markdown
## Model awareness

GitHub Copilot can use models from multiple providers (OpenAI, Anthropic, Google,
Microsoft). The underlying model may vary between sessions or be selected by the
user. All models should follow AGENTS.md identically. If you find that a complex
multi-step procedure (such as session startup or build close-out) is beyond your
current capabilities, complete as many steps as you can and clearly state which
steps you were unable to perform.
```

**Do not add model-conditional logic.** There is no reliable way for the instruction file to detect which model is active, so branching logic ("if you are Claude, do X; if you are GPT, do Y") would be fragile and likely to confuse models.

**Keep the wrapper thin.** The current 28-line approach is correct. AGENTS.md carries the rules; the wrapper adds only Copilot-specific context. The model-awareness note is a 6-line addition.

---

## Summary of recommendations

| Question | Recommendation | Action required |
|---|---|---|
| AI_IDENTITY format | Keep `GitHub Copilot` — no model suffix | None |
| AGENTS.md changes | None warranted | None |
| Copilot wrapper changes | Add a "Model awareness" section (6 lines) | Propose for review |
| verify.py changes | None needed | None |
| AGENT-SETUP.md changes | Add a note about model selection in the Copilot section | Propose for review |
| CLAUDE.md loading risk | Investigate whether Copilot conditionally loads CLAUDE.md | Future research — not blocking |

### Proposed wrapper addition

Add to `.github/copilot-instructions.md` after the "Copilot-specific notes" section:

```markdown
## Model awareness

GitHub Copilot can use models from multiple providers (OpenAI, Anthropic, Google,
Microsoft). The underlying model may vary between sessions or be selected by the
user. All models should follow AGENTS.md identically. If a complex multi-step
procedure (such as session startup or build close-out) is beyond your current
capabilities, complete as many steps as you can and clearly state which steps you
were unable to perform.
```

### Proposed AGENT-SETUP.md addition

Add to the GitHub Copilot section, after the existing "Note" paragraph:

```markdown
**Model selection:** Copilot supports 23+ models from OpenAI, Anthropic, Google,
and Microsoft. The user selects the model via the model picker, or Copilot's
auto-selection chooses one based on task complexity. Model choice does not affect
which instruction files are loaded — `AGENTS.md` and `.github/copilot-instructions.md`
apply regardless of model. However, instruction-following quality may vary between
models. For best results with Book Dragon's complex multi-step procedures, use a
high-capability model (Claude Opus, GPT-5.4+, or Gemini 3.1 Pro).
```

---

## Open questions for future research

1. **CLAUDE.md conditional loading** — does Copilot load CLAUDE.md only when a Claude model is active, or always? The official docs are ambiguous. If always loaded, this could cause instruction pollution for non-Claude models. Worth testing empirically.
2. **Model-specific compliance testing** — run the Book Dragon session startup with GPT-5 mini, Claude Sonnet, and Gemini 3 Flash via Copilot and compare compliance. Would produce concrete data on which models can handle AGENTS.md.
3. **AI_MODEL as a future field** — if model tracking becomes valuable for session-search analytics, design a project-wide `AI_MODEL` field that all multi-model tools (Copilot, Cursor, Continue, Aider) can populate. This is a bigger design task and not urgent.

---

## Sources

- [GitHub Docs — Supported models](https://docs.github.com/en/copilot/reference/ai-models/supported-models)
- [GitHub Docs — Model comparison](https://docs.github.com/en/copilot/reference/ai-models/model-comparison)
- [GitHub Docs — Auto model selection](https://docs.github.com/en/copilot/concepts/models/auto-model-selection)
- [GitHub Docs — Copilot CLI programmatic reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference)
- [GitHub Docs — Custom instructions](https://docs.github.com/copilot/customizing-copilot/adding-custom-instructions-for-github-copilot)
- [GitHub Docs — Change the chat model](https://docs.github.com/en/copilot/how-tos/use-ai-models/change-the-chat-model)
- [VS Code — Custom instructions](https://code.visualstudio.com/docs/agent-customization/custom-instructions)
- [GitHub Blog — Copilot coding agent supports AGENTS.md](https://github.blog/changelog/2025-08-28-copilot-coding-agent-now-supports-agents-md-custom-instructions/)
- [GitHub Blog — May 2026 model changes](https://github.blog/changelog/2026-05-20-updates-to-available-models-in-copilot-on-web/)
- [GitHub Blog — Claude Fable 5 GA](https://github.blog/changelog/2026-06-09-claude-fable-5-is-generally-available-for-github-copilot/)
- [GitHub Blog — Model selection for Claude and Codex agents](https://github.blog/changelog/2026-04-14-model-selection-for-claude-and-codex-agents-on-github-com/)
- [GitHub Blog — Copilot SDK GA](https://github.blog/changelog/2026-06-02-copilot-sdk-is-now-generally-available/)
- [TechCrunch — Copilot goes multi-model](https://techcrunch.com/2024/10/29/githubs-copilot-goes-multi-model-and-adds-support-for-anthropics-claude-and-googles-gemini/)
