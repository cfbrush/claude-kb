# Claude Knowledge Base — Starter Kit

**For the human:** put this `claude-kb-starter` folder anywhere on your
computer, open Claude Code, and say:

> Read BOOTSTRAP.md in <path to this folder> and set up my knowledge base.

**For Claude Code:** you are installing a cross-machine knowledge base for
this user. Everything below is your instructions. The code in `tools/` is
already written and debugged — do NOT rewrite it; configure it.

## The concept

A shared memory for all the user's projects that stays consistent across
their computers:

- **Notes are plain markdown files** in a folder the user already syncs
  between machines (Dropbox, OneDrive, Syncthing, Resilio, iCloud Drive —
  any file-sync tool). One fact per note, organized by project.
- **Never put a binary database (SQLite, ChromaDB, etc.) in the sync
  folder** — sync tools corrupt multi-writer binary files. That constraint
  shaped everything here.
- **Each machine builds its own local vector index** (at
  `~/.claude/kb-index/`, outside the synced folder) from the markdown,
  enabling semantic search. Sync conflicts can never corrupt it.
- **Embeddings are optional.** With no embedding backend, search falls back
  to keyword scoring — still useful, zero dependencies. The tool degrades
  gracefully whenever the backend is unreachable.
- Claude Code on every machine reads/writes the KB via `tools/kb.py`
  (Python ≥3.9, stdlib only — nothing to install), guided by a snippet in
  `~/.claude/CLAUDE.md`.

## Step 1 — Interview the user

Ask (use your question tool if available):

1. **Where is your synced folder, and what syncs it?** (Dropbox/OneDrive/
   Syncthing/Resilio/iCloud/other.) The KB will live at
   `<synced folder>/claude-knowledge/`.
   - iCloud/OneDrive/Dropbox users: warn them to mark that folder
     "always keep on this device" / "available offline" — online-only
     placeholder files break local search.
2. **Embeddings — pick one:**
   - **Ollama on this machine** — best default if they have or can install
     Ollama; needs `ollama pull nomic-embed-text` (~270 MB), free, offline.
   - **Ollama on another machine they can reach** (LAN/Tailscale) — give
     the URL, e.g. `http://hostname:11434`. The serving machine must have
     the model pulled and Ollama bound beyond localhost
     (`OLLAMA_HOST=0.0.0.0`).
   - **OpenAI-compatible API** — e.g. model `text-embedding-3-small`;
     needs an API key in an env var on every machine; costs cents.
   - **None** — keyword search only. Fine to start; can add embeddings
     later, `kb.py reindex` backfills.
3. **What projects?** Collect a few short slugs (e.g. `webapp`, `thesis`,
   `general`) for organizing notes.

## Step 2 — Install

1. Create `<synced folder>/claude-knowledge/` with subdirs `notes/general/`
   and `tools/`.
2. Copy `tools/kb.py`, `tools/kb_mcp.py`, `tools/claude-md-snippet.md` from
   this starter into its `tools/`.
3. Write `<synced folder>/claude-knowledge/kb-config.json` per the
   interview. Examples:

   Ollama (local and/or remote — candidates tried in order, and an
   endpoint is only chosen if it actually has the model pulled):
   ```json
   {"embedding": {"provider": "ollama", "model": "nomic-embed-text",
     "endpoints": ["http://localhost:11434", "http://otherhost:11434"]}}
   ```
   OpenAI-compatible:
   ```json
   {"embedding": {"provider": "openai", "model": "text-embedding-3-small",
     "endpoint": "https://api.openai.com/v1/embeddings",
     "api_key_env": "OPENAI_API_KEY"}}
   ```
   No embeddings:
   ```json
   {"embedding": {"provider": "none"}}
   ```
4. If they chose local Ollama: run `ollama pull nomic-embed-text` now.
5. Test (Windows: `py` or `python` instead of `python3`):
   ```
   python3 "<kb>/tools/kb.py" add "test note about the knowledge base" --project general
   python3 "<kb>/tools/kb.py" search "knowledge base"
   python3 "<kb>/tools/kb.py" status
   ```
   Semantic search should score ~0.5–0.8; keyword fallback prints a warning
   but still returns results. Both are success.

## Step 3 — Teach Claude Code to use it

APPEND the contents of `tools/claude-md-snippet.md` to this machine's
`~/.claude/CLAUDE.md` (create the file if missing; NEVER overwrite existing
content; skip if the `<!-- claude-knowledge-base -->` marker is already
there). Replace `<KB_PATH>` with the absolute KB path and `<PYTHON>` with
the working Python command.

## Step 4 — Optional automation (recommended)

Merge two hooks into `~/.claude/settings.json` (preserve everything already
there; validate JSON after — a malformed file silently disables all its
settings):

- **SessionStart**: `<PYTHON> "<KB_PATH>/tools/kb.py" hook-context`,
  `"timeout": 20` — reindexes newly synced notes, injects KB status into
  each session.
- **Stop**: `<PYTHON> "<KB_PATH>/tools/kb.py" hook-stop`, `"timeout": 10`
  — one reminder per session to save durable facts. kb.py enforces
  once-per-session itself via a marker file; do not rely on the hook
  option `"once": true` (it re-arms whenever settings reload).

Do NOT append `2>/dev/null || true` on Windows — POSIX-only, breaks cmd.

## Step 5 — Optional: Claude Desktop app access

If the Claude Desktop app is installed, register the MCP server in
`claude_desktop_config.json` (Mac:
`~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`),
preserving existing entries:

```json
"mcpServers": {"knowledge-base": {"command": "<absolute python path>",
  "args": ["<KB_PATH>/tools/kb_mcp.py"]}}
```

Restart the app. On Mac use `/usr/bin/python3` (see gotchas).

## Step 6 — Their other machines

Generate a `SETUP.md` inside their new `claude-knowledge/` folder: a
prompt for Claude Code on their other machines covering — verify sync
arrived, find the local KB path, run `kb.py status`, repeat steps 3–5 with
that machine's paths, test a search, report. Customize it to their sync
tool and embedding config. (The KB folder syncs, so SETUP.md travels with
it — on each other machine they just say "read SETUP.md and do it".)

## Step 7 — Report

Summarize: KB path, embedding config, test results, what was added to
CLAUDE.md/settings/desktop config, and tell the user how to use it
("mention something worth remembering and Claude saves it; ask about past
decisions and Claude searches it") and how to set up their other machines.

## Known gotchas (hard-won — keep respecting them)

- Binary DBs in synced folders get corrupted. Markdown + local index only.
- Ollama batch-embed returns HTTP 400 when a text exceeds the model
  context, `truncate:true` notwithstanding — kb.py already handles this
  (char cap + per-item halving retry). Don't "simplify" it away.
- kb.py only selects an Ollama endpoint that actually has the embedding
  model pulled (checks `/api/tags`) — an endpoint being up is not enough.
- macOS Local Network privacy can block non-Apple Python from reaching
  LAN/Tailscale IPs with `[Errno 9] Bad file descriptor` while curl works.
  Use `/usr/bin/python3`, or approve the terminal app in System Settings →
  Privacy & Security → Local Network.
- macOS `socket.gethostname()` may return a generic name ("Mac") — kb.py
  uses `scutil --get LocalHostName` there for the machine tag.
- `INDEX.md` is derived; if the sync tool ever reports a conflict on it,
  `kb.py reindex` rebuilds it. Note files themselves are add-only with
  date+machine+slug filenames, so they don't conflict.
