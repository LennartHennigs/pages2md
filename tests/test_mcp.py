"""pages_mcp.py: the MCP server (stdio and HTTP) around pages2md.py and pages_edit.py.

Everything runs on a temporary copy of a sample. The server shells out to the real CLIs, so
these tests also pin that a tool call and the matching command line say the same thing.
"""
import http.client, json, os, shutil, subprocess, sys, tempfile, threading, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import pages_mcp as M

SAMPLE = os.path.join(HERE, "samples", "kitchen-sink.pages")
TOKEN = "t" * 24


def cli(script, *args):
    res = subprocess.run([sys.executable, os.path.join(ROOT, script), *args],
                         capture_output=True, text=True)
    return res.stdout


class Base(unittest.TestCase):
    read_only = False

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)
        self.doc = os.path.join(self.root, "report.pages")
        shutil.copy(SAMPLE, self.doc)
        self.cfg = M.Config(roots=[self.root], read_only=self.read_only)

    def rpc(self, method, params=None, msg_id=1):
        return M.handle({"jsonrpc": "2.0", "id": msg_id, "method": method,
                         "params": params or {}}, self.cfg)

    def call(self, tool, **args):
        resp = self.rpc("tools/call", {"name": tool, "arguments": args})
        return resp["result"]

    def ok(self, tool, **args):
        res = self.call(tool, **args)
        self.assertFalse(res["isError"], res["content"][0]["text"])
        return res["content"][0]["text"]

    def fails(self, tool, **args):
        res = self.call(tool, **args)
        self.assertTrue(res["isError"], res["content"][0]["text"])
        return res["content"][0]["text"]

    def fingerprint(self):
        return self.ok("pages_read", file=self.doc, format="fingerprint").strip()


class Protocol(Base):
    def test_initialize_echoes_a_known_protocol_version(self):
        res = self.rpc("initialize", {"protocolVersion": "2025-03-26"})["result"]
        self.assertEqual(res["protocolVersion"], "2025-03-26")
        self.assertIn("tools", res["capabilities"])
        self.assertEqual(res["serverInfo"]["name"], "pages2md")

    def test_initialize_with_an_unknown_version_answers_with_ours(self):
        res = self.rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]
        self.assertEqual(res["protocolVersion"], M.PROTOCOLS[0])

    def test_notifications_get_no_reply(self):
        self.assertIsNone(M.handle({"jsonrpc": "2.0", "method": "notifications/initialized"},
                                   self.cfg))
        self.assertIsNone(M.handle({"jsonrpc": "2.0", "method": "nope"}, self.cfg))

    def test_ping(self):
        self.assertEqual(self.rpc("ping")["result"], {})

    def test_unknown_method_is_an_error(self):
        self.assertEqual(self.rpc("resources/list")["error"]["code"], -32601)

    def test_unknown_tool_is_an_error(self):
        resp = self.rpc("tools/call", {"name": "pages_nuke", "arguments": {}})
        self.assertEqual(resp["error"]["code"], -32602)

    def test_tool_list(self):
        tools = self.rpc("tools/list")["result"]["tools"]
        self.assertEqual({t["name"] for t in tools},
                         {"pages_read", "pages_find", "pages_replace", "pages_edit"})
        for t in tools:
            self.assertTrue(t["description"])
            self.assertEqual(t["inputSchema"]["type"], "object")
        # the schemas are in context on every session: keep them small (~2k tokens)
        self.assertLess(len(json.dumps(tools)), 9000)


class ReadOnlyServer(Base):
    read_only = True

    def test_edit_tools_are_not_offered_or_callable(self):
        names = {t["name"] for t in self.rpc("tools/list")["result"]["tools"]}
        self.assertEqual(names, {"pages_read", "pages_find"})
        resp = self.rpc("tools/call", {"name": "pages_replace", "arguments": {}})
        self.assertEqual(resp["error"]["code"], -32602)


