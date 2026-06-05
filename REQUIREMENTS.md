# Requirements

## AI system

This project requires **Claude Code** or **Claude Cowork**, both of which require a paid Claude plan. The free Claude plan provides chat only and is not sufficient to run this project.

**How to get set up:**
- **Desktop app** (recommended) — includes Chat, Code, and Cowork in one installation. Download from [claude.com/download](https://claude.com/download).
- **Claude Code CLI** — terminal-based, without the desktop app. Requires a paid plan or API credits.
- **IDE extension** — available for VS Code and JetBrains. Requires a paid plan or API credits.

For plan details and pricing: [anthropic.com/claude-code](https://www.anthropic.com/claude-code).

Support for other AI systems is planned; when added, Claude will remain a supported option.

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

Run the web-research install command once before using that workflow. All other workflows are ready to use as soon as Python is installed.

## API keys

Some workflows use optional external APIs for richer results. Keys are stored in `.env` at the project root — copy `.env.example` to `.env` and fill in only the values you need. No API keys are required for core functionality; they extend web-research source coverage only.

For a full guide to obtaining and configuring API keys — including free tier details for each provider — see `workflows/web-research/SETUP.md`.
