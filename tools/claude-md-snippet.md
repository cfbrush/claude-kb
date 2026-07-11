<!-- claude-knowledge-base -->
# Cross-Machine Knowledge Base

A shared knowledge base for all projects syncs across my computers at:
`<KB_PATH>` (CLI: `<PYTHON> "<KB_PATH>/tools/kb.py"`)

**When to READ it:** When starting work on a project or when I ask about
past decisions/status, search it first:

    <PYTHON> "<KB_PATH>/tools/kb.py" search "<topic>" [--project <name>]

**When to WRITE to it:** When I state a durable fact, decision, convention,
or project status that should be visible on my other computers, save it:

    <PYTHON> "<KB_PATH>/tools/kb.py" add "<the fact>" --project <name> --tags <a,b>

Use short project slugs. One fact per note. Machine-local details (paths,
this machine's config) stay in normal Claude memory — only cross-machine
knowledge goes here.

Notes are plain markdown under `<KB_PATH>/notes/` — you can also read/grep
them directly. `kb.py status` shows health; if the embedding backend is
unreachable the tool automatically falls back to keyword search (not an
error).
<!-- /claude-knowledge-base -->