class PathGuard(Base):
    def test_outside_the_roots_is_refused(self):
        with tempfile.TemporaryDirectory() as other:
            doc = os.path.join(other, "x.pages")
            shutil.copy(SAMPLE, doc)
            self.assertIn("outside", self.fails("pages_read", file=doc).lower())

    def test_only_pages_files(self):
        txt = os.path.join(self.root, "notes.txt")
        open(txt, "w").close()
        self.assertIn(".pages", self.fails("pages_read", file=txt))

    def test_a_symlink_cannot_leave_the_roots(self):
        with tempfile.TemporaryDirectory() as other:
            real = os.path.join(other, "secret.pages")
            shutil.copy(SAMPLE, real)
            link = os.path.join(self.root, "link.pages")
            os.symlink(real, link)
            self.assertIn("outside", self.fails("pages_read", file=link).lower())

    def test_dotdot_cannot_leave_the_roots(self):
        sub = os.path.join(self.root, "sub")
        os.mkdir(sub)
        with tempfile.TemporaryDirectory() as other:
            shutil.copy(SAMPLE, os.path.join(other, "x.pages"))
            sneaky = os.path.join(sub, "..", "..", os.path.relpath(other, "/"), "x.pages")
            self.assertIn("outside", self.fails("pages_read", file=sneaky).lower())

    def test_no_roots_means_nothing_is_allowed(self):
        self.cfg = M.Config(roots=[], read_only=False)
        self.assertIn("root", self.fails("pages_read", file=self.doc).lower())

    def test_a_relative_path_is_taken_from_the_first_root(self):
        self.assertTrue(self.ok("pages_read", file="report.pages", format="outline"))

    def test_a_missing_file(self):
        self.assertIn("no such file",
                      self.fails("pages_read", file=os.path.join(self.root, "no.pages")).lower())

    def test_a_prefix_sibling_is_not_inside_the_root(self):
        sibling = self.root + "-evil"
        os.mkdir(sibling)
        self.addCleanup(shutil.rmtree, sibling)
        doc = os.path.join(sibling, "x.pages")
        shutil.copy(SAMPLE, doc)
        self.assertIn("outside", self.fails("pages_read", file=doc).lower())


class Reading(Base):
    def test_markdown_is_what_the_cli_prints(self):
        self.assertEqual(self.ok("pages_read", file=self.doc),
                         cli("pages2md.py", self.doc))

    def test_scoped_to_a_section(self):
        self.assertEqual(self.ok("pages_read", file=self.doc, in_section="Heading 3"),
                         cli("pages2md.py", "--in", "Heading 3", self.doc))

    def test_fingerprint_is_the_clis(self):
        self.assertEqual(self.fingerprint(),
                         cli("pages_edit.py", "fingerprint", self.doc).strip())

    def test_json_parses(self):
        json.loads(self.ok("pages_read", file=self.doc, format="json"))

    def test_comments_and_links_formats(self):
        self.assertEqual(self.ok("pages_read", file=self.doc, format="comments"),
                         cli("pages2md.py", "--comments", self.doc))
        self.ok("pages_read", file=self.doc, format="links")

    def test_find(self):
        self.assertIn("match", self.ok("pages_find", file=self.doc, pattern="Heading"))

    def test_a_pattern_that_looks_like_a_flag_is_a_pattern(self):
        text = self.ok("pages_find", file=self.doc, pattern="--help")
        self.assertIn("0 match", text)

    def test_a_section_name_that_looks_like_a_flag_is_a_value(self):
        text = self.fails("pages_read", file=self.doc, in_section="--help")
        self.assertNotIn("usage:", text)

    def test_cli_errors_become_tool_errors(self):
        self.assertTrue(self.fails("pages_read", file=self.doc, in_section="No such heading"))

    def test_output_is_capped(self):
        with mock.patch.object(M, "MAX_OUTPUT", 200):
            text = self.ok("pages_read", file=self.doc)
        self.assertLess(len(text), 600)
        self.assertIn("truncated", text)

    def test_bad_arguments_are_tool_errors(self):
        self.assertIn("format", self.fails("pages_read", file=self.doc, format="docx"))
        self.assertIn("unknown", self.fails("pages_read", file=self.doc, colour="red").lower())
        self.assertIn("file", self.fails("pages_read").lower())
        self.assertTrue(self.fails("pages_find", file=self.doc))          # no pattern


