# claude-kb

A cross-machine knowledge base for Claude Code: plain markdown notes in a
folder you already sync between computers (Dropbox, OneDrive, Syncthing,
Resilio, iCloud...), plus a per-machine vector index for semantic search.
Claude Code on every machine reads and writes it, so your projects stay
consistent no matter which computer you work on.

## Version

Current: **0.1.0-alpha** (2026-07-11) — initial release. See
[Releases](https://github.com/cfbrush/claude-kb/releases) for
version history.

## Quick start

1. Clone or download this repo (Code → Download ZIP) anywhere on your
   computer.
2. Open Claude Code and say:

   > Read BOOTSTRAP.md in <path to this folder> and set up my knowledge base.

3. Claude interviews you (which synced folder? embeddings via Ollama, an
   OpenAI-style API, or none?) and installs everything.

## Requirements

- Python ≥ 3.9 (standard library only — nothing to pip install)
- Claude Code
- Optional, for semantic search: Ollama with `nomic-embed-text`, or an
  OpenAI-compatible embeddings API. Without either, search is keyword-based
  and everything still works.

## What's in here

| File | Purpose |
|------|---------|
| `BOOTSTRAP.md` | Instructions for Claude Code: interview + install |
| `tools/kb.py` | The CLI: `add`, `search`, `reindex`, `status`, plus Claude Code hook subcommands |
| `tools/kb_mcp.py` | MCP server so the Claude Desktop app can use the KB too |
| `tools/claude-md-snippet.md` | Block that teaches Claude Code when to read/write the KB |

## Design in one paragraph

Sync tools corrupt multi-writer binary databases, so nothing binary ever
goes in the synced folder — notes are one-fact-per-file markdown with
date+machine+slug filenames (no conflicts), and each machine rebuilds its
own vector index locally from the markdown. Embeddings are optional and
every failure degrades to keyword search over plain text files. Worst case
is always "re-run `kb.py reindex`", never data loss.

## Updating

Fixes land here. `git pull` (or re-download), then replace the `tools/`
files inside your `claude-knowledge/` folder with the new ones — your notes
and config are untouched. Since `tools/` lives in your synced folder, your
other machines pick the update up automatically.
