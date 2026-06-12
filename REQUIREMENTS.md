# Requirements

## AI system

Book Dragon is AI-agnostic — it works with any AI that reads `AGENTS.md`. See `README.md` for the full list of supported AIs and `AGENT-SETUP.md` for per-AI setup instructions.

## System requirements

**Python 3.9 or later** is the only current system-level requirement. Install it from [python.org](https://python.org), or ask an AI assistant to walk you through installation for your operating system.

## Python packages by workflow

Book Dragon uses one canonical project environment at `.venv`. Create or repair it with:

```
python workflows/biblio-tools/scripts/setup.py
```

The setup script installs the root `requirements.txt`, which includes every workflow-specific manifest below. Dependency-bearing workflow entry points automatically hand themselves to `.venv`, so the same documented commands work in every supported AI.

| Workflow | Packages required | Install command |
|---|---|---|
| audit | None — standard library only | — |
| create-wiki | pypdf >= 6.0.0 | Included by root `requirements.txt` |
| link-check | None — standard library only | — |
| weather | None — standard library only | — |
| journal | None — standard library only | — |
| web-research | See workflow requirements | Included by root `requirements.txt` |
| biblio-tools | mcp >= 1.0.0 (Python 3.10+) | Included by root `requirements.txt`; skipped on Python 3.9 |

The Create Wiki dependency provides shared PDF extraction for every supported AI. The biblio-tools MCP server requires Python 3.10+ (the MCP SDK requirement); on Python 3.9 the root manifest skips MCP, while direct workflow scripts remain available.

## API keys

Some workflows use optional external APIs for richer results. Keys are stored in `.env` at the project root — copy `.env.example` to `.env` and fill in only the values you need. No API keys are required for core functionality; they extend web-research source coverage only.

For a full guide to obtaining and configuring API keys — including free tier details for each provider — see `workflows/web-research/SETUP.md`.
