# Requirements

## AI system

Book Dragon is AI-agnostic — it works with any AI that reads `AGENTS.md`. See `README.md` for the full list of supported AIs and `AGENT-SETUP.md` for per-AI setup instructions.

## System requirements

**Python 3.9 or later** is the only current system-level requirement. Install it from [python.org](https://python.org), or ask an AI assistant to walk you through installation for your operating system.

## Python packages by workflow

Most workflows use the Python standard library only and need no additional packages. The exception is web-research.

| Workflow | Packages required | Install command |
|---|---|---|
| audit | None — standard library only | — |
| create-wiki | None — standard library only | — |
| link-check | None — standard library only | — |
| weather | None — standard library only | — |
| journal | None — standard library only | — |
| web-research | See requirements.txt | `pip install -r skills/web-research/scripts/requirements.txt` |
| biblio-tools | mcp >= 1.0.0 (Python 3.10+) | `pip install -r workflows/biblio-tools/requirements.txt` |

Run the web-research install command once before using that workflow. The biblio-tools MCP server requires Python 3.10+ (the MCP SDK requirement) — if running Python 3.9, the MCP server cannot start but all underlying scripts still work via direct shell commands. All other workflows are ready to use as soon as Python is installed.

## API keys

Some workflows use optional external APIs for richer results. Keys are stored in `.env` at the project root — copy `.env.example` to `.env` and fill in only the values you need. No API keys are required for core functionality; they extend web-research source coverage only.

For a full guide to obtaining and configuring API keys — including free tier details for each provider — see `workflows/web-research/SETUP.md`.