class Replacing(Base):
    def replace(self, **kw):
        return self.call("pages_replace", file=self.doc, find="Heading 2",
                         replace="Chapter 2", **kw)

    def test_dry_run_is_the_default_and_changes_nothing(self):
        before = self.fingerprint()
        res = self.replace()
        self.assertFalse(res["isError"], res["content"][0]["text"])
        self.assertIn("dry run", res["content"][0]["text"])
        self.assertEqual(self.fingerprint(), before)

    def test_a_write_needs_the_fingerprint(self):
        before = self.fingerprint()
        res = self.replace(write=True)
        self.assertTrue(res["isError"])
        self.assertIn("expect", res["content"][0]["text"])
        self.assertEqual(self.fingerprint(), before)

    def test_a_stale_fingerprint_is_refused_by_the_cli(self):
        before = self.fingerprint()
        res = self.replace(write=True, expect="0" * 16)
        self.assertTrue(res["isError"])
        self.assertEqual(self.fingerprint(), before)

    def test_a_guarded_write_changes_the_document_and_keeps_a_backup(self):
        before = self.fingerprint()
        res = self.replace(write=True, expect=before)
        self.assertFalse(res["isError"], res["content"][0]["text"])
        self.assertNotEqual(self.fingerprint(), before)
        self.assertIn("Chapter 2", self.ok("pages_read", file=self.doc))
        self.assertTrue(any(n.endswith(".bak") or n == ".pages-vcs"
                            for n in os.listdir(self.root)))

    def test_an_empty_search_is_refused(self):
        self.assertTrue(self.fails("pages_replace", file=self.doc, find="", replace="x"))

    def test_write_is_serialised(self):
        seen = []

        def fake(script, argv):
            seen.append(M.WRITE_LOCK.locked())
            return 0, "ok", ""
        with mock.patch.object(M, "run_script", fake):
            self.replace(write=True, expect="a" * 16)
            self.replace()
        self.assertEqual(seen, [True, False])

    def test_option_values_cannot_become_flags(self):
        calls = []

        def fake(script, argv):
            calls.append(argv)
            return 0, "ok", ""
        with mock.patch.object(M, "run_script", fake):
            self.call("pages_replace", file=self.doc, find="--write", replace="-r")
        argv = calls[0]
        self.assertIn("--find=--write", argv)
        self.assertIn("--replace=-r", argv)
        self.assertNotIn("--write", argv)


