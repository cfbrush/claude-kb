#!/usr/bin/env python3
"""kb.py — cross-machine knowledge base CLI.

Markdown notes live in the synced folder (this script's grandparent dir).
A vector index is kept locally per machine (~/.claude/kb-index/) and is
rebuilt from the notes, so sync conflicts can never corrupt it.

Embedding setup lives in kb-config.json at the KB root (synced with the
notes). Providers: "ollama" (default), "openai" (any OpenAI-compatible
/v1/embeddings endpoint; API key from env), or "none" (keyword search only).
The KB_OLLAMA env var overrides the configured ollama endpoint list.
If no embedding backend responds, search falls back to keyword scoring
over the note files — nothing breaks.

Commands:
  kb.py add "text" --project X [--tags a,b]
  kb.py search "query" [--project X] [-k 8]
  kb.py reindex
  kb.py status

Python >= 3.9, stdlib only. Works on macOS and Windows.
"""

import argparse
import hashlib
import json
import math
import os
import re
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

KB_ROOT = Path(__file__).resolve().parent.parent
NOTES_DIR = KB_ROOT / "notes"
INDEX_MD = KB_ROOT / "INDEX.md"
CONFIG_FILE = KB_ROOT / "kb-config.json"
# index file is keyed by KB root so multiple KBs on one machine (or a test
# copy of the tools) can never clobber each other's index
LOCAL_INDEX = (Path.home() / ".claude" / "kb-index" /
               f"index-{hashlib.sha256(str(KB_ROOT).encode()).hexdigest()[:12]}.json")

DEFAULT_CONFIG = {
    "embedding": {
        "provider": "ollama",          # "ollama" | "openai" | "none"
        "model": "nomic-embed-text",
        "endpoints": ["http://localhost:11434"],
        # openai provider instead uses:
        #   "endpoint": "https://api.openai.com/v1/embeddings",
        #   "model": "text-embedding-3-small",
        #   "api_key_env": "OPENAI_API_KEY",
    }
}


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if CONFIG_FILE.exists():
        try:
            user = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            cfg["embedding"].update(user.get("embedding", {}))
        except (json.JSONDecodeError, OSError):
            print(f"[kb] warning: could not parse {CONFIG_FILE}, "
                  f"using defaults", file=sys.stderr)
    return cfg


CONFIG = load_config()
EMB = CONFIG["embedding"]

_resolved_endpoint = None  # cache within one invocation; False = none reachable


def resolve_endpoint():
    """Return a usable embedding endpoint base URL, or None."""
    global _resolved_endpoint
    if _resolved_endpoint is not None:
        return _resolved_endpoint or None
    provider = EMB.get("provider", "ollama")
    if provider == "none":
        _resolved_endpoint = False
        return None
    if provider == "openai":
        key = os.environ.get(EMB.get("api_key_env", "OPENAI_API_KEY"))
        base = EMB.get("endpoint", "https://api.openai.com/v1/embeddings")
        _resolved_endpoint = base if key else False
        return _resolved_endpoint or None
    candidates = ([os.environ["KB_OLLAMA"]] if os.environ.get("KB_OLLAMA")
                  else EMB.get("endpoints", ["http://localhost:11434"]))
    want = EMB.get("model", "nomic-embed-text").split(":")[0]
    for base in candidates:
        base = base.rstrip("/")
        try:
            # reachable AND has the embedding model pulled — an endpoint
            # without the model (e.g. a machine running its own ollama for
            # other things) must fall through to the next candidate
            with urllib.request.urlopen(base + "/api/tags", timeout=3) as r:
                models = json.loads(r.read()).get("models", [])
            if any(m.get("name", "").split(":")[0] == want for m in models):
                _resolved_endpoint = base
                return base
        except (urllib.error.URLError, socket.timeout, OSError, ValueError,
                json.JSONDecodeError):
            continue
    _resolved_endpoint = False
    return None


EMBED_MAX_CHARS = 6000  # nomic-embed-text context is 2048 tokens
EMBED_BATCH = 32


