#!/usr/bin/env python3
"""kb_mcp.py — MCP stdio server exposing the knowledge base to the Claude
Desktop app (and any other MCP client).

Register in claude_desktop_config.json:
    "mcpServers": {
      "knowledge-base": {
        "command": "python3",
        "args": ["<path to this file>"]
      }
    }

Tools: kb_search, kb_add, kb_status. Stdlib only; reuses kb.py in this dir.
"""

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb  # noqa: E402

PROTOCOL_FALLBACK = "2024-11-05"

TOOLS = [
    {
        "name": "kb_search",
        "description": (
            "Search the user's cross-machine project knowledge base. "
            "Use when the user asks about past decisions, project "
            "status, conventions, or anything another work session may have "
            "recorded. Semantic search when the embedding server is up, "
            "keyword fallback otherwise."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for"},
                "project": {"type": "string",
                            "description": "Optional project slug filter"},
                "k": {"type": "integer", "description": "Max results",
                      "default": 8},
            },
            "required": ["query"],
        },
    },
    {
        "name": "kb_add",
        "description": (
            "Save a durable fact, decision, convention, or project status to "
            "the user's cross-machine knowledge base so it is visible on all "
            "their computers. One fact per note."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The fact to save"},
                "project": {"type": "string",
                            "description": "Project slug (e.g. general)"},
                "tags": {"type": "string",
                         "description": "Optional comma-separated tags"},
            },
            "required": ["text", "project"],
        },
    },
    {
        "name": "kb_status",
        "description": "Show knowledge base health: note count, index state, "
                       "embedding server reachability.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def do_search(args):
    kb.reindex(verbose=False)
    idx = kb.load_local_index()
    query, project = args["query"], args.get("project")
    k = args.get("k", 8)
    qvec = kb.embed([query], kind="search_query")
    results = []
    if qvec:
        for r, entry in idx.items():
            if project and not r.startswith(f"notes/{project}/"):
                continue
            results.append((kb.cosine(qvec[0], entry["vec"]), r))
        mode = "semantic"
    else:
        import re
        terms = [t for t in re.findall(r"[a-zA-Z0-9]+", query.lower())
                 if len(t) > 2]
        for p in kb.note_files():
            r = kb.rel(p)
            if project and not r.startswith(f"notes/{project}/"):
                continue
            text = p.read_text(encoding="utf-8", errors="replace").lower()
            score = sum(text.count(t) for t in terms)
            if score > 0:
                results.append((float(score), r))
        mode = "keyword"
    results.sort(reverse=True)
    if not results:
        return f"No results ({mode} search)."
    out = []
    for score, r in results[:k]:
        _, body = kb.parse_note(kb.KB_ROOT / r)
        out.append(f"### {r} (score {score:.3f}, {mode})\n{body[:1500]}")
    return "\n\n".join(out)


def do_add(args):
    import argparse
    ns = argparse.Namespace(text=args["text"], project=args["project"],
                            tags=args.get("tags", ""))
    buf = io.StringIO()
    stdout, sys.stdout = sys.stdout, buf
    try:
        kb.cmd_add(ns)
    finally:
        sys.stdout = stdout
    return buf.getvalue().strip() or "Note saved."


def do_status(args):
    files = kb.note_files()
    idx = kb.load_local_index()
    base = kb.resolve_endpoint()
    return (f"Notes: {len(files)}, locally indexed: {len(idx)}. "
            f"Embedding server: "
            f"{base or 'UNREACHABLE (keyword fallback active)'}. "
            f"KB root: {kb.KB_ROOT}")


HANDLERS = {"kb_search": do_search, "kb_add": do_add, "kb_status": do_status}


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, msg_id = req.get("method"), req.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": msg_id, "result": {
                "protocolVersion": req.get("params", {}).get(
                    "protocolVersion", PROTOCOL_FALLBACK),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "knowledge-base", "version": "1.0.0"},
            }})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": msg_id,
                  "result": {"tools": TOOLS}})
        elif method == "tools/call":
            params = req.get("params", {})
            name = params.get("name")
            handler = HANDLERS.get(name)
            if not handler:
                send({"jsonrpc": "2.0", "id": msg_id, "error": {
                    "code": -32602, "message": f"Unknown tool: {name}"}})
                continue
            try:
                text = handler(params.get("arguments", {}))
                send({"jsonrpc": "2.0", "id": msg_id, "result": {
                    "content": [{"type": "text", "text": text}]}})
            except Exception as e:
                send({"jsonrpc": "2.0", "id": msg_id, "result": {
                    "content": [{"type": "text",
                                 "text": f"Error: {type(e).__name__}: {e}"}],
                    "isError": True}})
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": msg_id, "result": {}})
        elif msg_id is not None:
            # unknown request — must answer
            send({"jsonrpc": "2.0", "id": msg_id, "error": {
                "code": -32601, "message": f"Method not found: {method}"}})
        # notifications (no id) are ignored


if __name__ == "__main__":
    main()