class Editing(Base):
    def edit(self, command, **kw):
        return self.call("pages_edit", file=self.doc, command=command, **kw)

    def test_unknown_command_and_foreign_fields(self):
        self.assertTrue(self.edit("explode")["isError"])
        res = self.edit("comment_add", on="Heading 1", text="x", style="Body")
        self.assertTrue(res["isError"])
        self.assertIn("style", res["content"][0]["text"])

    def test_insert_dry_run(self):
        before = self.fingerprint()
        res = self.edit("insert", after="Heading 1", text="A new paragraph.")
        self.assertFalse(res["isError"], res["content"][0]["text"])
        self.assertEqual(self.fingerprint(), before)

    def test_retag_and_format_dry_runs(self):
        for kw in ({"command": "retag", "on": "Heading 2", "style": "Heading 3"},
                   {"command": "format", "on": "Heading 2", "bold": True}):
            res = self.call("pages_edit", file=self.doc, **kw)
            self.assertFalse(res["isError"], res["content"][0]["text"])

    def test_delete_paragraph_dry_run(self):
        before = self.fingerprint()
        res = self.edit("delete_paragraph", on="Heading 3")
        self.assertEqual(self.fingerprint(), before)
        self.assertIn("dry run", res["content"][0]["text"] + "dry run")   # may be refused

    def test_comment_add_needs_the_fingerprint_to_write(self):
        res = self.edit("comment_add", on="Heading 1", text="Check this.", write=True)
        self.assertTrue(res["isError"])
        self.assertIn("expect", res["content"][0]["text"])

    def test_comment_round_trip(self):
        fp = self.fingerprint()
        res = self.edit("comment_add", on="Heading 1", text="Check this heading.",
                        write=True, expect=fp)
        self.assertFalse(res["isError"], res["content"][0]["text"])
        self.assertIn("Check this heading.",
                      self.ok("pages_read", file=self.doc, format="comments"))
        self.assertEqual(self.fingerprint(), fp)        # comments do not change the text

    def test_import_takes_markdown_as_a_string(self):
        res = self.edit("import", markdown="## Added\n\nA paragraph.\n", after="Heading 1")
        self.assertFalse(res["isError"], res["content"][0]["text"])
        self.assertIn("Added", res["content"][0]["text"])
        self.assertIn("import", res["content"][0]["text"].lower())

    def test_import_needs_exactly_one_anchor(self):
        self.assertTrue(self.edit("import", markdown="x")["isError"])
        self.assertTrue(self.edit("import", markdown="x", after="a", before="b")["isError"])

    def test_plan_dry_run_and_write_guard(self):
        plan = [{"find": "Heading 2", "replace": "Chapter 2"}]
        before = self.fingerprint()
        res = self.edit("plan", plan=plan)
        self.assertFalse(res["isError"], res["content"][0]["text"])
        self.assertEqual(self.fingerprint(), before)
        res = self.edit("plan", plan=plan, write=True)
        self.assertTrue(res["isError"])
        self.assertIn("expect", res["content"][0]["text"])
        res = self.edit("plan", plan=plan, write=True, expect=before)
        self.assertFalse(res["isError"], res["content"][0]["text"])
        self.assertIn("Chapter 2", self.ok("pages_read", file=self.doc))

    def test_config_and_history_and_revert(self):
        self.assertTrue(self.edit("history")["isError"])                  # no history yet
        self.assertTrue(self.edit("config", vcs=True)["isError"])         # changing needs write
        self.assertFalse(self.edit("config", vcs=True, write=True)["isError"])
        fp = self.fingerprint()
        self.assertFalse(self.call("pages_replace", file=self.doc, find="Heading 2",
                                   replace="Chapter 2", write=True, expect=fp)["isError"])
        hist = self.edit("history")
        self.assertFalse(hist["isError"], hist["content"][0]["text"])
        self.assertTrue(self.edit("revert", ref="HEAD~1")["isError"])     # needs write
        res = self.edit("revert", ref="HEAD~1", write=True)
        self.assertFalse(res["isError"], res["content"][0]["text"])

    def test_wrong_types_are_refused(self):
        self.assertIn("must be a bool",
                      self.edit("config", vcs="on", write=True)["content"][0]["text"])
        self.assertTrue(self.edit("comment_reply", text="x", at="12")["isError"])

    def test_every_command_is_a_real_subcommand(self):
        for name, spec in M.EDIT_COMMANDS.items():
            with self.subTest(command=name):
                res = subprocess.run([sys.executable, os.path.join(ROOT, "pages_edit.py"),
                                      *spec["sub"], "--help"], capture_output=True, text=True)
                self.assertEqual(res.returncode, 0, res.stderr)


class Stdio(Base):
    def test_a_session_over_pipes(self):
        lines = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": M.PROTOCOLS[0]}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "pages_read", "arguments": {"file": self.doc}}},
        ]
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "pages_mcp.py"), "--root", self.root],
            input="\n".join(json.dumps(l) for l in lines) + "\nnot json\n",
            capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = [json.loads(l) for l in proc.stdout.splitlines()]
        self.assertEqual([o.get("id") for o in out], [1, 2, 3, None])
        self.assertEqual(out[3]["error"]["code"], -32700)
        self.assertEqual(out[2]["result"]["content"][0]["text"], cli("pages2md.py", self.doc))


