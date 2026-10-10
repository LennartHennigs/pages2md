#!/usr/bin/env python3
"""pages_mcp -- an MCP server for pages2md.py and pages_edit.py (stdio and HTTP).

Four tools -- pages_read, pages_find, pages_replace, pages_edit -- each of which builds a
command line and runs the real script, so a tool call and the matching command say the same
thing. Standard library only.

    python3 pages_mcp.py --root ~/Documents                 # stdio, for VS Code / Claude
    PAGES_MCP_TOKEN=... python3 pages_mcp.py --http --root ~/Documents   # HTTP on 127.0.0.1

Only .pages files under a --root folder (or PAGES_MCP_ROOTS) can be touched. Edits are a dry
run until write=true, and a write needs `expect`, the document fingerprint. See the README.
"""
import argparse, hmac, json, os, subprocess, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_INFO = {"name": "pages2md", "version": "0.1.0"}
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = ("Read .pages files with pages_read and pages_find. Edits are a dry run until "
                "write=true, and a write needs expect (the fingerprint from pages_read "
                "format=fingerprint). Nothing written has been opened in real Pages yet: "
                "work on a copy.")
MAX_OUTPUT = 100_000          # characters returned to the model
MAX_BODY = 1 << 20            # bytes accepted over HTTP
TIMEOUT = 120                 # seconds per command
MIN_TOKEN = 16
WRITE_LOCK = threading.Lock()  # one writer at a time: a write reloads and rezips the package


class ToolError(Exception):
    """A problem the caller can fix; reported as an isError tool result."""


class Config:
    def __init__(self, roots, read_only=False):
        self.roots = list(roots)
        self.read_only = read_only


# --- arguments -----------------------------------------------------------------------------

def arg(args, key, kind, required=False, choices=None):
    value = args.get(key)
    if value is None:
        if required:
            raise ToolError(f"{key} is required")
        return None
    good = {bool: isinstance(value, bool),
            int: isinstance(value, int) and not isinstance(value, bool),
            str: isinstance(value, str)}[kind]
    if not good:
        raise ToolError(f"{key} must be a {kind.__name__}")
    if kind is str and "\x00" in value:
        raise ToolError(f"{key} contains a NUL character")
    if choices and value not in choices:
        raise ToolError(f"{key} must be one of: {', '.join(choices)}")
    return value


def check_keys(args, allowed):
    extra = sorted(set(args) - set(allowed))
    if extra:
        raise ToolError(f"unknown argument(s): {', '.join(extra)}; "
                        f"allowed: {', '.join(sorted(allowed))}")


def opt(argv, flag, value):
    """Append `--flag=value` (one token, so a value starting with `-` stays a value)."""
    if value is not None and value is not False:
        argv.append(flag if value is True else f"{flag}={value}")


def tristate(argv, value, on, off):
    if value is True:
        argv.append(on)
    elif value is False:
        argv.append(off)


def resolve_document(path, roots):
    if not path:
        raise ToolError("file is required")
    if not path.lower().endswith(".pages"):
        raise ToolError("file must be a .pages document")
    if not roots:
        raise ToolError("no document folders are configured: start the server with "
                        "--root DIR or set PAGES_MCP_ROOTS")
    if not os.path.isabs(path):
        path = os.path.join(roots[0], path)
    real = os.path.realpath(path)
    if not any(real == r or real.startswith(r.rstrip(os.sep) + os.sep) for r in roots):
        raise ToolError("file is outside the allowed folders")
    if not os.path.isfile(real):
        raise ToolError(f"no such file: {os.path.basename(real)}")
    return real


def guard_write(args, write):
    """A write must name the fingerprint the caller last saw."""
    if write and not arg(args, "expect", str):
        raise ToolError("write=true needs expect: the fingerprint from "
                        "pages_read format=fingerprint")


# --- tool builders: each returns (script, argv, mutating, cleanup) --------------------------

def noop():
    pass


READ_FORMATS = ("markdown", "plain", "json", "outline", "comments", "links", "styles",
                "fingerprint")


