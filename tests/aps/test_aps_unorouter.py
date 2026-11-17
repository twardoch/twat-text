# this_file: tests/aps/test_aps_unorouter.py
"""Synthetic observations and loopback POSTs only; no provider inference."""

from copy import deepcopy
import hashlib
import http.server
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from twat_text.aps import books as books
from twat_text.aps import quality as q
from twat_text.aps import transport as transport
from twat_text.aps import unorouter as direct
from twat_text.aps import semantics as semantics
import test_aps_quality as fixtures
from quality_fixtures import passing, calibrate_fixture

KEY = "local-test-not-a-provider-key"
META = {
    "http_status": 200,
    "finish_reason": "stop",
    "model": direct.MODEL,
    "usage": {"prompt_tokens": 10, "completion_tokens": 10},
}


def catalog():
    privacy = json.loads(direct.CONTRACT.read_text())["privacy"]
    return {
        "model_name": direct.MODEL,
        "is_free": True,
        "online": True,
        "input_price": 0,
        "output_price": 0,
        "fixed_price": 0,
        "is_fixed_price": False,
        "own_purpose": privacy["own_purpose"],
        "enable_groups": ["test-a", "test-b"],
        "group_ratio": {"test-a": 0, "test-b": 0},
        "group_providers": {
            "test-a": privacy["provider"],
            "test-b": privacy["provider"],
        },
    }


def observation(key=KEY):
    now = time.time()
    value = catalog()
    contract = json.loads(direct.CONTRACT.read_text())
    contract["observed_at"] = now
    return {
        "started": now,
        "ended": now,
        "contract": contract,
        "catalog_url": direct.CATALOG,
        "catalog": value,
        "catalog_sha256": direct.sha(value),
        "effective": direct.catalog_policy(value),
        "counter_url": direct.COUNTER,
        "counter": {"object": "token_usage", "total_used": 0},
        "credential_sha256": hashlib.sha256(key.encode()).hexdigest(),
    }


class DirectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = books.connect(self.root)
        self.addCleanup(lambda: self.db.close())
        q.initialize(self.db)
        self.calls = []

    def send(self, text, model, prompt, base, key):
        self.assertEqual((model, base, key), (direct.MODEL, direct.BASE, KEY))
        self.calls.append(json.loads(text))
        return transport.Completion("A complete response.", deepcopy(META))

    def dispatch(self, **kwargs):
        return direct.dispatch(
            self.db,
            {"source": "owned fictional source"},
            direct.ROUTE,
            "prompt",
            q.policy(),
            observe_fn=kwargs.pop("observe_fn", observation),
            key_fn=lambda: KEY,
            send=kwargs.pop("send", self.send),
            **kwargs,
        )

    def test_malformed200_diagnostic_is_bound_durably_and_never_retried_or_published(
        self,
    ):
        from test_aps_transport import endpoint

        source = self.root / "source" / "book"
        source.mkdir(parents=True)
        (source / "1.md").write_text("Target.")
        books.prepare(
            self.db,
            [self.root / "source"],
            "prompt",
            3000,
            list,
            lambda text, limit: [text],
        )
        escaped = "".join("\\u" + f"{ord(c):04x}" for c in KEY)
        body = '{"error":{"message":"' + escaped + ' private failure"}}'
        with endpoint(body=body) as requests:

            def send(text, model, prompt, base, key):
                return transport.raw_request(text, model, prompt, transport.settings()[0], key)

            with self.assertRaises(RuntimeError):
                self.dispatch(send=send)
            first = dict(self.db.execute("SELECT * FROM quality_dispatches").fetchone())
            self.db.close()
            self.db = books.connect(self.root)
            with self.assertRaises(RuntimeError):
                self.dispatch(send=send)
            self.assertEqual(len(requests), 1)
        self.assertEqual(first["state"], "unknown")
        self.assertIsNone(first["raw"])
        self.assertEqual(first["route"], direct.ROUTE)
        self.assertEqual(json.loads(first["payload"]), {"source": "owned fictional source"})
        self.assertEqual(first["prompt"], "prompt")
        metadata = json.loads(first["metadata"])
        self.assertEqual(metadata["http_status"], 200)
        self.assertIn("[REDACTED] private failure", metadata["malformed_response"]["text"])
        self.assertNotIn(KEY, first["metadata"])
        self.assertEqual(dict(self.db.execute("SELECT * FROM quality_dispatches").fetchone()), first)
        self.assertTrue(q.holds(self.db))
        with self.assertRaisesRegex(ValueError, "Unreconciled quality"):
            books.publish(self.db, self.root / "published")
        self.assertFalse((self.root / "published").exists())

    def test_counter_uses_actual_wrapped_schema_and_rejects_ambiguous_values(self):
        good = {
            "success": True,
            "data": {
                "object": "token_usage",
                "total_used": 42,
                "name": "do-not-retain",
            },
        }
        self.assertEqual(direct.counter(good), {"object": "token_usage", "total_used": 42})
        for data in (
            good["data"],
            {"success": False, "data": good["data"]},
            *(
                {"success": True, "data": {"object": "token_usage", "total_used": n}}
                for n in (None, True, -1, 1.5, "0")
            ),
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                direct.counter(data)

    def test_privacy_and_active_group_prices_are_complete_order_insensitive_policy(
        self,
    ):
        value = catalog()
        reordered = deepcopy(value)
        reordered["enable_groups"].reverse()
        reordered["group_providers"] = dict(reversed(list(reordered["group_providers"].items())))
        self.assertEqual(direct.catalog_policy(value), direct.catalog_policy(reordered))
        for field, changed in (
            ("prompts", "unknown"),
            ("seat_country", "CN"),
            ("transfer", "unknown"),
            ("unsafe_third_country", True),
            ("onward_unvetted", True),
        ):
            modified = deepcopy(value)
            modified["group_providers"]["test-a"][field] = changed
            with self.subTest(field=field), self.assertRaises(ValueError):
                direct.catalog_policy(modified)
        for field in ("input_price", "output_price", "fixed_price"):
            modified = deepcopy(value)
            modified[field] = 0.01
            with self.subTest(field=field), self.assertRaises(ValueError):
                direct.catalog_policy(modified)
        value["group_ratio"]["test-a"] = 1
        with self.assertRaises(ValueError):
            direct.catalog_policy(value)

    def test_contract_cadence_reuses_fresh_evidence_but_not_expired_or_changed_promise(
        self,
    ):
        seed = json.loads(direct.CONTRACT.read_text())

        def unavailable(*args):
            raise ValueError("403 fixture")

        self.assertEqual(direct.contract_observation(seed["observed_at"] + 1, unavailable), seed)
        with self.assertRaises(ValueError):
            direct.contract_observation(seed["observed_at"] + direct.CADENCE + 1, unavailable)
        refreshed = direct.contract_observation(
            seed["observed_at"] + direct.CADENCE + 1,
            lambda url: (
                "<p>"
                + direct.CONTRACT_TEXT
                + " The same name without the suffix is the paid version: uncapped, billed per token. Switching is a one-string change.</p>"
            ),
        )
        self.assertEqual(refreshed["method"], "official-https-get")
        with self.assertRaises(ValueError):
            direct.contract_observation(
                seed["observed_at"] + direct.CADENCE + 1,
                lambda url: "A changed contract",
            )

    def test_valid_contract_preserves_absent_billing_and_reopens_without_resubmission(
        self,
    ):
        result = self.dispatch()
        q.accepted_response(
            str(result),
            result.metadata,
            self.db,
            ({"source": "owned fictional source"}, direct.ROUTE, "prompt"),
        )
        self.assertNotIn("cost", result.metadata["usage"])
        self.assertNotIn("is_byok", result.metadata["usage"])
        self.assertFalse(q.free_metadata(result.metadata))
        self.assertEqual(q.holds(self.db), [])
        with self.assertRaises(RuntimeError):
            q.accepted_response(str(result), result.metadata)  # metadata alone is not evidence
        with self.assertRaises(RuntimeError):
            q.accepted_response(
                str(result),
                result.metadata,
                self.db,
                ({"source": "other"}, direct.ROUTE, "prompt"),
            )
        self.db.close()
        self.db = books.connect(self.root)
        self.assertEqual(str(self.dispatch()), str(result))
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE quality_dispatches SET raw='changed'")
        dump = "\n".join(self.db.iterdump())
        self.assertNotIn(KEY, dump)

    def test_precheck_change_sends_zero_requests(self):
        def bad(key):
            record = observation(key)
            record["catalog"]["own_purpose"]["consent_required"] = True
            return record

        with self.assertRaises(ValueError):
            self.dispatch(observe_fn=bad)
        self.assertEqual(self.calls, [])
        self.assertEqual(q.holds(self.db), [])

    def test_postcheck_failure_retains_raw_and_blocks_reopen_and_route_change(self):
        count = 0

        def changed(key):
            nonlocal count
            count += 1
            record = observation(key)
            if count == 2:
                record["counter"]["total_used"] = 1
            return record

        with self.assertRaises(RuntimeError):
            self.dispatch(observe_fn=changed)
        self.assertEqual(len(self.calls), 1)
        row = self.db.execute("SELECT * FROM quality_dispatches").fetchone()
        self.assertEqual(row["raw"], "A complete response.")
        self.assertEqual(row["state"], "unknown")
        self.db.close()
        self.db = books.connect(self.root)
        self.assertTrue(q.holds(self.db))
        with self.assertRaises(RuntimeError):
            self.dispatch()
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(ValueError):
            books.publish(self.db, self.root / "out")

    def test_incomplete_known_response_and_billing_conflict_remain_distinct(self):
        for usage, wanted in (
            (None, transport.ValidationError),
            ({"cost": 7}, RuntimeError),
            ({"is_byok": True}, RuntimeError),
        ):
            with self.subTest(usage=usage):
                db = books.connect(self.root / str(len(self.calls)))
                self.addCleanup(db.close)

                def incomplete(*args):
                    self.calls.append(1)
                    metadata = {**META, "usage": usage, "finish_reason": "length"}
                    return transport.checked_completion("partial", metadata)

                with self.assertRaises(wanted):
                    direct.dispatch(
                        db,
                        {},
                        direct.ROUTE,
                        "prompt",
                        q.policy(),
                        observe_fn=observation,
                        key_fn=lambda: KEY,
                        send=incomplete,
                    )
                self.assertEqual(bool(direct.holds(db)), wanted is RuntimeError)

    def test_timeout_and_crash_before_durable_response_never_retry(self):
        def interrupted(*args):
            self.calls.append(1)
            raise TimeoutError("fixture")

        with self.assertRaises(RuntimeError):
            self.dispatch(send=interrupted)
        self.assertTrue(q.holds(self.db))
        self.assertIsNone(self.db.execute("SELECT raw FROM quality_dispatches").fetchone()[0])
        # A crash after HTTP returned but before UPDATE leaves exactly the same conservative claim.
        other = books.connect(self.root / "crash")
        self.addCleanup(other.close)
        direct.initialize(other)
        other.execute(
            "CREATE TRIGGER crash_before_raw BEFORE UPDATE OF raw ON quality_dispatches BEGIN SELECT RAISE(ABORT,'simulated write interruption'); END"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            direct.dispatch(
                other,
                {},
                direct.ROUTE,
                "prompt",
                q.policy(),
                observe_fn=observation,
                key_fn=lambda: KEY,
                send=self.send,
            )
        self.assertEqual(
            other.execute("SELECT state,raw FROM quality_dispatches").fetchone()[0],
            "dispatching",
        )
        self.assertIsNone(other.execute("SELECT raw FROM quality_dispatches").fetchone()[0])
        with self.assertRaises(RuntimeError):
            direct.dispatch(
                other,
                {},
                direct.ROUTE,
                "prompt",
                q.policy(),
                observe_fn=observation,
                key_fn=lambda: KEY,
                send=self.send,
            )
        self.assertEqual(len(self.calls), 2)

    def test_loopback_partial_response_one_post_and_exact_payload(self):
        requests = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                body = json.dumps(
                    {
                        "model": direct.MODEL,
                        "choices": [
                            {
                                "finish_reason": "length",
                                "message": {"content": "partial"},
                            }
                        ],
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def send(text, model, prompt, base, key):
            return transport.raw_request(text, model, prompt, f"http://127.0.0.1:{server.server_port}/v1", key)

        with self.assertRaises(transport.ValidationError):
            self.dispatch(send=send)
        with self.assertRaises(transport.ValidationError):
            self.dispatch(send=send)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["model"], direct.MODEL)
        self.assertNotIn("models", requests[0])
        self.assertEqual(q.holds(self.db), [])

    def test_evidence_get_has_whole_request_deadline_and_no_retry(self):
        import subprocess

        with patch.object(
            direct.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired("worker", 20),
        ) as run:
            with self.assertRaisesRegex(ValueError, "evidence unavailable"):
                direct.get(direct.COUNTER, KEY)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.kwargs["timeout"], 20)

    def test_changed_post_privacy_blocks_full_review_publication_and_route_reversal(
        self,
    ):
        f = fixtures.QualityTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        calibrate_fixture(f.db, [direct.ROUTE])
        count = 0

        def observe(key):
            nonlocal count
            count += 1
            record = observation(key)
            if count == 2:
                record["catalog"]["group_providers"]["test-a"]["prompts"] = "unknown"
            return record

        def complete(payload, route, prompt):
            return direct.dispatch(
                f.db,
                payload,
                route,
                prompt,
                q.policy(),
                observe_fn=observe,
                key_fn=lambda: KEY,
                send=lambda *a: transport.Completion(str(passing(payload, route, prompt)), deepcopy(META)),
            )

        routes = [direct.ROUTE, f.routes[0]]
        self.assertEqual(
            list(q.review(f.db, routes, "Owner prompt", complete=complete).values()),
            ["unknown"],
        )
        f.db.close()
        f.db = books.connect(f.root / "work")
        self.assertEqual(
            list(
                q.review(
                    f.db,
                    list(reversed(routes)),
                    "Owner prompt",
                    complete=lambda *a: self.fail("hold bypass"),
                ).values()
            ),
            ["unknown"],
        )
        with self.assertRaisesRegex(ValueError, "hold"):
            books.publish(f.db, f.root / "out")
        self.assertIsNotNone(f.db.execute("SELECT raw FROM quality_dispatches").fetchone()[0])

    def test_direct_repair_candidate_requires_its_exact_dispatch_and_distinct_review(
        self,
    ):
        f = fixtures.QualityTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        calibrate_fixture(f.db, [direct.ROUTE])
        initial = f.row["response"]
        revised = initial.replace("A loop over word", "A for loop over the string word")
        routes = [f.routes[0], direct.ROUTE]

        def complete(payload, route, prompt):
            if "phase" not in payload:
                return direct.dispatch(
                    f.db,
                    payload,
                    route,
                    prompt,
                    q.policy(),
                    observe_fn=observation,
                    key_fn=lambda: KEY,
                    send=lambda *a: transport.Completion(revised, deepcopy(META)),
                )
            raw = json.loads(passing(payload, route, prompt))
            if payload["segments"]["candidate"] == initial:
                raw["decision"] = "revise"
                if payload["phase"] == "reconcile":
                    raw["checks"]["fidelity"] = "revise"
                    raw["findings"] = [
                        {
                            "segment": "candidate",
                            "quote": "A loop",
                            "occurrence": 0,
                            "reason": "Identify the string loop.",
                        }
                    ]
                else:
                    raw["units"][0]["checks"]["fidelity"] = "revise"
                    raw["units"][0].update(verdict="revise", reason="Identify the string loop.")
            from quality_fixtures import METADATA

            return transport.Completion(json.dumps(raw), METADATA)

        self.assertEqual(
            list(q.review(f.db, routes, "Owner prompt", complete=complete).values()),
            ["accepted"],
        )
        self.assertEqual(f.current()["response"], revised)
        q.verify_selection(f.db, f.current())
        repair = f.db.execute("SELECT * FROM quality_requests WHERE stage='repair'").fetchone()
        self.assertIn("contract_dispatch", json.loads(repair["metadata"]))
        self.assertEqual(f.db.execute("SELECT count(*) FROM quality_dispatches").fetchone()[0], 1)

    def recovery_fixture(self):
        f = fixtures.QualityTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        calibrate_fixture(f.db, [direct.ROUTE])
        candidate = q.register(f.db, f.row)
        return f, candidate, q.payloads(f.row, candidate)[0]

    def completion_for(self, f, calls):
        def complete(payload, route, prompt):
            def send(*args):
                calls.append(1)
                return transport.Completion(str(passing(payload, route, prompt)), deepcopy(META))

            return direct.dispatch(
                f.db,
                payload,
                route,
                prompt,
                q.policy(),
                observe_fn=observation,
                key_fn=lambda: KEY,
                send=send,
            )

        return complete

    def test_owner_write_crash_recovers_only_exact_verified_dispatch_without_post(self):
        for exact in (True, False):
            with self.subTest(exact=exact):
                f, candidate, payload = self.recovery_fixture()
                calls = []
                f.db.execute(
                    "CREATE TRIGGER owner_write_crash BEFORE UPDATE OF raw ON quality_requests BEGIN SELECT RAISE(ABORT,'owner-write-interrupted'); END"
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    q.request(
                        f.db,
                        candidate,
                        "audit",
                        payload,
                        q.AUDIT,
                        direct.ROUTE,
                        self.completion_for(f, calls),
                        q.encode,
                        q.INPUT_TOKENS,
                    )
                dispatch = f.db.execute("SELECT * FROM quality_dispatches").fetchone()
                self.assertEqual(dispatch["state"], "verified")
                self.assertIsNotNone(dispatch["raw"])
                self.assertIsNotNone(dispatch["post"])
                f.db.execute("DROP TRIGGER owner_write_crash")
                if not exact:
                    f.db.execute("UPDATE quality_requests SET prompt='different input'")
                f.db.commit()
                f.db.close()
                f.db = books.connect(f.root / "work")
                _, state = q.request(
                    f.db,
                    candidate,
                    "audit",
                    payload,
                    q.AUDIT,
                    direct.ROUTE,
                    lambda *a: self.fail("verified response resubmitted"),
                    q.encode,
                    q.INPUT_TOKENS,
                )
                self.assertEqual(state, "completed" if exact else "unknown")
                self.assertEqual(len(calls), 1)
                self.assertEqual(bool(q.holds(f.db)), not exact)

    def test_policy_drift_does_not_reclassify_historical_execution_as_billing_unknown(
        self,
    ):
        f, candidate, payload = self.recovery_fixture()
        calls = []
        self.assertEqual(
            list(
                q.review(
                    f.db,
                    [direct.ROUTE],
                    "Owner prompt",
                    complete=self.completion_for(f, calls),
                ).values()
            ),
            ["accepted"],
        )
        old = q.policy()
        new = {**old, "implementation": "new-policy"}
        current_contract = json.loads(direct.CONTRACT.read_text())
        current_contract["privacy"]["provider"]["prompts"] = "retains"
        path = self.root / "changed-contract.json"
        path.write_text(json.dumps(current_contract))
        observed = []

        def no_model_call(*args):
            observed.append(1)
            raise direct.PreSubmissionError("test precheck stops before inference")

        with (
            patch.object(q, "policy", return_value=new),
            patch.object(direct, "CONTRACT", path),
        ):
            self.assertEqual(q.holds(f.db), [])
            self.assertIsNone(q.calibrated(f.db, direct.ROUTE))
            with self.assertRaisesRegex(ValueError, "policy changed"):
                q.verify_selection(f.db, f.current())
            with patch.object(direct, "dispatch", side_effect=no_model_call):
                result = q.live_calibration(f.db, direct.ROUTE, request_limit=1)
            self.assertEqual(result["reason"], "PreSubmissionError")
            self.assertEqual(observed, [1])
        self.assertEqual(len(calls), 3)

    def test_preflight_diagnostics_survive_reopen_and_allow_only_safe_later_submission(
        self,
    ):
        f, candidate, payload = self.recovery_fixture()
        calls = []

        def failed(payload, route, prompt):
            def unavailable(key):
                raise ValueError("Temporary official evidence failure")

            return direct.dispatch(
                f.db,
                payload,
                route,
                prompt,
                q.policy(),
                observe_fn=unavailable,
                key_fn=lambda: KEY,
                send=lambda *a: self.fail("precheck reached POST"),
            )

        for _ in range(2):
            self.assertEqual(
                q.request(
                    f.db,
                    candidate,
                    "audit",
                    payload,
                    q.AUDIT,
                    direct.ROUTE,
                    failed,
                    q.encode,
                    q.INPUT_TOKENS,
                )[1],
                "preflight",
            )
        self.assertEqual(f.db.execute("SELECT count(*) FROM quality_dispatches").fetchone()[0], 0)
        self.assertEqual(f.db.execute("SELECT count(*) FROM quality_preflights").fetchone()[0], 2)
        self.assertEqual(q.holds(f.db), [])
        f.db.close()
        f.db = books.connect(f.root / "work")
        self.assertEqual(
            q.request(
                f.db,
                candidate,
                "audit",
                payload,
                q.AUDIT,
                direct.ROUTE,
                self.completion_for(f, calls),
                q.encode,
                q.INPUT_TOKENS,
            )[1],
            "completed",
        )
        self.assertEqual(calls, [1])
        self.assertEqual(f.db.execute("SELECT count(*) FROM quality_preflights").fetchone()[0], 2)
        with self.assertRaises(sqlite3.IntegrityError):
            f.db.execute("DELETE FROM quality_preflights")

    def test_preflight_event_recovers_crash_before_owner_diagnostic_write(self):
        f, candidate, payload = self.recovery_fixture()
        f.db.execute(
            "CREATE TRIGGER owner_write_crash BEFORE UPDATE OF raw ON quality_requests BEGIN SELECT RAISE(ABORT,'owner-write-interrupted'); END"
        )

        def failed(payload, route, prompt):
            def unavailable(key):
                raise TimeoutError("Read-only precheck timed out")

            return direct.dispatch(
                f.db,
                payload,
                route,
                prompt,
                q.policy(),
                observe_fn=unavailable,
                key_fn=lambda: KEY,
                send=lambda *a: self.fail("unexpected POST"),
            )

        with self.assertRaises(sqlite3.IntegrityError):
            q.request(
                f.db,
                candidate,
                "audit",
                payload,
                q.AUDIT,
                direct.ROUTE,
                failed,
                q.encode,
                q.INPUT_TOKENS,
            )
        self.assertEqual(f.db.execute("SELECT state FROM quality_requests").fetchone()[0], "running")
        f.db.execute("DROP TRIGGER owner_write_crash")
        f.db.commit()
        f.db.close()
        f.db = books.connect(f.root / "work")
        calls = []
        self.assertEqual(
            q.request(
                f.db,
                candidate,
                "audit",
                payload,
                q.AUDIT,
                direct.ROUTE,
                self.completion_for(f, calls),
                q.encode,
                q.INPUT_TOKENS,
            )[1],
            "completed",
        )
        self.assertEqual(calls, [1])

    def test_resumed_preflight_counts_toward_limit_but_local_receipt_replay_is_free(
        self,
    ):
        f, candidate, payload = self.recovery_fixture()
        calls = []

        def failed(payload, route, prompt):
            def unavailable(key):
                raise ValueError("Temporary precheck failure")

            return direct.dispatch(
                f.db,
                payload,
                route,
                prompt,
                q.policy(),
                observe_fn=unavailable,
                key_fn=lambda: KEY,
                send=lambda *a: self.fail("preflight POST"),
            )

        self.assertEqual(
            q.request(
                f.db,
                candidate,
                "audit",
                payload,
                q.AUDIT,
                direct.ROUTE,
                failed,
                q.encode,
                q.INPUT_TOKENS,
            )[1],
            "preflight",
        )
        # Resume an existing owner row: row-count deltas must not hide its POST.
        complete = self.completion_for(f, calls)
        self.assertEqual(
            list(
                q.review(
                    f.db,
                    [direct.ROUTE],
                    "Owner prompt",
                    complete=complete,
                    request_limit=1,
                ).values()
            ),
            ["request-budget"],
        )
        self.assertEqual(len(calls), 1)
        # The already-completed first window is replayed free on the next invocation.
        self.assertEqual(
            list(
                q.review(
                    f.db,
                    [direct.ROUTE],
                    "Owner prompt",
                    complete=complete,
                    request_limit=1,
                ).values()
            ),
            ["request-budget"],
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            list(
                q.review(
                    f.db,
                    [direct.ROUTE],
                    "Owner prompt",
                    complete=complete,
                    request_limit=1,
                ).values()
            ),
            ["accepted"],
        )
        self.assertEqual(len(calls), 3)

    def test_budget_allows_cached_windows_after_the_last_admitted_submission(self):
        f, candidate, payload = self.recovery_fixture()
        calls = []
        complete = self.completion_for(f, calls)
        for cached in q.payloads(f.row, candidate)[1:]:
            self.assertEqual(
                q.request(
                    f.db,
                    candidate,
                    "audit",
                    cached,
                    q.AUDIT,
                    direct.ROUTE,
                    complete,
                    q.encode,
                    q.INPUT_TOKENS,
                )[1],
                "completed",
            )
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            list(
                q.review(
                    f.db,
                    [direct.ROUTE],
                    "Owner prompt",
                    complete=complete,
                    request_limit=1,
                ).values()
            ),
            ["accepted"],
        )
        self.assertEqual(len(calls), 3)

    def test_resumed_repair_and_audits_share_the_invocation_submission_budget(self):
        f, candidate, payload = self.recovery_fixture()
        initial = f.row["response"]
        revised = initial.replace("A loop over word", "A for loop over the string word")
        routes = [f.routes[0], direct.ROUTE]
        calls, blocked = [], [True]

        def complete(payload, route, prompt):
            if "phase" not in payload:

                def observe(key):
                    if blocked[0]:
                        raise ValueError("Temporary precheck failure")
                    return observation(key)

                def send(*args):
                    calls.append("repair")
                    return transport.Completion(revised, deepcopy(META))

                return direct.dispatch(
                    f.db,
                    payload,
                    route,
                    prompt,
                    q.policy(),
                    observe_fn=observe,
                    key_fn=lambda: KEY,
                    send=send,
                )
            calls.append("audit")
            raw = json.loads(passing(payload, route, prompt))
            if payload["segments"]["candidate"] == initial:
                raw["decision"] = "revise"
                if payload["phase"] == "reconcile":
                    raw["checks"]["fidelity"] = "revise"
                    raw["findings"] = [
                        {
                            "segment": "candidate",
                            "quote": "A loop",
                            "occurrence": 0,
                            "reason": "Identify the string loop.",
                        }
                    ]
                else:
                    raw["units"][0]["checks"]["fidelity"] = "revise"
                    raw["units"][0].update(verdict="revise", reason="Identify the string loop.")
            from quality_fixtures import METADATA

            return transport.Completion(json.dumps(raw), METADATA)

        self.assertEqual(
            list(q.review(f.db, routes, "Owner prompt", complete=complete, request_limit=4).values()),
            ["preflight"],
        )
        self.assertEqual(calls, ["audit"] * 3)
        blocked[0] = False
        self.assertEqual(
            list(q.review(f.db, routes, "Owner prompt", complete=complete, request_limit=1).values()),
            ["request-budget"],
        )
        self.assertEqual(calls, ["audit"] * 3 + ["repair"])
        self.assertEqual(
            list(q.review(f.db, routes, "Owner prompt", complete=complete, request_limit=3).values()),
            ["accepted"],
        )
        self.assertEqual(calls, ["audit"] * 3 + ["repair"] + ["audit"] * 3)

    def test_recorded_local_import_does_not_consume_submission_budget(self):
        f, candidate, payload = self.recovery_fixture()
        route = f.routes[0]
        records = {}
        for payload in q.payloads(f.row, candidate):
            bound = q.bound_payload(f.db, payload, route)
            raw = passing(bound, route, q.AUDIT)
            records[q.sha([bound, route, q.AUDIT])] = {
                "raw": str(raw),
                "metadata": raw.metadata,
            }
        self.assertEqual(
            list(
                q.review(
                    f.db,
                    [route],
                    "Owner prompt",
                    complete=q.recorded_completions(records),
                    request_limit=1,
                ).values()
            ),
            ["accepted"],
        )
        self.assertEqual(f.db.execute("SELECT count(*) FROM quality_requests").fetchone()[0], 3)

    def test_direct_alias_cannot_fake_an_independent_reviewer(self):
        with self.assertRaises(ValueError):
            q.quality_routes([direct.ROUTE, "freellm:unorouter:" + direct.MODEL])

    def test_calibration_review_publication_and_task2_bind_actual_dispatches(self):
        f = fixtures.QualityTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        calibrate_fixture(f.db, [direct.ROUTE])
        # Use frozen gold fixture outputs only to exercise live-calibration plumbing.
        records = json.loads(
            f.db.execute(
                "SELECT records FROM quality_calibrations WHERE route=?",
                (direct.ROUTE,),
            ).fetchone()[0]
        )
        calls = []

        def send(text, model, prompt, base, key):
            payload = json.loads(text)
            calls.append(payload)
            record = records.get(q.sha([payload, direct.ROUTE, prompt]))
            raw = record["raw"] if record else str(passing(payload, direct.ROUTE, prompt))
            return transport.Completion(raw, deepcopy(META))

        original = direct.dispatch

        def local(db, payload, route, prompt, policy):
            return original(
                db,
                payload,
                route,
                prompt,
                policy,
                observe_fn=observation,
                key_fn=lambda: KEY,
                send=send,
            )

        with patch.object(direct, "dispatch", side_effect=local):
            self.assertEqual(
                q.live_calibration(f.db, direct.ROUTE, request_limit=1)["reason"],
                "request-budget",
            )
            self.assertTrue(q.live_calibration(f.db, direct.ROUTE, request_limit=100)["complete"])
            self.assertEqual(len(calls), 30)
            result = q.review(f.db, [direct.ROUTE], "Owner prompt", complete=q.live_completions(f.db))
            self.assertEqual(list(result.values()), ["accepted"])
        with patch.object(q, "policy", return_value={**q.policy(), "implementation": "later-policy"}):
            self.assertEqual(q.holds(f.db), [])  # includes all 30 historical contract calibration receipts
            self.assertIsNone(q.calibrated(f.db, direct.ROUTE))
        with patch.object(q, "AUDIT", q.AUDIT + "\nNew reviewer instructions."):
            self.assertEqual(q.holds(f.db), [])
            self.assertIsNone(q.calibrated(f.db, direct.ROUTE))
            with self.assertRaisesRegex(ValueError, "policy changed"):
                q.verify_selection(f.db, f.current())
        selected = q.verify_selection(f.db, f.current())
        self.assertTrue(all(r["billing_basis"] == "contract-backed" for r in selected["reviews"]))
        out = f.root / "out"
        books.publish(f.db, out)
        self.assertEqual((out / "book.md").read_text(), f.text)
        semantics.inspect_task1(f.root / "work/aps.sqlite3", out)
        with patch.object(direct, "send", create=True, side_effect=AssertionError("no network")):
            self.assertEqual(
                list(
                    q.review(
                        f.db,
                        [direct.ROUTE],
                        "Owner prompt",
                        complete=lambda *a: self.fail("resubmission"),
                    ).values()
                ),
                ["accepted"],
            )
