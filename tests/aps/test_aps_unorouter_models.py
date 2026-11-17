# this_file: tests/aps/test_aps_unorouter_models.py
"""Exact multi-model contract routes, using offline evidence and loopback only."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import test_aps_quality as fixtures
from test_aps_unorouter import observation, catalog, KEY
from test_aps_transport import endpoint
from quality_fixtures import passing, calibrate_fixture
from twat_text.aps import books as books
from twat_text.aps import quality as q
from twat_text.aps import semantics as semantics
from twat_text.aps import transport as transport
from twat_text.aps import unorouter as direct

SOLAR = "direct-unorouter:solar-pro4:free"
STEP = "direct-unorouter:step-5-preview:free"


def model_catalog(route):
    value = catalog()
    if route == direct.ROUTE:
        return value
    privacy = direct.route_profile(direct.policy(), route)["privacy"]
    value.update(deepcopy(privacy))
    value["model_name"] = direct.exact_model(route)
    value["group_ratio"] = dict.fromkeys(value["enable_groups"], 0)
    return value


def model_observation(key, route):
    result = observation(key)
    result["route"] = route
    result["catalog"] = model_catalog(route)
    result["catalog_url"] = direct.route_profile(direct.policy(), route)["catalog_url"]
    result["catalog_sha256"] = direct.sha(result["catalog"])
    result["effective"] = direct.catalog_policy(result["catalog"], route=route)
    return result


def completion(text, route):
    return transport.Completion(
        text,
        {
            "http_status": 200,
            "finish_reason": "stop",
            "model": direct.exact_model(route),
            "usage": {"prompt_tokens": 10, "completion_tokens": 10},
        },
    )


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = books.connect(self.root / "work")
        self.addCleanup(lambda: self.db.close())
        q.initialize(self.db)

    def dispatch(self, route, **kwargs):
        return direct.dispatch(
            kwargs.pop("db", self.db),
            {"source": "fictional source"},
            route,
            "prompt",
            kwargs.pop("policy", q.policy()),
            observe_fn=kwargs.pop("observe_fn", lambda key: model_observation(key, route)),
            key_fn=lambda: KEY,
            send=kwargs.pop("send", lambda *a: completion("Complete.", route)),
            **kwargs,
        )

    def test_three_exact_models_reach_loopback_without_fallbacks(self):
        for route in direct.ROUTES:
            with self.subTest(route=route):
                body = json.dumps(
                    {
                        "model": direct.exact_model(route),
                        "usage": {},
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {"content": "Complete."},
                            }
                        ],
                    }
                )
                with endpoint(body=body) as calls:

                    def send(text, model, prompt, base, key):
                        self.assertEqual(base, direct.BASE)
                        return transport.raw_request(text, model, prompt, transport.settings()[0], key)

                    result = self.dispatch(route, send=send)
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(calls[0]["model"], direct.exact_model(route))
                    self.assertEqual(
                        set(calls[0]),
                        {"model", "messages", "max_tokens", "temperature", "top_p"},
                    )
                q.accepted_response(
                    str(result),
                    result.metadata,
                    self.db,
                    ({"source": "fictional source"}, route, "prompt"),
                )
        self.assertEqual(q.holds(self.db), [])

    def test_each_route_observes_its_exact_catalogue_and_default_dispatch_route(self):
        for route in (SOLAR, STEP):
            expected = direct.route_profile(direct.policy(), route)["catalog_url"]
            fetched = []

            def fetch(url, key=None):
                fetched.append((url, key))
                if url == expected:
                    return json.dumps(model_catalog(route))
                self.assertEqual(url, direct.COUNTER)
                self.assertEqual(key, KEY)
                return json.dumps(
                    {
                        "success": True,
                        "data": {"object": "token_usage", "total_used": 0},
                    }
                )

            # Pin the clock to the stored contract observation so the test does not
            # age past the contract revalidation cadence.
            pinned = json.loads(direct.CONTRACT.read_text())["observed_at"]
            observed = direct.observe(KEY, fetch=fetch, clock=lambda: pinned, route=route)
            with patch.object(direct.time, "time", return_value=pinned):
                direct.check_observation(observed)
            self.assertEqual(fetched, [(expected, None), (direct.COUNTER, KEY)])
            with patch.object(
                direct,
                "observe",
                side_effect=lambda key, route: model_observation(key, route),
            ) as observer:
                result = direct.dispatch(
                    self.db,
                    {"route": route},
                    route,
                    "prompt",
                    q.policy(),
                    key_fn=lambda: KEY,
                    send=lambda *a: completion("Complete.", route),
                )
                self.assertEqual(str(result), "Complete.")
                self.assertEqual(observer.call_count, 2)
                observer.assert_called_with(KEY, route=route)

    def test_route_profiles_bind_distinct_privacy_and_reject_aliases_or_unlisted_models(
        self,
    ):
        solar = direct.route_profile(direct.policy(), SOLAR)["privacy"]
        step = direct.route_profile(direct.policy(), STEP)["privacy"]
        self.assertEqual(
            solar["own_purpose"],
            {"trains_or_retains": True, "consent_required": False, "countries": ["KR"]},
        )
        self.assertEqual(step["own_purpose"]["countries"], ["US"])
        self.assertEqual(solar["group_providers"]["up1-solar-pro4-free"]["prompts"], "unknown")
        self.assertEqual(step["group_providers"]["nvda1-step-5-preview-free"]["prompts"], "trains")
        self.assertEqual(q.quality_routes([SOLAR, STEP]), [SOLAR, STEP])
        for route in (SOLAR, STEP):
            with self.assertRaisesRegex(ValueError, "aliases"):
                q.quality_routes([route, "freellm:unorouter:" + direct.exact_model(route)])
        for route in (
            "direct-unorouter:solar-pro4",
            "direct-unorouter:auto:free",
            "direct-unorouter:unknown:free",
            "direct-unorouter:step-5-preview:free:free",
        ):
            key = Mock(side_effect=AssertionError("credential discovery"))
            with self.subTest(route=route), self.assertRaises(ValueError):
                direct.dispatch(self.db, {}, route, "prompt", q.policy(), key_fn=key)
            key.assert_not_called()

    def test_each_model_rejects_price_consent_and_every_provider_privacy_drift_before_post(
        self,
    ):
        mutations = [
            lambda c: c.update(model_name="another:free"),
            lambda c: c.update(input_price=1),
            lambda c: c.update(is_free=False),
            lambda c: c["own_purpose"].update(consent_required=True),
            lambda c: c["own_purpose"].update(countries=["ZZ"]),
        ]
        for field in (
            "prompts",
            "seat_country",
            "transfer",
            "unsafe_third_country",
            "onward_unvetted",
        ):
            mutations.append(
                lambda c, field=field: next(iter(c["group_providers"].values())).update(
                    {field: True if field in ("unsafe_third_country", "onward_unvetted") else "changed"}
                )
            )
        for route in (SOLAR, STEP):
            for mutate in mutations:
                record = model_observation(KEY, route)
                mutate(record["catalog"])
                send = Mock(side_effect=AssertionError("unexpected POST"))
                with (
                    self.subTest(route=route, mutate=mutate),
                    self.assertRaises(direct.PreSubmissionError),
                ):
                    self.dispatch(route, observe_fn=lambda key: record, send=send)
                send.assert_not_called()
        self.assertEqual(self.db.execute("SELECT count(*) FROM quality_dispatches").fetchone()[0], 0)
        self.assertEqual(q.holds(self.db), [])

    def test_returned_different_model_holds_all_routes(self):
        send = Mock(return_value=completion("Wrong model.", STEP))
        with self.assertRaises(RuntimeError):
            self.dispatch(SOLAR, send=send)
        with self.assertRaises(RuntimeError):
            self.dispatch(STEP, send=send)
        self.assertEqual(send.call_count, 1)
        self.assertTrue(q.holds(self.db))

    def test_legacy_minimax_policy_validates_historical_execution_only(self):
        old = deepcopy(q.policy())
        del old["direct_contract"]["routes"]
        result = self.dispatch(direct.ROUTE, policy=old)
        stored = dict(self.db.execute("SELECT * FROM quality_dispatches").fetchone())
        self.db.close()
        self.db = books.connect(self.root / "work")
        self.assertTrue(direct.verified(self.db, str(result), result.metadata, old))
        self.assertFalse(direct.verified(self.db, str(result), result.metadata, q.policy()))
        self.assertEqual(q.holds(self.db), [])
        for route in (SOLAR, STEP):
            with self.assertRaises(ValueError):
                direct.route_profile(old["direct_contract"], route)
        self.assertEqual(dict(self.db.execute("SELECT * FROM quality_dispatches").fetchone()), stored)
        with patch.object(direct, "policy", return_value={**direct.policy(), "routes": {}}):
            self.assertTrue(direct.verified(self.db, str(result), result.metadata, old))
            self.assertEqual(q.holds(self.db), [])

    def test_minimax_unknown_blocks_new_route_calibration_review_and_publication(self):
        source = self.root / "source" / "book"
        source.mkdir(parents=True)
        (source / "1.md").write_text("Target.")
        books.prepare(self.db, [self.root / "source"], "prompt", 3000, list, lambda t, n: [t])
        with self.assertRaises(RuntimeError):
            self.dispatch(direct.ROUTE, send=Mock(side_effect=TimeoutError("fixture")))
        first = dict(self.db.execute("SELECT * FROM quality_dispatches").fetchone())
        self.db.close()
        self.db = books.connect(self.root / "work")
        send = Mock(side_effect=AssertionError("unknown must hold"))
        for route in (SOLAR, STEP):
            with self.assertRaises(RuntimeError):
                self.dispatch(route, send=send)
            self.assertEqual(q.live_calibration(self.db, route)["reason"], "unknown")
        self.assertEqual(
            q.review(self.db, [SOLAR, STEP], "prompt", complete=send),
            {"hold": "unknown"},
        )
        with self.assertRaisesRegex(ValueError, "Unreconciled quality"):
            books.publish(self.db, self.root / "out")
        send.assert_not_called()
        self.assertEqual(dict(self.db.execute("SELECT * FROM quality_dispatches").fetchone()), first)

    def test_known429_cooldown_is_model_scoped_but_unknown_is_global(self):
        def reject(*args):
            transport.check_status({"http_status": 429, "response_headers": {"retry-after": "3600"}})

        with self.assertRaises(direct.RetryDeferred):
            self.dispatch(SOLAR, send=reject)
        self.assertEqual(str(self.dispatch(STEP)), "Complete.")
        self.assertEqual(q.holds(self.db), [])
        with self.assertRaises(direct.RetryDeferred):
            self.dispatch(SOLAR, send=Mock(side_effect=AssertionError("cooldown")))

    def test_distinct_contract_models_calibrate_audit_repair_and_publish_with_task2_provenance(
        self,
    ):
        f = fixtures.QualityTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        routes = [SOLAR, STEP]
        calibrate_fixture(f.db, routes)
        gold = {
            route: json.loads(
                f.db.execute("SELECT records FROM quality_calibrations WHERE route=?", (route,)).fetchone()[0]
            )
            for route in routes
        }
        initial = f.row["response"]
        revised = initial.replace("A loop over word", "A for loop over the string word")
        calls = []
        original = direct.dispatch

        def local(db, payload, route, prompt, policy):
            def send(text, model, wire_prompt, base, key):
                self.assertEqual(model, direct.exact_model(route))
                calls.append((route, payload))
                record = gold[route].get(q.sha([payload, route, prompt]))
                if record:
                    raw = record["raw"]
                elif "phase" not in payload:
                    raw = revised
                else:
                    result = json.loads(passing(payload, route, prompt))
                    if payload["segments"]["candidate"] == initial:
                        result["decision"] = "revise"
                        if payload["phase"] == "reconcile":
                            result["checks"]["fidelity"] = "revise"
                            result["findings"] = [
                                {
                                    "segment": "candidate",
                                    "quote": "A loop",
                                    "occurrence": 0,
                                    "reason": "Identify the string loop.",
                                }
                            ]
                        else:
                            result["units"][0]["checks"]["fidelity"] = "revise"
                            result["units"][0].update(verdict="revise", reason="Identify the string loop.")
                    raw = json.dumps(result)
                return completion(raw, route)

            return original(
                db,
                payload,
                route,
                prompt,
                policy,
                observe_fn=lambda key: model_observation(key, route),
                key_fn=lambda: KEY,
                send=send,
            )

        with patch.object(direct, "dispatch", side_effect=local):
            for route in routes:
                result = q.live_calibration(f.db, route, request_limit=100)
                self.assertTrue(result["complete"])
            self.assertEqual(len(calls), 60)
            result = q.review(f.db, routes, "Owner prompt", complete=q.live_completions(f.db))
            self.assertEqual(list(result.values()), ["accepted"])
        self.assertEqual(f.current()["response"], revised)
        repair = f.db.execute("SELECT * FROM quality_requests WHERE stage='repair'").fetchone()
        self.assertEqual(repair["model"], STEP)
        self.assertEqual(
            {r[0] for r in f.db.execute("SELECT model FROM quality_requests WHERE stage='audit'")},
            {SOLAR},
        )
        selected = q.verify_selection(f.db, f.current())
        self.assertTrue(all(r["billing_basis"] == "contract-backed" for r in selected["reviews"]))
        out = f.root / "out"
        books.publish(f.db, out)
        self.assertIn(revised, (out / "book.md").read_text())
        semantics.inspect_task1(f.root / "work/aps.sqlite3", out)
        with patch.object(q, "policy", return_value={**q.policy(), "implementation": "new-policy"}):
            self.assertEqual(q.holds(f.db), [])
            self.assertIsNone(q.calibrated(f.db, SOLAR))
            self.assertIsNone(q.calibrated(f.db, STEP))


if __name__ == "__main__":
    unittest.main()