def embed(texts, kind="search_document"):
    """Return list of vectors, or None if the backend is unreachable/errored.
    Long texts are truncated for embedding only (note files untouched)."""
    base = resolve_endpoint()
    if not base:
        return None
    provider = EMB.get("provider", "ollama")
    model = EMB.get("model", "nomic-embed-text")
    # nomic models are trained with task prefixes; other models aren't
    prefix = f"{kind}: " if "nomic" in model else ""

    def call(inputs):
        if provider == "openai":
            payload = json.dumps({"model": model, "input": inputs}
                                 ).encode("utf-8")
            key = os.environ.get(EMB.get("api_key_env", "OPENAI_API_KEY"), "")
            req = urllib.request.Request(
                base, data=payload,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())["data"]
                return [d["embedding"]
                        for d in sorted(data, key=lambda d: d["index"])]
        payload = json.dumps({
            "model": model, "truncate": True, "input": inputs,
        }).encode("utf-8")
        req = urllib.request.Request(
            base + "/api/embed", data=payload,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())["embeddings"]

    def embed_one(text):
        # Some token-dense texts exceed the model context even after the
        # char cap (and ollama's truncate:true is unreliable there) —
        # halve until the backend accepts.
        limit = EMBED_MAX_CHARS
        while limit >= 200:
            try:
                return call([prefix + text[:limit]])[0]
            except urllib.error.HTTPError:
                limit //= 2
        return None

    out = []
    try:
        for i in range(0, len(texts), EMBED_BATCH):
            batch = [prefix + t[:EMBED_MAX_CHARS]
                     for t in texts[i:i + EMBED_BATCH]]
            try:
                out.extend(call(batch))
            except urllib.error.HTTPError:
                for t in texts[i:i + EMBED_BATCH]:
                    v = embed_one(t)
                    if v is None:
                        print("[kb] embed error: one note rejected even "
                              "after shrinking; skipping batch",
                              file=sys.stderr)
                        return None
                    out.append(v)
    except (urllib.error.URLError, socket.timeout, OSError, KeyError):
        return None
    return out


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def load_local_index():
    if LOCAL_INDEX.exists():
        try:
            return json.loads(LOCAL_INDEX.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def atomic_write(path, text):
    """Write via temp file + rename so concurrent sessions never see a
    torn/partial file."""
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def save_local_index(idx):
    LOCAL_INDEX.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(LOCAL_INDEX, json.dumps(idx))


def note_files():
    if not NOTES_DIR.exists():
        return []
    return sorted(p for p in NOTES_DIR.rglob("*.md")
                  if not p.name.startswith("."))


def rel(p):
    return p.relative_to(KB_ROOT).as_posix()


def parse_note(path):
    """Return (meta dict, body str)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    meta = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            body = parts[2].strip()
            for line in parts[1].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip().strip("[]")
    return meta, body


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def regenerate_index_md():
    lines = ["# Knowledge Base Index", "",
             "One line per note. Regenerated by `kb.py` — if this file ever",
             "gets a sync conflict, just run `kb.py reindex` to rebuild it.", ""]
    by_project = {}
    for p in note_files():
        meta, body = parse_note(p)
        first = next((l for l in body.splitlines() if l.strip()), "")[:100]
        project = meta.get("project") or p.parent.name
        by_project.setdefault(project, []).append(
            f"- `{rel(p)}` — {first}")
    for proj in sorted(by_project):
        lines.append(f"## {proj}")
        lines.extend(by_project[proj])
        lines.append("")
    atomic_write(INDEX_MD, "\n".join(lines))


def reindex(verbose=True):
    """Embed new/changed notes, drop deleted ones, regenerate INDEX.md.
    Returns (n_embedded, n_pending). Never fails if the backend is down —
    unembedded notes just stay pending."""
    idx = load_local_index()
    files = note_files()
    current = {}
    to_embed = []
    for p in files:
        text = p.read_text(encoding="utf-8", errors="replace")
        h = sha(text)
        r = rel(p)
        current[r] = h
        if idx.get(r, {}).get("sha") != h:
            to_embed.append((r, text))
    # drop deleted notes
    for r in list(idx):
        if r not in current:
            del idx[r]
    n_done = 0
    if to_embed:
        vecs = embed([t for _, t in to_embed])
        if vecs:
            for (r, text), v in zip(to_embed, vecs):
                idx[r] = {"sha": current[r], "vec": v}
            n_done = len(to_embed)
        elif verbose:
            why = ("embeddings disabled (provider: none)"
                   if EMB.get("provider") == "none"
                   else "embedding backend unreachable")
            print(f"[kb] {why} — {len(to_embed)} note(s) pending; "
                  f"keyword search still works.", file=sys.stderr)
    save_local_index(idx)
    regenerate_index_md()
    if verbose and n_done:
        print(f"[kb] embedded {n_done} new/changed note(s)")
    return n_done, len(to_embed) - n_done


def machine_name():
    """Short unique-ish machine name. macOS gethostname() often returns a
    generic DHCP name like 'Mac', so prefer scutil's LocalHostName there."""
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["scutil", "--get", "LocalHostName"],
                                 capture_output=True, text=True, timeout=5)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip().lower()
        except Exception:
            pass
    return socket.gethostname().split(".")[0].lower()


def slugify(text, max_words=6):
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())[:max_words]
    return "-".join(words) or "note"


def cmd_add(args):
    project = args.project or "general"
    proj_dir = NOTES_DIR / project
    proj_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    machine = machine_name()
    # machine name in the filename prevents cross-machine collisions when
    # two computers save similar notes before the sync tool merges them
    base = f"{today}-{machine}-{slugify(args.text)}"
    path = proj_dir / f"{base}.md"
    n = 2
    while path.exists():
        path = proj_dir / f"{base}-{n}.md"
        n += 1
    tags = f"[{args.tags}]" if args.tags else "[]"
    path.write_text(
        f"---\nproject: {project}\ntags: {tags}\ndate: {today}\n"
        f"machine: {machine}\n---\n\n{args.text.strip()}\n",
        encoding="utf-8")
    print(f"[kb] wrote {rel(path)}")
    reindex(verbose=True)