def build_read(args, cfg):
    check_keys(args, {"file", "format", "in_section", "page", "changes", "notes"})
    doc = resolve_document(arg(args, "file", str, True), cfg.roots)
    fmt = arg(args, "format", str, choices=READ_FORMATS) or "markdown"
    if fmt == "fingerprint":
        return "pages_edit.py", ["fingerprint", doc], False, noop
    argv = [f"--to={fmt}"]
    opt(argv, "--in", arg(args, "in_section", str))
    opt(argv, "--page", arg(args, "page", str))
    opt(argv, "--changes", arg(args, "changes", str, choices=("accept", "reject", "mark")))
    opt(argv, "--notes", arg(args, "notes", str, choices=("inline", "skip", "only")))
    return "pages2md.py", argv + ["--", doc], False, noop


def build_find(args, cfg):
    check_keys(args, {"file", "pattern", "regex", "raw", "where", "in_section", "page"})
    doc = resolve_document(arg(args, "file", str, True), cfg.roots)
    pattern = arg(args, "pattern", str, True)
    argv = ["find"]
    opt(argv, "--regex", arg(args, "regex", bool))
    opt(argv, "--raw", arg(args, "raw", bool))
    opt(argv, "--where", arg(args, "where", str))
    opt(argv, "--in", arg(args, "in_section", str))
    opt(argv, "--page", arg(args, "page", str))
    return "pages_edit.py", argv + ["--", pattern, doc], False, noop


def build_replace(args, cfg):
    check_keys(args, {"file", "find", "replace", "all", "occurrence", "regex", "where",
                      "in_section", "page", "track", "expect", "write"})
    doc = resolve_document(arg(args, "file", str, True), cfg.roots)
    find = arg(args, "find", str, True)
    if not find:
        raise ToolError("find must not be empty")
    replace = arg(args, "replace", str, True)
    write = bool(arg(args, "write", bool))
    guard_write(args, write)
    argv = ["replace", f"--find={find}", f"--replace={replace}"]
    opt(argv, "--all", arg(args, "all", bool))
    opt(argv, "--occurrence", arg(args, "occurrence", int))
    opt(argv, "--regex", arg(args, "regex", bool))
    opt(argv, "--where", arg(args, "where", str))
    opt(argv, "--in", arg(args, "in_section", str))
    opt(argv, "--page", arg(args, "page", str))
    tristate(argv, arg(args, "track", bool), "--track", "--no-track")
    opt(argv, "--expect", arg(args, "expect", str))
    opt(argv, "--write", write)
    return "pages_edit.py", argv + ["--", doc], write, noop


# name -> sub-command, allowed fields, required fields, "exactly one of" fields, and whether it
# takes --expect/--write ("guarded"), only --write ("plan"), or is gated by write=true at this
# layer because the CLI applies it at once ("gate").
EDIT_COMMANDS = {
    "comment_add": dict(sub=["comment", "add"], fields=("on", "text", "where", "in_section", "page"),
                        req=("on", "text"), mode="guarded"),
    "comment_reply": dict(sub=["comment", "reply"], fields=("text", "on", "at", "where"),
                          req=("text",), one=("on", "at"), mode="guarded"),
    "comment_delete": dict(sub=["comment", "delete"], fields=("on", "at", "where"),
                           one=("on", "at"), mode="guarded"),
    "insert": dict(sub=["insert"], fields=("after", "before", "text", "style", "in_section", "page"),
                   req=("text",), one=("after", "before"), mode="guarded"),
    "retag": dict(sub=["retag"], fields=("on", "style", "list", "in_section", "page"),
                  req=("on", "style"), mode="guarded"),
    "format": dict(sub=["format"], fields=("on", "bold", "italic", "plain", "in_section", "page"),
                   req=("on",), mode="guarded"),
    "delete_paragraph": dict(sub=["delete-paragraph"], fields=("on", "in_section", "page"),
                             req=("on",), mode="guarded"),
    "import": dict(sub=["import"], fields=("markdown", "after", "before", "replace_section",
                                           "in_section", "page"),
                   req=("markdown",), one=("after", "before", "replace_section"), mode="guarded"),
    "plan": dict(sub=["plan"], fields=("plan", "track"), req=("plan",), mode="plan"),
    "history": dict(sub=["history"], fields=(), mode="read"),
    "revert": dict(sub=["revert"], fields=("ref",), req=("ref",), mode="gate"),
    "config": dict(sub=["config"], fields=("vcs", "track"), mode="gate"),
}
# schema name -> (flag, kind)
EDIT_OPTIONS = {
    "on": ("--on", str), "text": ("--text", str), "at": ("--at", int), "after": ("--after", str),
    "before": ("--before", str), "style": ("--style", str), "list": ("--list", str),
    "bold": ("--bold", bool), "italic": ("--italic", bool), "plain": ("--plain", bool),
    "where": ("--where", str), "in_section": ("--in", str), "page": ("--page", str),
    "replace_section": ("--replace-section", str),
}


