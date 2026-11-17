# this_file: tests/aps/quality_fixtures.py
"""Explicit synthetic reviewer for plumbing tests; never semantic acceptance evidence."""

import json

from twat_text.aps import quality as quality
from twat_text.aps import transport as transport

METADATA = {
    "http_status": 200,
    "usage": {"is_byok": False, "cost": 0},
    "finish_reason": "stop",
    "model": "offline-test-fixture",
}


def passing(payload, route, prompt):
    if payload["phase"] == "reconcile":
        result = {
            "decision": "pass",
            "checks": dict.fromkeys(quality.DIMENSIONS, "pass"),
            "findings": [],
        }
    else:
        support = "candidate" if payload["phase"] == "coverage" else "source"
        result = {
            "decision": "pass",
            "units": [
                {
                    "quote": payload["owned"]["text"],
                    "supports": [
                        {
                            "segment": support,
                            "quote": payload["segments"][support][:500],
                            "occurrence": 0,
                            "proposition": 0,
                        }
                    ],
                    "verdict": "pass",
                    "reason": "",
                    "propositions": [payload["owned"]["text"].strip()[:400]],
                    "checks": dict.fromkeys(quality.DIMENSIONS, "pass"),
                    "missing_referents": [],
                }
            ],
        }
    return transport.Completion(json.dumps(result), METADATA)


def calibrate_fixture(db, routes):
    """Synthetic calibration recordings exercise the real persistent gate."""
    gold = {c["id"]: c for c in json.loads(quality.CALIBRATION.read_text())["cases"]}
    for route in routes:
        records = {}
        for job in quality.calibration_inputs(quality.CALIBRATION, route)["jobs"]:
            result = json.loads(passing(job["payload"], route, job["prompt"]))
            case = gold[job["case"]]
            if case["expected"] == "revise":
                result["decision"] = "revise"
                if job["payload"]["phase"] == "reconcile":
                    result["checks"]["fidelity"] = "revise"
                    result["findings"] = [
                        {
                            "segment": "candidate",
                            "quote": case["candidate"][:500],
                            "occurrence": 0,
                            "reason": case["defect"],
                        }
                    ]
                else:
                    result["units"][0].update(verdict="revise", reason=case["defect"])
                    result["units"][0]["checks"]["fidelity"] = "revise"
            records[job["key"]] = {"raw": json.dumps(result), "metadata": METADATA}
        _, result = quality.persist_calibration(db, quality.CALIBRATION, route, records)
        assert result["all_matched"], result


def review_fixture(db):
    """Backfill only synthetic tests' handcrafted generation records, then run real review code."""
    with db:
        for attempt in db.execute("SELECT rowid,* FROM attempts WHERE outcome='done'").fetchall():
            row = db.execute(
                "SELECT * FROM chunks WHERE book=? AND ordinal=?",
                (attempt["book"], attempt["ordinal"]),
            ).fetchone()
            db.execute(
                "INSERT OR IGNORE INTO attempt_responses VALUES(?,?,NULL)",
                (attempt["rowid"], row["response"]),
            )
            db.execute(
                "INSERT OR IGNORE INTO attempt_metadata VALUES(?,?)",
                (attempt["rowid"], json.dumps(METADATA)),
            )
    quality.initialize(db)
    calibrate_fixture(db, ["freellm:fixture-reviewer", "freellm:fixture-repairer"])
    return quality.review(
        db,
        ["freellm:fixture-reviewer", "freellm:fixture-repairer"],
        "Fixture prompt",
        complete=passing,
        request_limit=1000,
    )
