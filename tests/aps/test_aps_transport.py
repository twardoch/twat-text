# this_file: tests/aps/test_aps_transport.py
"""Local HTTP fixtures verify single submissions, deadlines and durable evidence."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from twat_text.aps import books as aps_books
from twat_text.aps import quality as aps_quality
from twat_text.aps import semantics as aps_semantics
from twat_text.aps import transport as transport


@contextmanager
def endpoint(status=200, content="Target.", delay=0, trickle=False, body=None, headers=None):
    requests = []
    data = (
        body
        if body is not None
        else json.dumps(
            {
                "id": "completion-1",
                "model": "served-model",
                "usage": {"total_tokens": 12},
                "choices": [{"finish_reason": "stop", "message": {"content": content}}],
            }
        )
    )

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            time.sleep(delay)
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("x-request-id", "request-1")
                self.send_header("Location", "/redirected")
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                if trickle:
                    for byte in data.encode():
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.03)
                else:
                    self.wfile.write(data.encode())
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02})
    thread.start()
    try:
        with patch.object(
            transport,
            "settings",
            return_value=(f"http://127.0.0.1:{server.server_port}/v1", "fixture-key"),
        ):
            yield requests
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


class TransportTests(unittest.TestCase):
    def test_429_retains_private_diagnostics_without_credentials_or_cookie_headers(
        self,
    ):
        body = '{"error":{"code":"rate_limited","message":"fixture-key quota exceeded"}}'
        with endpoint(
            status=429,
            body=body,
            headers={
                "Retry-After": "61",
                "Set-Cookie": "private-cookie",
                "x-request-id": "fixture-key-id",
            },
        ) as requests:
            with self.assertRaises(ValueError) as caught:
                transport.request("Target", "freellm:nvidia:model", "prompt")
        metadata = caught.exception.metadata
        self.assertEqual(len(requests), 1)
        self.assertEqual(metadata["response_headers"]["retry-after"], "61")
        self.assertIn("date", metadata["response_headers"])
        self.assertNotIn("set-cookie", metadata["response_headers"])
        self.assertNotIn("fixture-key", json.dumps(metadata))
        self.assertIn("[REDACTED]-id", metadata["request_id"])
        self.assertIn("[REDACTED]-id", metadata["response_headers"]["x-request-id"])
        self.assertIn("[REDACTED] quota exceeded", metadata["error_response"])
        self.assertFalse(hasattr(caught.exception, "raw"))

    def test_success_preserves_provider_pin_raw_json_and_metadata(self):
        raw = '{"concepts":[],"empty_reason":"None"}'
        with endpoint(content=raw) as requests:
            result = aps_semantics.infer({"aps": "Target."}, "freellm:nvidia:z-ai/glm-5.3-flash", "prompt")
        self.assertEqual(result, raw)
        self.assertEqual(json.loads(result)["concepts"], [])
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["model"], "nvidia:z-ai/glm-5.3-flash")
        self.assertEqual(json.loads(requests[0]["messages"][1]["content"]), {"aps": "Target."})
        headers = result.metadata["response_headers"]
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(headers["x-request-id"], "request-1")
        self.assertTrue(headers["date"])
        self.assertEqual(
            result.metadata,
            {
                "response_headers": headers,
                "http_status": 200,
                "finish_reason": "stop",
                "request_id": "request-1",
                "id": "completion-1",
                "model": "served-model",
                "usage": {"total_tokens": 12},
            },
        )

    def test_unorouter_free_pin_reaches_both_callers_without_fallbacks(self):
        model = "unorouter:glm-5.3-flash:free"
        for route in (model, "freellm:" + model):
            with self.subTest(route=route), endpoint(content='{"ok":true}') as requests:
                self.assertEqual(
                    aps_books.checked_routes([route, "a"], None),
                    ["freellm:" + model, "freellm:a"],
                )
                self.assertEqual(aps_books.infer("Target.", route, "prompt"), '{"ok":true}')
                self.assertEqual(
                    json.loads(aps_semantics.infer({"aps": "Target."}, route, "prompt")),
                    {"ok": True},
                )
                with self.assertRaisesRegex(RuntimeError, "non-BYOK enforcement"):
                    aps_quality.infer({"source": "Target."}, route, "prompt")
                self.assertEqual(len(requests), 2)
                for request in requests:
                    self.assertEqual(request["model"], model)
                    self.assertEqual(
                        set(request),
                        {"model", "messages", "max_tokens", "temperature", "top_p"},
                    )

    def test_unorouter_unpinned_paid_automatic_and_malformed_routes_never_submit(self):
        invalid = (
            "unorouter:glm-5.3-flash",
            "unorouter:glm-5.3-flash:paid",
            "unorouter::free",
            "unorouter:auto:free",
            "unorouter:AUTO:free",
            "unorouter:default:free",
            "unorouter:fusion:free",
            "unorouter:random:free",
            "unorouter:auto-model:free",
            "unorouter:glm-5.3-flash:free:free",
            "unorouter:unorouter:glm-5.3-flash:free",
            "unorouter:provider/model:free",
            "unorouter:glm-5.3-flash :free",
            "unorouter:glm-5.3-flash?fallback=paid:free",
            "unorouter:glm-5.3-flash:free/paid",
            "glm-5.3-flash:free",
        )
        with patch.object(transport, "settings") as settings:
            for model in invalid:
                for route in (model, "freellm:" + model):
                    with self.subTest(route=route):
                        with self.assertRaises(ValueError):
                            aps_books.checked_routes([route, "a"], None)
                        with self.assertRaises(ValueError):
                            transport.request("Target.", route, "prompt")
            settings.assert_not_called()
        route = "unorouter:glm-5.3-flash:free"
        with self.assertRaisesRegex(ValueError, "distinct"):
            aps_books.checked_routes([route, "freellm:" + route], None)

    def test_openrouter_free_pin_reaches_both_callers_without_fallbacks(self):
        model = "openrouter:google/gemma-4-26b-a4b-it:free"
        for route in (model, "freellm:" + model):
            with self.subTest(route=route), endpoint(content='{"ok":true}') as requests:
                self.assertEqual(
                    aps_books.checked_routes([route, "a"], None),
                    ["freellm:" + model, "freellm:a"],
                )
                self.assertEqual(aps_books.infer("Target.", route, "prompt"), '{"ok":true}')
                self.assertEqual(
                    json.loads(aps_semantics.infer({"aps": "Target."}, route, "prompt")),
                    {"ok": True},
                )
                self.assertEqual(len(requests), 2)
                for request in requests:
                    self.assertEqual(request["model"], model)
                    self.assertNotIn("models", request)
                    self.assertEqual(
                        set(request),
                        {"model", "messages", "max_tokens", "temperature", "top_p"},
                    )

    def test_openrouter_paid_automatic_and_malformed_routes_fail_before_submission(
        self,
    ):
        invalid = (
            "openrouter:google/gemma",
            "openrouter:google/gemma:paid",
            "openrouter::free",
            "openrouter:auto:free",
            "openrouter:openrouter/auto:free",
            "openrouter:google/gemma:free:free",
            "openrouter:nvidia:google/gemma:free",
            "openrouter:google//gemma:free",
            "openrouter:google/gemma :free",
            "openrouter:google/gemma?paid:free",
            "openrouter:google/gemma:free/paid",
        )
        with patch.object(transport, "settings") as settings:
            for model in invalid:
                for route in (model, "freellm:" + model):
                    with self.subTest(route=route):
                        with self.assertRaises(ValueError):
                            aps_books.checked_routes([route, "a"], None)
                        with self.assertRaises(ValueError):
                            transport.request("Target.", route, "prompt")
            settings.assert_not_called()
        route = "openrouter:liquid/lfm-2.5-2.6b:free"
        with self.assertRaisesRegex(ValueError, "distinct"):
            aps_books.checked_routes([route, "freellm:" + route], None)

    def test_server_errors_and_redirects_never_retry(self):
        for status in (500, 502, 503, 307):
            with self.subTest(status=status), endpoint(status=status) as requests:
                with self.assertRaises(RuntimeError) as caught:
                    transport.request("Target.", "freellm:a", "prompt")
                self.assertEqual(caught.exception.metadata["http_status"], status)
                self.assertEqual(len(requests), 1)

    def test_explicit_rejections_keep_retryable_semantics_without_body_json(self):
        for status in (400, 401, 403, 404, 422, 429):
            with (
                self.subTest(status=status),
                endpoint(status=status, body="rejected") as requests,
            ):
                with self.assertRaises(ValueError) as caught:
                    transport.request("Target.", "a", "prompt")
                self.assertEqual(caught.exception.metadata["request_id"], "request-1")
                self.assertEqual(len(requests), 1)

    def test_deadline_kills_worker_once_for_stall_and_trickle(self):
        for trickle in (False, True):
            workers = []
            popen = subprocess.Popen

            def start(*args, **kwargs):
                process = popen(*args, **kwargs)
                workers.append(process)
                return process

            with (
                self.subTest(trickle=trickle),
                endpoint(delay=0 if trickle else 0.6, trickle=trickle) as requests,
                patch.object(transport.subprocess, "Popen", side_effect=start),
            ):
                started = time.monotonic()
                with self.assertRaises(TimeoutError):
                    transport.request("Target.", "a", "prompt", timeout=0.3)
                self.assertLess(time.monotonic() - started, 1.0)
                self.assertEqual(len(requests), 1)
                self.assertEqual(len(workers), 1)
                self.assertIsNotNone(workers[0].poll(), "Timed-out worker must be reaped")

    def test_malformed_envelopes_are_unknown_and_keep_request_id(self):
        for body in ("not JSON", "{}", "[]", '{"choices":[]}'):
            with self.subTest(body=body), endpoint(body=body) as requests:
                with self.assertRaises(RuntimeError) as caught:
                    transport.request("Target.", "a", "prompt")
                self.assertEqual(caught.exception.metadata["request_id"], "request-1")
                self.assertEqual(len(requests), 1)

    def test_malformed200_retains_bounded_sanitized_body_and_available_content(self):
        escaped = "".join("\\u" + f"{ord(c):04x}" for c in "fixture-key")
        bodies = [
            "<html>fixture-key upstream failure</html>",
            '{"error": "' + escaped + '"}',
            '{"choices": [{"message": {"content": "Available text."}}]}',
            '{"choices": [{"message": {"content": 123}, "finish_reason": "stop"}]}',
        ]
        for body in bodies:
            with self.subTest(body=body), endpoint(body=body) as requests:
                with self.assertRaises(RuntimeError) as caught:
                    transport.request("Target.", "a", "prompt")
                error = caught.exception
                diagnostic = error.metadata["malformed_response"]
                expected = body.replace("fixture-key", "[REDACTED]").replace(escaped, "[REDACTED]")
                self.assertEqual(diagnostic["text"], expected)
                self.assertEqual(
                    diagnostic["sanitized_sha256"],
                    hashlib.sha256(expected.encode()).hexdigest(),
                )
                self.assertFalse(diagnostic["truncated"])
                self.assertNotIn("fixture-key", json.dumps(error.metadata))
                self.assertNotIn(escaped, diagnostic["text"])
                self.assertEqual(
                    getattr(error, "raw", None),
                    "Available text." if "Available text." in body else None,
                )
                self.assertEqual(len(requests), 1)

    def test_available_malformed_content_and_metadata_redact_configured_credential(
        self,
    ):
        body = json.dumps(
            {
                "id": "fixture-key",
                "usage": {"private": "fixture-key"},
                "choices": [{"message": {"content": "fixture-key available text."}}],
            }
        )
        with endpoint(body=body) as requests:
            with self.assertRaises(RuntimeError) as caught:
                transport.request("Target.", "a", "prompt")
        error = caught.exception
        self.assertEqual(error.raw, "[REDACTED] available text.")
        self.assertTrue(error.metadata["completion_credential_redacted"])
        self.assertNotIn("fixture-key", json.dumps(error.metadata))
        self.assertEqual(error.metadata["id"], "[REDACTED]")
        self.assertEqual(len(requests), 1)

    def test_malformed200_bounds_utf8_prefix_after_redaction_and_hashes_full_body(self):
        body = "fixture-key" + ("é" * transport.DIAGNOSTIC_BYTES) + "fixture-key"
        with endpoint(body=body) as requests:
            with self.assertRaises(RuntimeError) as caught:
                transport.request("Target.", "a", "prompt")
        diagnostic = caught.exception.metadata["malformed_response"]
        sanitized = body.replace("fixture-key", "[REDACTED]").encode()
        self.assertLessEqual(len(diagnostic["text"].encode()), transport.DIAGNOSTIC_BYTES)
        self.assertTrue(diagnostic["truncated"])
        self.assertTrue(diagnostic["credential_redacted"])
        self.assertEqual(diagnostic["sanitized_bytes"], len(sanitized))
        self.assertEqual(diagnostic["sanitized_sha256"], hashlib.sha256(sanitized).hexdigest())
        self.assertEqual(len(requests), 1)

    def test_diagnostic_redaction_handles_mixed_escapes_and_prefix_boundary(self):
        key = "Ab/cé"
        forms = [
            key,
            json.dumps(key)[1:-1],
            json.dumps(key)[1:-1].replace("/", "\\/"),
            "A\\u0062/\\u0063é",
            "".join("\\u" + f"{ord(c):04X}" for c in key),
        ]
        for form in forms:
            with self.subTest(form=form):
                self.assertEqual(
                    transport.sanitize({"value": [form]}, key),
                    {"value": ["[REDACTED]"]},
                )
        body = "x" * (transport.DIAGNOSTIC_BYTES - 4) + key
        diagnostic = transport.malformed_diagnostic(body, key)
        self.assertTrue(diagnostic["credential_redacted"])
        self.assertNotIn("Ab/", diagnostic["text"])
        self.assertLessEqual(len(diagnostic["text"].encode()), transport.DIAGNOSTIC_BYTES)
        self.assertEqual(transport.sanitize("ab/cé", key), "ab/cé")

    def test_redaction_long_near_matches_finish_in_bounded_subprocess(self):
        key = "a" * 40 + "b"
        values = [
            "a" * 40,
            "\\ " + ("a" * 40 + "c") * 1000,
            ("\\u0061" * 40 + "c") * 1000,
        ]
        script = (
            "import json,sys; "
            "from twat_text.aps import transport as t; key,values=json.load(sys.stdin); "
            "assert all(t.sanitize(value,key)==value for value in values); print('ok')"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            input=json.dumps([key, values]),
            text=True,
            capture_output=True,
            timeout=5,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), "ok")

    def test_redaction_preserves_ambiguous_literal_backslash_and_unicode_matches(self):
        cases = [
            (r"\u0061b", r"\u0061\u0062"),
            (r"\ab", r"\\a\u0062"),
            ("a😀b", r"a\uD83D\uDE00b"),
            ("a\nb", r"a\n\u0062"),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                self.assertEqual(transport.sanitize(value, key), "[REDACTED]")
                self.assertEqual(
                    transport.sanitize(value + "/" + value, key),
                    "[REDACTED]/[REDACTED]",
                )

    def test_malformed200_diagnostic_survives_books_and_semantic_reopen(self):
        body = '{"error":{"message":"fixture-key private failure"}}'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for semantic in (False, True):
                with self.subTest(semantic=semantic):
                    folder = root / str(semantic)
                    connect = aps_semantics.connect if semantic else aps_books.connect
                    db = connect(folder)
                    try:
                        if not semantic:
                            source = root / "source" / "book"
                            source.mkdir(parents=True)
                            (source / "1.md").write_text("Target.")
                            aps_books.prepare(
                                db,
                                [root / "source"],
                                "prompt",
                                3000,
                                list,
                                lambda text, limit: [text],
                            )
                        with endpoint(body=body) as requests:
                            for reopen in (False, True):
                                if reopen:
                                    db.close()
                                    db = connect(folder)
                                if semantic:
                                    with self.assertRaises(ValueError):
                                        aps_semantics.job(
                                            db,
                                            "fixture",
                                            {},
                                            "prompt",
                                            ["a", "b"],
                                            lambda result, payload: result,
                                            aps_semantics.infer,
                                        )
                                else:
                                    aps_books.run(db, ["a", "b"], "prompt")
                            self.assertEqual(len(requests), 1)
                        metadata = json.loads(db.execute("SELECT metadata FROM attempt_metadata").fetchone()[0])
                        self.assertIn(
                            "[REDACTED] private failure",
                            metadata["malformed_response"]["text"],
                        )
                        self.assertEqual(
                            db.execute("SELECT count(*) FROM attempt_responses").fetchone()[0],
                            0,
                        )
                        if not semantic:
                            self.assertEqual(
                                db.execute("SELECT state FROM chunks").fetchone()[0],
                                "unknown",
                            )
                            self.assertEqual(aps_books.publish(db, root / "output"), [])
                            self.assertEqual(list((root / "output").glob("*.md")), [])
                    finally:
                        db.close()

    def test_poe_disabled_before_any_submission(self):
        with patch.object(transport, "settings") as settings:
            with self.assertRaisesRegex(ValueError, "disabled"):
                transport.request("Target.", "poe:Kimi-K3.1-Pro-T", "prompt")
            settings.assert_not_called()
        with tempfile.TemporaryDirectory() as temp:
            db = aps_books.connect(Path(temp))
            try:
                evidence = {
                    "accept_historical_cost_risk": True,
                    "models": {
                        "Kimi-K3.1-Pro-T": {
                            "observed_points": 0,
                            "observed_at": "date",
                            "source": "fixture",
                            "prompt": "Reply with exactly OK",
                        }
                    },
                }
                with self.assertRaisesRegex(ValueError, "disabled"):
                    aps_books.run(
                        db,
                        ["poe:Kimi-K3.1-Pro-T", "a"],
                        "prompt",
                        poe_evidence=evidence,
                    )
                self.assertEqual(db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 0)
            finally:
                db.close()

    def test_both_runners_retain_metadata_and_do_not_resubmit_unknown(self):
        for status in (200, 503):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                books = aps_books.connect(root / "books")
                semantic = aps_semantics.connect(root / "semantic")
                try:
                    with books:
                        books.execute(
                            "INSERT INTO chunks(book,ordinal,source,original,state) VALUES('book',0,'source','Target.','pending')"
                        )
                    with endpoint(status=status) as requests:
                        aps_books.run(books, ["a", "b"], "prompt")
                        aps_books.run(books, ["a", "b"], "prompt")
                        self.assertEqual(len(requests), 1)
                    metadata = json.loads(books.execute("SELECT metadata FROM attempt_metadata").fetchone()[0])
                    self.assertEqual(metadata["id"], "completion-1")
                    self.assertEqual(
                        books.execute("SELECT state FROM chunks").fetchone()[0],
                        "done" if status == 200 else "unknown",
                    )
                    with endpoint(status=status, content='{"ok":true}') as requests:
                        if status == 200:
                            self.assertEqual(
                                aps_semantics.job(
                                    semantic,
                                    "fixture",
                                    {},
                                    "prompt",
                                    ["a", "b"],
                                    lambda result, payload: result,
                                    aps_semantics.infer,
                                ),
                                {"ok": True},
                            )
                        else:
                            with self.assertRaisesRegex(ValueError, "Unaccepted"):
                                aps_semantics.job(
                                    semantic,
                                    "fixture",
                                    {},
                                    "prompt",
                                    ["a", "b"],
                                    lambda result, payload: result,
                                    aps_semantics.infer,
                                )
                            with self.assertRaisesRegex(ValueError, "Unresolved"):
                                aps_semantics.job(
                                    semantic,
                                    "fixture",
                                    {},
                                    "prompt",
                                    ["a", "b"],
                                    Mock(),
                                    aps_semantics.infer,
                                )
                        self.assertEqual(len(requests), 1)
                    metadata = json.loads(semantic.execute("SELECT metadata FROM attempt_metadata").fetchone()[0])
                    self.assertEqual(metadata["usage"], {"total_tokens": 12})
                finally:
                    books.close()
                    semantic.close()

    def test_malformed_fields_leave_one_unknown_attempt_without_resubmission(self):
        cases = [(content, "stop") for content in (123, None, {}, [], True)]
        cases += [("Target.", reason) for reason in (None, 123, "unexpected", [], {})]
        for content, reason in cases:
            with (
                self.subTest(content=content, reason=reason),
                tempfile.TemporaryDirectory() as temp,
            ):
                db = aps_books.connect(Path(temp))
                try:
                    with db:
                        db.execute(
                            "INSERT INTO chunks(book,ordinal,source,original,state) VALUES('book',0,'source','Target.','pending')"
                        )
                    body = json.dumps(
                        {
                            "choices": [
                                {
                                    "message": {"content": content},
                                    "finish_reason": reason,
                                }
                            ]
                        }
                    )
                    with endpoint(body=body) as requests:
                        aps_books.run(db, ["a", "b"], "prompt")
                        aps_books.run(db, ["a", "b"], "prompt")
                        self.assertEqual(len(requests), 1)
                    self.assertEqual(db.execute("SELECT state FROM chunks").fetchone()[0], "unknown")
                    self.assertEqual(
                        db.execute("SELECT outcome FROM attempts").fetchall()[0][0],
                        "RuntimeError",
                    )
                    metadata = json.loads(db.execute("SELECT metadata FROM attempt_metadata").fetchone()[0])
                    self.assertEqual(metadata["request_id"], "request-1")
                finally:
                    db.close()

    def test_recognized_incomplete_or_empty_completions_remain_retryable(self):
        for content, reason in [
            ("", "stop"),
            ("Target.", "length"),
            ("Target.", "content_filter"),
            ("Target.", "tool_calls"),
            ("Target.", "function_call"),
        ]:
            with self.subTest(content=content, reason=reason):
                body = json.dumps({"choices": [{"message": {"content": content}, "finish_reason": reason}]})
                with endpoint(body=body) as requests:
                    with self.assertRaises(ValueError):
                        transport.request("Target.", "a", "prompt")
                    self.assertEqual(len(requests), 1)

    def test_settings_reuses_existing_discovery_without_generation(self):
        module = Mock()
        module.setting.return_value = "fixture-key"
        module.base_url.return_value = "http://localhost:3001/v1"
        with patch.dict(sys.modules, {"freellm": module}):
            self.assertEqual(transport.settings(), ("http://localhost:3001/v1", "fixture-key"))
        module.setting.assert_called_once_with("FREELLMAPI_API_KEY", "FREE_LLMAPI_API_KEY")
        module.freellm.assert_not_called()


if __name__ == "__main__":
    unittest.main()