def build_edit(args, cfg):
    command = arg(args, "command", str, True)
    spec = EDIT_COMMANDS.get(command)
    if spec is None:
        raise ToolError(f"command must be one of: {', '.join(EDIT_COMMANDS)}")
    mode = spec["mode"]
    allowed = {"file", "command", *spec["fields"]}
    if mode in ("guarded", "plan", "gate"):
        allowed.add("write")
    if mode in ("guarded", "plan"):
        allowed.add("expect")
    check_keys(args, allowed)
    doc = resolve_document(arg(args, "file", str, True), cfg.roots)
    for key in spec.get("req", ()):
        if args.get(key) is None:
            raise ToolError(f"{key} is required for {command}")
    if "one" in spec and sum(args.get(k) is not None for k in spec["one"]) != 1:
        raise ToolError(f"give exactly one of: {', '.join(spec['one'])}")

    write = bool(arg(args, "write", bool))
    if mode == "gate":
        changing = command == "revert" or any(args.get(k) is not None for k in ("vcs", "track"))
        if changing and not write:
            raise ToolError(f"{command} applies at once: pass write=true to confirm")
        write = changing
    elif mode == "guarded":
        guard_write(args, write)

    argv = list(spec["sub"])
    tmp = None
    cleanup = noop
    tail = []
    try:
        if command == "plan":
            plan = args["plan"]
            expect = arg(args, "expect", str)
            if isinstance(plan, list):
                plan = {"edits": plan}
            if not isinstance(plan, dict) or not isinstance(plan.get("edits"), list):
                raise ToolError("plan must be a list of edits or {fingerprint, edits}")
            if expect:
                if plan.get("fingerprint") not in (None, expect):
                    raise ToolError("expect and plan.fingerprint disagree")
                plan = dict(plan, fingerprint=expect)
            if write and not plan.get("fingerprint"):
                raise ToolError("write=true needs expect (or plan.fingerprint)")
            tmp = tempfile.mkdtemp(prefix="pages-mcp-")
            plan_path = os.path.join(tmp, "plan.json")
            with open(plan_path, "w", encoding="utf-8") as fh:
                json.dump(plan, fh, ensure_ascii=False)
            tristate(argv, arg(args, "track", bool), "--track", "--no-track")
            tail = [plan_path]
        elif command == "import":
            md = arg(args, "markdown", str, True)
            tmp = tempfile.mkdtemp(prefix="pages-mcp-")
            md_path = os.path.join(tmp, "import.md")
            with open(md_path, "w", encoding="utf-8") as fh:
                fh.write(md)
            tail = [md_path]
        elif command == "revert":
            tail = [arg(args, "ref", str, True)]
        elif command == "config":
            for key, flag in (("vcs", "--vcs"), ("track", "--track")):
                value = arg(args, key, bool)
                if value is not None:
                    argv.append(f"{flag}={'on' if value else 'off'}")
        for key in spec["fields"]:
            if key in EDIT_OPTIONS and args.get(key) is not None:
                flag, kind = EDIT_OPTIONS[key]
                opt(argv, flag, arg(args, key, kind))
        if mode == "guarded":
            opt(argv, "--expect", arg(args, "expect", str))
        if mode in ("guarded", "plan"):
            opt(argv, "--write", write)
    except BaseException:
        if tmp:
            _rmtree(tmp)
        raise
    if tmp:
        cleanup = lambda: _rmtree(tmp)
    return "pages_edit.py", argv + ["--", *tail, doc], write, cleanup


