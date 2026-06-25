# Session Search Skill

## What this skill does

Searches the Book Dragon full conversation archive across all indexed AI sessions and returns the most relevant snippets for a given query. Enables recall of past decisions, conversations, and context that predates the current session.

## When to invoke

Trigger this skill when the user asks questions such as:

- "When did we talk about X?"
- "What did we decide about Y?"
- "Search our history for..."
- "Recall the conversation where we discussed Z"
- "Did we ever discuss X?"
- "What was our reasoning behind...?"
- "Can you find what we said about...?"

## How to run it

```
python workflows/session-search/scripts/search.py "your query here"
```

### Optional flags

| Flag | Description |
|------|-------------|
| `--limit N` | Return at most N results (default: 10) |
| `--since YYYY-MM-DD` | Only show results from after this date |
| `--source claude-code\|cowork` | Limit to one source |
| `--hostname NAME` | Limit to a specific machine |

### Examples

```
python workflows/session-search/scripts/search.py "memory workflow"
python workflows/session-search/scripts/search.py "USER.md" --since 2026-06-01
python workflows/session-search/scripts/search.py "journal" --source claude-code --limit 5
```

## What to do with results

Read the returned snippets and respond in plain prose. Describe what was actually discussed, cite the session title and approximate date, and note the source/AI identity. Do not speculate about or infer content that was not present in the returned snippets.

If results appear sparse or missing:
1. Run the indexer to ensure the archive is up to date:
   ```
   python workflows/session-search/scripts/index.py
   ```
2. Try broader search terms - FTS5 is keyword-based, not semantic.

## Important limitations

- **Keyword search only** - FTS5 does not match synonyms or concepts; only exact words. If a query returns nothing, try alternative wording.
- **Index freshness** - `index.py` runs at startup for every AI that follows AGENTS.md and hourly via scheduled maintenance. A session that is still open or was closed without any hook/scheduler run may not appear yet. Running `index.py` manually catches anything missed.
- **Cowork lag** - Cowork sessions are captured by scheduled maintenance, not in real time. Recent Cowork sessions may not appear until the next scheduled run or startup index pass.
