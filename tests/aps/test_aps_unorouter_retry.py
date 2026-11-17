# this_file: tests/aps/test_aps_unorouter_retry.py
"""Known429 retry tests use fake time and injected completions, never providers."""

import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

import test_aps_quality as fixtures
from test_aps_unorouter import observation, KEY, META
from quality_fixtures import passing, calibrate_fixture
from twat_text.aps import books as books
from twat_text.aps import quality as q
from twat_text.aps import transport as transport
from twat_text.aps import unorouter as direct


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = books.connect(self.root)
        self.addCleanup(lambda: self.db.close())
        q.initialize(self.db)
        self.now = time.time()
        self.calls = []
        self.responses = [429, 200]

    def observe(self, key):
        value = observation(key)
        value["started"] = value["ended"] = self.now
        value["contract"]["observed_at"] = self.now
        return value

    def sleep(self, seconds):
        self.assertLessEqual(seconds, 1)
        self.now += seconds

    def send(self, text, model, prompt, base, key):
        self.calls.append(json.loads(text))
        status = self.responses.pop(0)
        if status == 429:
            transport.check_status(
                {
                    "http_status": 429,
                    "model": direct.MODEL,
                    "response_headers": {"retry-after": "2"},
                    "error_response": '{"error":{"code":"rate_limited"}}',
                }
            )
        return transport.Completion("Complete response.", META)

    def dispatch(self, payload=None, send=None):
        return direct.dispatch(
            self.db,
            payload or {"owned": "fictional target"},
            direct.ROUTE,
            "prompt",
            q.policy(),
            observe_fn=self.observe,
            key_fn=lambda: KEY,
            send=send or self.send,
            clock=lambda: self.now,
        )

    def test_known429_cooldown_appends_success_and_preserves_both_attempts_after_reopen(
        self,
    ):
        with self.assertRaises(direct.RetryDeferred) as caught:
            self.dispatch()
        self.assertTrue(caught.exception.submitted)
        first = dict(self.db.execute("SELECT * FROM quality_dispatches").fetchone())
        self.assertEqual(len(self.calls), 1)
        self.db.close()
        self.db = books.connect(self.root)
        with self.assertRaises(direct.RetryDeferred) as waiting:
            self.dispatch()
        self.assertFalse(waiting.exception.submitted)
        self.assertEqual(len(self.calls), 1)
        self.now = caught.exception.retry_at
        result = self.dispatch()
        self.assertEqual(len(self.calls), 2)
        self.assertNotEqual(result.metadata["contract_dispatch"], first["id"])
        self.assertEqual(
            dict(self.db.execute("SELECT * FROM quality_dispatches WHERE id=?", (first["id"],)).fetchone()),
            first,
        )
        q.accepted_response(
            str(result),
            result.metadata,
            self.db,
            ({"owned": "fictional target"}, direct.ROUTE, "prompt"),
        )
        self.assertEqual(str(self.dispatch()), str(result))
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(q.holds(self.db), [])
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("DELETE FROM quality_retry_links")

    def test_loopback429_then_success_keeps_diagnostics_and_one_post_per_attempt(self):
        from test_aps_transport import endpoint

        def local(text, model, prompt, base, key):
            url, _ = transport.settings()
            return transport.raw_request(text, model, prompt, url, key)

        with endpoint(
            status=429,
            body='{"error":{"code":"rate_limited"}}',
            headers={"Retry-After": "1"},
        ) as first:
            with self.assertRaises(direct.RetryDeferred) as caught:
                self.dispatch(send=local)
        self.assertEqual(len(first), 1)
        first_id = caught.exception.metadata["contract_dispatch"]
        self.assertEqual(caught.exception.metadata["response_headers"]["retry-after"], "1")
        self.now = caught.exception.retry_at
        body = json.dumps(
            {
                "model": direct.MODEL,
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "Complete response."},
                    }
                ],
            }
        )
        with endpoint(body=body) as second:
            result = self.dispatch(send=local)
        self.assertEqual(len(second), 1)
        self.assertNotEqual(result.metadata["contract_dispatch"], first_id)
        self.assertEqual(self.db.execute("SELECT count(*) FROM quality_dispatches").fetchone()[0], 2)
        self.assertEqual(
            json.loads(
                self.db.execute("SELECT metadata FROM quality_dispatches WHERE id=?", (first_id,)).fetchone()[0]
            )["error_response"],
            '{"error":{"code":"rate_limited"}}',
        )

    def test_unknown_retry_cannot_be_replayed_as_the_previous_known429(self):
        with self.assertRaises(direct.RetryDeferred) as caught:
            self.dispatch()
        self.now = caught.exception.retry_at

        def timeout(*args):
            self.calls.append("uncertain")
            raise TimeoutError("fixture")

        with self.assertRaises(RuntimeError):
            self.dispatch(send=timeout)
        self.now += 10000
        with self.assertRaises(RuntimeError):
            self.dispatch()
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(q.holds(self.db))

    def test_three_total429_attempts_exhaust_without_fourth_post(self):
        self.responses = [429] * 4
        for number in range(3):
            wanted = direct.RetryExhausted if number == 2 else direct.RetryDeferred
            with self.assertRaises(wanted) as caught:
                self.dispatch()
            self.now = caught.exception.retry_at + 1
        self.assertEqual(len(self.calls), 3)
        with self.assertRaises(direct.RetryExhausted) as caught:
            self.dispatch()
        self.assertFalse(caught.exception.submitted)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.db.execute("SELECT count(*) FROM quality_dispatches").fetchone()[0], 3)
        self.assertEqual(q.holds(self.db), [])

    def test_retry_after_date_skew_fallback_and_long_deadlines_are_not_shortened(self):
        identity = "0000" + "a" * 60  # deterministic one-second positive jitter
        for value in (None, "invalid", "-1", "NaN"):
            headers = {} if value is None else {"retry-after": value}
            at, basis = direct.retry_deadline({"response_headers": headers}, 1000, 1, identity)
            self.assertEqual((at, basis), (1121, "fallback"))
        at, basis = direct.retry_deadline(
            {
                "response_headers": {
                    "date": "Wed, 21 Oct 2015 07:28:00 GMT",
                    "retry-after": "Wed, 21 Oct 2015 07:30:00 GMT",
                }
            },
            1000,
            0,
            identity,
        )
        self.assertEqual((at, basis), (1121, "retry-after-date"))
        at, basis = direct.retry_deadline({"response_headers": {"retry-after": "999999"}}, 1000, 0, identity)
        self.assertEqual((at, basis), (1001000, "retry-after-seconds"))
        import math

        at, basis = direct.retry_deadline({"response_headers": {"retry-after": "9" * 500}}, 1000, 0, identity)
        self.assertTrue(math.isinf(at))  # Never shorten an unschedulably long server delay.
        with self.assertRaises(ValueError):
            q.continue_known_429(
                lambda: self.fail("invalid wait reached operation"),
                self.db,
                [1],
                math.inf,
            )

    def test_valid_absolute_retry_after_survives_invalid_optional_date(self):
        from email.utils import formatdate

        ended = 1700000000
        for date in ("invalid", "", None, 123):
            with self.subTest(date=date):
                deadline, basis = direct.retry_deadline(
                    {
                        "response_headers": {
                            "retry-after": formatdate(ended + 3600, usegmt=True),
                            "date": date,
                        }
                    },
                    ended,
                    0,
                    "0000" + "a" * 60,
                )
                self.assertEqual((deadline, basis), (ended + 3601, "retry-after-date"))

    def test_reopened_legacy_short_cooldown_enforces_valid_absolute_deadline(self):
        from email.utils import formatdate

        self.now = int(self.now)
        started = self.now

        def reject(*args):
            self.calls.append("legacy429")
            transport.check_status(
                {
                    "http_status": 429,
                    "response_headers": {
                        "retry-after": formatdate(started + 3600, usegmt=True),
                        "date": "invalid optional Date",
                    },
                }
            )

        with patch.object(direct, "retry_deadline", return_value=(started + 61, "fallback")):
            with self.assertRaises(direct.RetryDeferred):
                self.dispatch(send=reject)
        stored = dict(self.db.execute("SELECT * FROM quality_cooldowns").fetchone())
        self.db.close()
        self.db = books.connect(self.root)
        self.now = started + 61
        with self.assertRaises(direct.RetryDeferred) as pending:
            self.dispatch()
        self.assertFalse(pending.exception.submitted)
        self.assertGreater(pending.exception.retry_at, started + 3600)
        self.assertEqual(len(self.calls), 1)
        result = q.continue_known_429(
            lambda: {
                "reason": "retry-wait",
                "_retry_dispatch": pending.exception.metadata["contract_dispatch"],
            },
            self.db,
            [1],
            100,
            clock=lambda: self.now,
            sleep=lambda *a: self.fail("Wait must defer beyond its budget"),
        )
        self.assertEqual(result["reason"], "retry-wait")
        self.now = pending.exception.retry_at
        self.responses = [200]
        self.dispatch()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(dict(self.db.execute("SELECT * FROM quality_cooldowns").fetchone()), stored)
        self.assertEqual(q.holds(self.db), [])

    def test_cooldown_never_shortens_existing_deadline_or_rederives_fallback(self):
        with patch.object(direct, "retry_deadline", return_value=(self.now + 7200, "fallback")):
            with self.assertRaises(direct.RetryDeferred):
                self.dispatch()
        row = self.db.execute("SELECT * FROM quality_dispatches").fetchone()
        stored = dict(self.db.execute("SELECT * FROM quality_cooldowns").fetchone())
        self.assertEqual(direct.cooldown(self.db, row), self.now + 7200)
        with patch.object(direct, "retry_deadline", return_value=(self.now + 9000, "fallback")):
            self.assertEqual(direct.cooldown(self.db, row), self.now + 7200)
        self.assertEqual(dict(self.db.execute("SELECT * FROM quality_cooldowns").fetchone()), stored)

    def test_wait_uses_active_dispatch_not_other_credentials_long_cooldown(self):
        def other_send(*args):
            transport.check_status(
                {
                    "http_status": 429,
                    "response_headers": {"retry-after": "3600"},
                }
            )

        with self.assertRaises(direct.RetryDeferred) as other:
            direct.dispatch(
                self.db,
                {"other": "job"},
                direct.ROUTE,
                "prompt",
                q.policy(),
                observe_fn=self.observe,
                key_fn=lambda: "other-credential",
                send=other_send,
                clock=lambda: self.now,
            )

        def operation():
            try:
                self.dispatch()
            except direct.RetryDeferred as error:
                return {
                    "reason": "retry-wait",
                    "_retry_dispatch": error.metadata["contract_dispatch"],
                }
            return {"complete": True}

        result = q.continue_known_429(
            operation,
            self.db,
            [2],
            100,
            clock=lambda: self.now,
            sleep=self.sleep,
        )
        self.assertEqual(result, {"complete": True})
        self.assertEqual(len(self.calls), 2)
        self.assertLess(self.now, other.exception.retry_at)
        self.assertEqual(q.holds(self.db), [])

    def test_unknown_charge_or_postcheck_failure_never_retries(self):
        for mode in ("charge", "byok", "timeout", "503", "postcheck"):
            with self.subTest(mode=mode):
                db = books.connect(self.root / mode)
                self.addCleanup(db.close)
                calls, observations = [], []

                def observe(key):
                    observations.append(1)
                    if mode == "postcheck" and len(observations) == 2:
                        raise ValueError("postcheck failed")
                    return self.observe(key)

                def send(*args):
                    calls.append(1)
                    if mode == "timeout":
                        raise TimeoutError("fixture")
                    metadata = {"http_status": 503 if mode == "503" else 429}
                    if mode == "charge":
                        metadata["usage"] = {"cost": 2}
                    if mode == "byok":
                        metadata["usage"] = {"is_byok": True}
                    transport.check_status(metadata)

                for _ in range(2):
                    with self.assertRaises(RuntimeError):
                        direct.dispatch(
                            db,
                            {},
                            direct.ROUTE,
                            "prompt",
                            q.policy(),
                            observe_fn=observe,
                            key_fn=lambda: KEY,
                            send=send,
                            clock=lambda: self.now,
                        )
                self.assertEqual(len(calls), 1)
                self.assertTrue(direct.holds(db))

    def test_crash_after_retry_claim_blocks_further_submission(self):
        with self.assertRaises(direct.RetryDeferred) as caught:
            self.dispatch()
        self.now = caught.exception.retry_at
        self.db.execute(
            "CREATE TRIGGER interrupt_retry BEFORE UPDATE OF raw ON quality_dispatches BEGIN SELECT RAISE(ABORT,'interruption'); END"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.dispatch()
        self.db.execute("DROP TRIGGER interrupt_retry")
        self.db.commit()
        self.assertEqual(len(self.calls), 2)
        with self.assertRaises(RuntimeError):
            self.dispatch()
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(q.holds(self.db))

    def test_unattended_calibration_retries_with_shared_limit_and_replays_locally(self):
        original = direct.dispatch
        calls, first = [], [True]

        def local(db, payload, route, prompt, policy):
            def send(*args):
                calls.append(1)
                if first[0]:
                    first[0] = False
                    transport.check_status({"http_status": 429, "response_headers": {"retry-after": "2"}})
                return transport.Completion(str(passing(payload, route, prompt)), META)

            return original(
                db,
                payload,
                route,
                prompt,
                policy,
                observe_fn=self.observe,
                key_fn=lambda: KEY,
                send=send,
                clock=lambda: self.now,
            )

        with patch.object(direct, "dispatch", side_effect=local):
            budget = [1]
            result = q.continue_known_429(
                lambda: q.live_calibration(self.db, direct.ROUTE, budget=budget),
                self.db,
                budget,
                100,
                clock=lambda: self.now,
                sleep=self.sleep,
            )
            self.assertEqual(result["reason"], "retry-wait")
            self.assertEqual(len(calls), 1)
            budget = [1]
            result = q.continue_known_429(
                lambda: q.live_calibration(self.db, direct.ROUTE, budget=budget),
                self.db,
                budget,
                100,
                clock=lambda: self.now,
                sleep=self.sleep,
            )
            self.assertEqual(result["reason"], "request-budget")
            self.assertEqual(len(calls), 2)  # cooldown checks and first result replay were free
            self.assertEqual(budget, [0])

    def test_retry_preflight_failure_retains_exact_no_submission_evidence(self):
        payload = {"owned": "fictional target"}
        with self.assertRaises(direct.RetryDeferred) as caught:
            self.dispatch(payload)
        self.now = caught.exception.retry_at
        started = self.now
        with patch.object(self, "observe", side_effect=RuntimeError("offline")):
            with self.assertRaises(direct.PreSubmissionError):
                self.dispatch(payload)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(direct.preflight_safe(self.db, payload, direct.ROUTE, "prompt", q.policy(), started))
        self.db.close()
        self.db = books.connect(self.root)
        self.dispatch(payload)
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(direct.preflight_safe(self.db, payload, direct.ROUTE, "prompt", q.policy(), started))

    def test_wait_budget_smaller_than_cooldown_defers_without_sleep(self):
        with self.assertRaises(direct.RetryDeferred) as caught:
            self.dispatch()
        result = q.continue_known_429(
            lambda: {
                "reason": "retry-wait",
                "_retry_dispatch": caught.exception.metadata["contract_dispatch"],
            },
            self.db,
            [1],
            0,
            clock=lambda: self.now,
            sleep=lambda *a: self.fail("unexpected sleep"),
        )
        self.assertEqual(result["reason"], "retry-wait")
        self.assertEqual(len(self.calls), 1)

    def test_review_retry_respects_shared_limit_after_known_rejection(self):
        f = fixtures.QualityTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        calibrate_fixture(f.db, [direct.ROUTE])
        calls, first = [], [True]

        def complete(payload, route, prompt):
            def send(*args):
                calls.append(1)
                if first[0]:
                    first[0] = False
                    transport.check_status({"http_status": 429, "response_headers": {"retry-after": "2"}})
                return transport.Completion(str(passing(payload, route, prompt)), META)

            return direct.dispatch(
                f.db,
                payload,
                route,
                prompt,
                q.policy(),
                observe_fn=self.observe,
                key_fn=lambda: KEY,
                send=send,
                clock=lambda: self.now,
            )

        budget = [2]
        result = q.continue_known_429(
            lambda: q.review(
                f.db,
                [direct.ROUTE],
                "Owner prompt",
                complete=complete,
                budget=budget,
                request_limit=2,
            ),
            f.db,
            budget,
            100,
            clock=lambda: self.now,
            sleep=self.sleep,
        )
        self.assertEqual(list(result.values()), ["request-budget"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(budget, [0])
        self.assertEqual(q.holds(f.db), [])
        self.assertEqual(f.db.execute("SELECT count(*) FROM quality_dispatches").fetchone()[0], 2)