def _rmtree(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


# --- running a script ----------------------------------------------------------------------

def run_script(script, argv):
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    try:
        proc = subprocess.run([sys.executable, os.path.join(HERE, script), *argv],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=TIMEOUT, env=env, cwd=HERE,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {TIMEOUT} seconds"
    return proc.returncode, proc.stdout, proc.stderr


def cap(text):
    if len(text) <= MAX_OUTPUT:
        return text
    return (text[:MAX_OUTPUT] + f"\n[truncated {len(text) - MAX_OUTPUT} more characters; "
            "narrow with in_section or page]")


def tool_result(text, error=False):
    return {"content": [{"type": "text", "text": cap(text)}], "isError": error}


def call_tool(name, args, cfg):
    builder = TOOLS[name]["build"]
    try:
        script, argv, mutating, cleanup = builder(args, cfg)
    except ToolError as exc:
        return tool_result(str(exc), True)
    try:
        if mutating:
            with WRITE_LOCK:
                code, out, err = run_script(script, argv)
        else:
            code, out, err = run_script(script, argv)
    finally:
        cleanup()
    if code != 0:
        return tool_result(err.strip() or out.strip() or f"exit code {code}", True)
    text = out
    if err.strip():
        text = f"{out}\n[stderr]\n{err.strip()}\n" if out else err.strip()
    return tool_result(text)


# --- tool definitions ----------------------------------------------------------------------

def _schema(props, required=()):
    return {"type": "object", "properties": props, "required": list(required),
            "additionalProperties": False}


FILE = {"type": "string", "description": "path to a .pages file inside an allowed folder"}
SECTION = {"type": "string", "description": "only this section (heading name; see format=outline)"}
PAGE = {"type": "string", "description": "page or range N-M (needs a page index, Mac only)"}
WHERE = {"type": "string", "description": "all (default), body, notes or noteN"}
EXPECT = {"type": "string", "description": "document fingerprint; required when write=true"}
WRITE = {"type": "boolean", "description": "apply the change; default false = dry run"}

TOOLS = {
    "pages_read": {
        "build": build_read, "write": False,
        "description": "Read an Apple Pages (.pages) document as Markdown (default), plain, "
                       "json, outline, comments, links or styles, or get its fingerprint. "
                       "Use in_section on long documents. Never writes.",
        "inputSchema": _schema({
            "file": FILE, "format": {"type": "string", "enum": list(READ_FORMATS)},
            "in_section": SECTION, "page": PAGE,
            "changes": {"type": "string", "enum": ["accept", "reject", "mark"]},
            "notes": {"type": "string", "enum": ["inline", "skip", "only"],
                      "description": "footnotes and margin notes"}}, ["file"])},
    "pages_find": {
        "build": build_find, "write": False,
        "description": "Find text in a .pages document: numbered matches with offsets and "
                       "context, on the accepted text (tracked changes applied).",
        "inputSchema": _schema({
            "file": FILE, "pattern": {"type": "string"}, "regex": {"type": "boolean"},
            "raw": {"type": "boolean", "description": "search the unaccepted text"},
            "where": WHERE, "in_section": SECTION, "page": PAGE}, ["file", "pattern"])},
    "pages_replace": {
        "build": build_replace, "write": True,
        "description": "Replace text in a .pages document. Dry run (shows the diff) unless "
                       "write=true; a write needs expect, the fingerprint from pages_read "
                       "format=fingerprint. Writes are not yet verified in real Pages: use a copy.",
        "inputSchema": _schema({
            "file": FILE, "find": {"type": "string"}, "replace": {"type": "string"},
            "all": {"type": "boolean", "description": "every occurrence"},
            "occurrence": {"type": "integer", "description": "only the Nth (default 1)"},
            "regex": {"type": "boolean"}, "where": WHERE, "in_section": SECTION, "page": PAGE,
            "track": {"type": "boolean", "description": "record as a Pages tracked change"},
            "expect": EXPECT, "write": WRITE}, ["file", "find", "replace"])},
    "pages_edit": {
        "build": build_edit, "write": True,
        "description": "Other edits to a .pages document, chosen by command. comment_add "
                       "(on,text), comment_reply (text, on|at), comment_delete (on|at), insert "
                       "(text, after|before, style), retag (on,style,list), format (on, bold/"
                       "italic/plain), delete_paragraph (on), import (markdown, after|before|"
                       "replace_section), plan (plan, track), history, revert (ref), config "
                       "(vcs,track). Dry run unless write=true; a write needs expect. "
                       "revert and config need write=true.",
        "inputSchema": _schema({
            "file": FILE, "command": {"type": "string", "enum": list(EDIT_COMMANDS)},
            "on": {"type": "string", "description": "text identifying the paragraph or anchor"},
            "at": {"type": "integer", "description": "comment anchor offset from format=comments"},
            "text": {"type": "string"}, "after": {"type": "string"}, "before": {"type": "string"},
            "style": {"type": "string", "description": "e.g. Heading 2, Body"},
            "list": {"type": "string", "description": "list style, None clears a bullet"},
            "bold": {"type": "boolean"}, "italic": {"type": "boolean"}, "plain": {"type": "boolean"},
            "markdown": {"type": "string", "description": "headings, paragraphs and bullets"},
            "replace_section": {"type": "string", "description": "heading to replace (max 3 paragraphs)"},
            "plan": {"description": "list of {find, replace, in, all, ...} or {fingerprint, edits}"},
            "ref": {"type": "string", "description": "history ref, e.g. HEAD~1"},
            "vcs": {"type": "boolean"}, "track": {"type": "boolean"},
            "where": WHERE, "in_section": SECTION, "page": PAGE,
            "expect": EXPECT, "write": WRITE}, ["file", "command"])},
}


def offered(cfg):
    return {n: t for n, t in TOOLS.items() if not (cfg.read_only and t["write"])}


# --- the protocol --------------------------------------------------------------------------

def _error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle(msg, cfg):
    """One JSON-RPC message -> the reply, or None for a notification."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _error(None, -32600, "invalid request")
    method = msg.get("method")
    if method is None:                        # a response to us; we never ask anything
        return None
    msg_id = msg.get("id")
    notification = "id" not in msg
    params = msg.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return None if notification else _error(msg_id, -32602, "params must be an object")

    def ok(result):
        return None if notification else {"jsonrpc": "2.0", "id": msg_id, "result": result}

    if method == "initialize":
        asked = params.get("protocolVersion")
        return ok({"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                   "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO,
                   "instructions": INSTRUCTIONS})
    if method.startswith("notifications/"):
        return None
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": [{"name": n, "description": t["description"],
                              "inputSchema": t["inputSchema"]}
                             for n, t in offered(cfg).items()]})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments")
        if args is None:
            args = {}
        if name not in offered(cfg):
            return None if notification else _error(msg_id, -32602, f"unknown tool: {name}")
        if not isinstance(args, dict):
            return None if notification else _error(msg_id, -32602, "arguments must be an object")
        try:
            return ok(call_tool(name, args, cfg))
        except Exception as exc:               # a bug here must not take the server down
            return ok(tool_result(f"internal error: {type(exc).__name__}: {exc}", True))
    return None if notification else _error(msg_id, -32601, f"method not found: {method}")


# --- stdio ---------------------------------------------------------------------------------

def serve_stdio(cfg):
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = _error(None, -32700, "parse error")
        else:
            reply = (_error(None, -32600, "batch requests are not supported")
                     if isinstance(msg, list) else handle(msg, cfg))
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()


# --- HTTP ----------------------------------------------------------------------------------

LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1")


def host_of(header):
    header = (header or "").strip().lower()
    if header.startswith("["):
        return header[:header.find("]") + 1] if "]" in header else header
    return header.rsplit(":", 1)[0] if header.count(":") == 1 else header


class Handler(BaseHTTPRequestHandler):
    server_version = "pages_mcp"
    sys_version = ""

    def log_message(self, fmt, *args):         # no access log: it could only hold noise
        pass

    def reply(self, status, body=b"", headers=()):
        self.send_response(status)
        for key, value in headers:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)
        self.close_connection = True

    def reply_json(self, status, payload):
        self.reply(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   [("Content-Type", "application/json")])

    def method_not_allowed(self):
        self.reply(405, b"", [("Allow", "POST")])

    do_GET = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = method_not_allowed

    def do_POST(self):
        srv = self.server
        if self.path.split("?", 1)[0] != "/mcp":
            return self.reply(404)
        if host_of(self.headers.get("Host")) not in srv.allowed_hosts:
            return self.reply_json(403, {"error": "host not allowed"})
        origin = self.headers.get("Origin")
        if origin is not None and origin.strip().lower() not in srv.allowed_origins:
            return self.reply_json(403, {"error": "origin not allowed"})
        scheme, _, presented = (self.headers.get("Authorization") or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
                presented.strip().encode("utf-8"), srv.token):
            return self.reply(401, b'{"error": "unauthorized"}',
                              [("WWW-Authenticate", "Bearer"),
                               ("Content-Type", "application/json")])
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self.reply_json(411, {"error": "Content-Length required"})
        if length > MAX_BODY:
            return self.reply_json(413, {"error": "request too large"})
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            msg = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self.reply_json(400, _error(None, -32700, "parse error"))
        if isinstance(msg, list):
            return self.reply_json(400, _error(None, -32600, "batch requests are not supported"))
        reply = handle(msg, srv.cfg)
        if reply is None:
            return self.reply(202)
        self.reply_json(200, reply)


def make_http_server(cfg, host, port, token, allowed_hosts=(), allowed_origins=()):
    if not token or len(token) < MIN_TOKEN:
        raise ValueError(f"the token must be at least {MIN_TOKEN} characters")
    if not cfg.roots:
        raise ValueError("no document folders are configured: pass --root or set PAGES_MCP_ROOTS")
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.cfg = cfg
    server.token = token.encode("utf-8")
    server.allowed_hosts = {h.lower() for h in (*LOCAL_HOSTS, *allowed_hosts)}
    server.allowed_origins = {o.strip().lower() for o in allowed_origins}
    return server


# --- command line --------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="pages_mcp",
        description="MCP server for pages2md / pages_edit (stdio by default, --http for HTTP).")
    ap.add_argument("--root", action="append", default=[], metavar="DIR",
                    help="folder whose .pages files may be used (repeatable; also "
                         "PAGES_MCP_ROOTS, separated by the OS path separator)")
    ap.add_argument("--read-only", action="store_true",
                    help="offer only pages_read and pages_find")
    ap.add_argument("--http", action="store_true",
                    help="serve POST /mcp over HTTP; needs PAGES_MCP_TOKEN in the environment")
    ap.add_argument("--host", default="127.0.0.1", help="HTTP bind address (default loopback)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                    help="extra Host header to accept, e.g. your tunnel's hostname")
    ap.add_argument("--allow-origin", action="append", default=[], metavar="ORIGIN",
                    help="browser origin to accept (default: none; MCP clients send no Origin)")
    args = ap.parse_args(argv)

    roots = []
    env_roots = os.environ.get("PAGES_MCP_ROOTS", "")
    for raw in [*args.root, *[r for r in env_roots.split(os.pathsep) if r]]:
        real = os.path.realpath(os.path.expanduser(raw))
        if not os.path.isdir(real):
            sys.exit(f"pages_mcp: {raw} is not a folder")
        if real not in roots:
            roots.append(real)
    cfg = Config(roots, args.read_only)

    if args.http:
        token = os.environ.get("PAGES_MCP_TOKEN", "")
        if not token:
            sys.exit("pages_mcp: set PAGES_MCP_TOKEN (at least %d characters) in the "
                     "environment; it is never taken from the command line" % MIN_TOKEN)
        try:
            server = make_http_server(cfg, args.host, args.port, token,
                                      args.allow_host, args.allow_origin)
        except ValueError as exc:
            sys.exit(f"pages_mcp: {exc}")
        if args.host not in ("127.0.0.1", "localhost", "::1"):
            print(f"pages_mcp: warning: listening on {args.host}, not only on this machine",
                  file=sys.stderr)
        print(f"pages_mcp: http://{args.host}:{server.server_address[1]}/mcp  "
              f"roots={roots}  {'read-only' if cfg.read_only else 'read+edit'}", file=sys.stderr)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return
    if not roots:
        print("pages_mcp: warning: no --root or PAGES_MCP_ROOTS; every call will be refused",
              file=sys.stderr)
    serve_stdio(cfg)


if __name__ == "__main__":
    main()