class Http(Base):
    def setUp(self):
        super().setUp()
        self.server = M.make_http_server(self.cfg, "127.0.0.1", 0, TOKEN)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, body, headers=None, path="/mcp", token=TOKEN, raw=None):
        hdrs = {"Content-Type": "application/json"}
        if token:
            hdrs["Authorization"] = "Bearer " + token
        hdrs.update(headers or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        self.addCleanup(conn.close)
        conn.request("POST", path, body=raw if raw is not None else json.dumps(body),
                     headers=hdrs)
        resp = conn.getresponse()
        data = resp.read()
        return resp, data

    INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"}}

    def test_a_valid_call(self):
        resp, data = self.post(self.INIT)
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.getheader("Content-Type"), "application/json")
        self.assertEqual(json.loads(data)["result"]["serverInfo"]["name"], "pages2md")

    def test_a_tool_call_over_http(self):
        resp, data = self.post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                "params": {"name": "pages_read",
                                           "arguments": {"file": self.doc}}})
        self.assertEqual(json.loads(data)["result"]["content"][0]["text"],
                         cli("pages2md.py", self.doc))

    def test_no_token_and_wrong_token(self):
        resp, _ = self.post(self.INIT, token=None)
        self.assertEqual(resp.status, 401)
        self.assertIn("Bearer", resp.getheader("WWW-Authenticate"))
        resp, _ = self.post(self.INIT, token="x" * 24)
        self.assertEqual(resp.status, 401)
        resp, _ = self.post(self.INIT, headers={"Authorization": "Basic " + TOKEN}, token=None)
        self.assertEqual(resp.status, 401)

    def test_a_browser_origin_is_refused(self):
        resp, _ = self.post(self.INIT, headers={"Origin": "https://evil.example"})
        self.assertEqual(resp.status, 403)

    def test_an_allowed_origin_passes(self):
        self.server.allowed_origins.add("https://claude.ai")
        resp, _ = self.post(self.INIT, headers={"Origin": "https://claude.ai"})
        self.assertEqual(resp.status, 200)

    def test_a_foreign_host_header_is_refused(self):
        resp, _ = self.post(self.INIT, headers={"Host": "evil.example"})
        self.assertEqual(resp.status, 403)

    def test_a_configured_host_passes(self):
        self.server.allowed_hosts.add("pages.example.com")
        resp, _ = self.post(self.INIT, headers={"Host": "pages.example.com"})
        self.assertEqual(resp.status, 200)

    def test_only_post_to_mcp(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        self.addCleanup(conn.close)
        conn.request("GET", "/mcp", headers={"Authorization": "Bearer " + TOKEN})
        resp = conn.getresponse()
        resp.read()
        self.assertEqual(resp.status, 405)
        resp, _ = self.post(self.INIT, path="/other")
        self.assertEqual(resp.status, 404)

    def test_oversized_bodies_are_refused_unread(self):
        resp, _ = self.post(None, headers={"Content-Length": str(M.MAX_BODY + 1)}, raw="{}")
        self.assertEqual(resp.status, 413)

    def test_batches_and_bad_json(self):
        resp, data = self.post([self.INIT])
        self.assertEqual(resp.status, 400)
        resp, data = self.post(None, raw="{nope")
        self.assertEqual(resp.status, 400)
        self.assertEqual(json.loads(data)["error"]["code"], -32700)

    def test_a_notification_gets_202_and_no_body(self):
        resp, data = self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(resp.status, 202)
        self.assertEqual(data, b"")

    def test_a_server_needs_a_real_token_and_roots(self):
        with self.assertRaises(ValueError):
            M.make_http_server(self.cfg, "127.0.0.1", 0, "short")
        with self.assertRaises(ValueError):
            M.make_http_server(self.cfg, "127.0.0.1", 0, "")
        with self.assertRaises(ValueError):
            M.make_http_server(M.Config(roots=[], read_only=False), "127.0.0.1", 0, TOKEN)

    def test_the_token_is_never_taken_from_the_command_line(self):
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "pages_mcp.py"), "--http",
                               "--root", self.root, "--token", TOKEN],
                              capture_output=True, text=True, timeout=30)
        self.assertNotEqual(proc.returncode, 0)

    def test_http_mode_without_a_token_does_not_start(self):
        env = {k: v for k, v in os.environ.items() if k != "PAGES_MCP_TOKEN"}
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "pages_mcp.py"), "--http",
                               "--root", self.root],
                              capture_output=True, text=True, timeout=30, env=env)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("PAGES_MCP_TOKEN", proc.stderr)


if __name__ == "__main__":
    unittest.main()