def cmd_search(args):
    reindex(verbose=False)  # pick up notes synced in from other machines
    idx = load_local_index()
    qvec = embed([args.query], kind="search_query")
    results = []  # (score, relpath)
    if qvec:
        q = qvec[0]
        for r, entry in idx.items():
            if args.project and not r.startswith(f"notes/{args.project}/"):
                continue
            results.append((cosine(q, entry["vec"]), r))
        mode = "semantic"
    else:
        why = ("embeddings disabled" if EMB.get("provider") == "none"
               else "embedding backend unreachable")
        print(f"[kb] {why} — keyword search fallback", file=sys.stderr)
        terms = [t for t in re.findall(r"[a-zA-Z0-9]+", args.query.lower())
                 if len(t) > 2]
        for p in note_files():
            r = rel(p)
            if args.project and not r.startswith(f"notes/{args.project}/"):
                continue
            text = p.read_text(encoding="utf-8", errors="replace").lower()
            score = sum(text.count(t) for t in terms)
            if score > 0:
                results.append((float(score), r))
        mode = "keyword"
    results.sort(reverse=True)
    top = results[:args.k]
    if not top:
        print(f"[kb] no results ({mode})")
        return
    for score, r in top:
        meta, body = parse_note(KB_ROOT / r)
        print(f"\n### {r}  (score {score:.3f}, {mode})")
        print(body[:1500])


def cmd_reindex(args):
    n_done, n_pending = reindex(verbose=True)
    total = len(note_files())
    print(f"[kb] {total} notes, {n_done} (re)embedded, {n_pending} pending")


def cmd_hook_context(args):
    """Claude Code SessionStart hook: quiet reindex + inject KB context."""
    try:
        n_done, _ = reindex(verbose=False)
    except Exception:
        n_done = 0
    base = resolve_endpoint()
    ctx = (
        f"[claude-knowledge-base] Cross-machine KB: {len(note_files())} notes "
        f"at {KB_ROOT}. "
        f"Semantic search: {'available' if base else 'OFFLINE (keyword fallback active)'}. "
        + (f"Indexed {n_done} newly synced note(s). " if n_done else "")
        + f'Search: `python3 "{Path(__file__).resolve()}" search "<topic>"`. '
        f"Save durable cross-machine facts (decisions, conventions, project "
        f'status) with: `python3 "{Path(__file__).resolve()}" add "<fact>" '
        f'--project <slug>`.'
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart", "additionalContext": ctx}}))


def cmd_hook_stop(args):
    """Claude Code Stop hook: remind once per session to save durable facts.
    Uses a marker file keyed by session_id because the hook config's
    `once: true` re-arms whenever settings.json is reloaded."""
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    sid = str(data.get("session_id", "unknown"))[:64]
    marker = Path(tempfile.gettempdir()) / f"kb-stop-hook-{sid}"
    if marker.exists():
        return  # already reminded this session — allow the stop silently
    marker.touch()
    print(json.dumps({"decision": "block", "reason":
        "[claude-knowledge-base] One-time end-of-session check: if this "
        "session established durable cross-machine facts (decisions, "
        "conventions, project status worth seeing on other computers), save "
        "each one now with kb.py add (command in CLAUDE.md). If nothing "
        "durable came up, finish without further comment."}))


def cmd_status(args):
    files = note_files()
    idx = load_local_index()
    embedded = sum(1 for r in idx if "vec" in idx[r])
    base = resolve_endpoint()
    print(f"KB root:        {KB_ROOT}")
    print(f"Notes:          {len(files)}")
    print(f"Locally indexed:{embedded:>4}")
    print(f"Local index:    {LOCAL_INDEX}")
    print(f"Embeddings:     "
          f"{base or 'unavailable (keyword fallback active)'} "
          f"(provider: {EMB.get('provider', 'ollama')})")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="add a note")
    p.add_argument("text")
    p.add_argument("--project", default="general")
    p.add_argument("--tags", default="")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("search", help="search notes")
    p.add_argument("query")
    p.add_argument("--project")
    p.add_argument("-k", type=int, default=8)
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("reindex", help="embed new/changed notes, rebuild INDEX.md")
    p.set_defaults(fn=cmd_reindex)

    p = sub.add_parser("status", help="show KB status")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("hook-context",
                       help="Claude Code SessionStart hook: reindex + JSON context")
    p.set_defaults(fn=cmd_hook_context)

    p = sub.add_parser("hook-stop",
                       help="Claude Code Stop hook: once-per-session save reminder")
    p.set_defaults(fn=cmd_hook_stop)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
