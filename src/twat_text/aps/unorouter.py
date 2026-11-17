# this_file: src/twat_text/aps/unorouter.py
"""Direct free-contract evidence; never a fabricated per-call billing receipt."""

from html.parser import HTMLParser
from email.utils import parsedate_to_datetime
from datetime import timezone
import hashlib
import http.client
import json
import os
import math
from pathlib import Path
import sys
import subprocess
import time
from urllib.parse import urlsplit

from . import transport

ROUTE = "direct-unorouter:minimax-m3.1-flash:free"
MODEL = "minimax-m3.1-flash:free"
ROUTES = (
    ROUTE,
    "direct-unorouter:solar-pro4:free",
    "direct-unorouter:step-5-preview:free",
)
MODELS = Path(__file__).with_name("aps-unorouter-models.json")
BASE = "https://api.unorouter.com/v1"
CATALOG = "https://api.unorouter.com/api/pricing/catalog/model?model=" + MODEL
COUNTER = "https://api.unorouter.com/api/usage/token/"
CONTRACT_URL = "https://unorouter.com/en/docs/platform/models-and-pricing"
CONTRACT_TEXT = "A :free model routes only to free providers and never uses your balance."
CONTRACT = Path(__file__).with_name("aps-unorouter-contract.json")
CADENCE = 24 * 60 * 60
WINDOW = 120  # max time from each catalogue observation to its durable phase
MODE = "unorouter-free-contract-v1"
MAX_ATTEMPTS = 3
FALLBACK_DELAY = 60


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def model_identity(route):
    """Gateway and direct aliases cannot act as independent reviewer models."""
    return route.removeprefix("freellm:").replace("direct-unorouter:", "unorouter:", 1)


def policy():
    definition = json.loads(CONTRACT.read_text())
    return {
        "mode": MODE,
        "implementation": sha(Path(__file__).read_text()),
        "contract": sha(definition),
        "contract_definition": definition,
        "route": ROUTE,  # Legacy default; explicit per-route profiles bind new requests.
        "routes": {
            ROUTE: {
                "model": MODEL,
                "catalog_url": CATALOG,
                "privacy": definition["privacy"],
            },
            **json.loads(MODELS.read_text())["routes"],
        },
        "recheck_seconds": CADENCE,
        "observation_window_seconds": WINDOW,
    }


def exact_model(route):
    """Only explicit reviewed direct pins may reach credential discovery or POST."""
    if route not in ROUTES:
        raise ValueError("Direct model is not allowlisted")
    return route.removeprefix("direct-unorouter:")


def route_profile(recorded, route):
    """Use the execution's recorded profile; old policies authorize only MiniMax."""
    model = exact_model(route)
    profiles = recorded.get("routes")
    if profiles is None:
        if route != ROUTE or recorded.get("route") != ROUTE:
            raise ValueError("Legacy policy authorizes only its recorded MiniMax route")
        definition = recorded.get("contract_definition")
        if definition is None:
            raise ValueError("Historical contract definition absent")
        return {
            "model": MODEL,
            "catalog_url": CATALOG,
            "privacy": definition["privacy"],
        }
    profile = profiles.get(route)
    expected_url = "https://api.unorouter.com/api/pricing/catalog/model?model=" + model
    if not isinstance(profile, dict) or profile.get("model") != model or profile.get("catalog_url") != expected_url:
        raise ValueError("Exact model profile mismatch")
    return profile


def credentials():
    """Read the already-configured key via the installed bridge; never persist it."""
    directory = os.environ.get("TWAT_TEXT_FREELLM_PATH") or str(Path.home() / "Developer/llm2/2611/askpoe2")
    sys.path.insert(0, directory)
    try:
        import freellm

        key = freellm.setting("UNOROUTER_API_KEY", "UNO_ROUTER_API_KEY", "UNOROUTER")
    finally:
        sys.path.remove(directory)
    if not key:
        raise ValueError("Existing UnoRouter credential unavailable")
    return key


def get(url, key=None):
    """Killable whole-GET deadline, including stalled or trickling evidence bodies."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", __spec__.name],
            input=json.dumps({"url": url, "key": key}),
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        if result.returncode:
            raise ValueError("Official evidence unavailable")
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, ValueError):
        raise ValueError("Official evidence unavailable") from None


def get_once(url, key=None):
    """Worker: single GET, fixed official hosts, no redirects or retries."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in (
        "api.unorouter.com",
        "unorouter.com",
    ):
        raise ValueError("Unapproved evidence origin")
    connection = http.client.HTTPSConnection(parsed.hostname, timeout=20)
    try:
        headers = {"Authorization": "Bearer " + key} if key else {}
        connection.request(
            "GET",
            parsed.path + ("?" + parsed.query if parsed.query else ""),
            headers=headers,
        )
        response = connection.getresponse()
        body = response.read(2_000_001)
        if response.status != 200 or len(body) > 2_000_000:
            raise ValueError("Official evidence unavailable")
        return body.decode("utf-8")
    finally:
        connection.close()


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.paragraphs = []
        self.in_paragraph = False
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag == "p":
            self.parts = []
            self.in_paragraph = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
        if tag == "p":
            self.paragraphs.append(" ".join("".join(self.parts).split()))
            self.in_paragraph = False

    def handle_data(self, data):
        if self.in_paragraph and not self.hidden:
            self.parts.append(data)


def contract_observation(now, fetch=get):
    record = json.loads(CONTRACT.read_text())
    if now < record["observed_at"]:
        raise ValueError("Contract observation is from the future")
    if now - record["observed_at"] <= CADENCE:
        return record
    parser = Text()
    parser.feed(fetch(CONTRACT_URL))
    # Exact approved statement; a changed promise cannot be approved by a model.
    matches = [text for text in parser.paragraphs if sha(text) == record["paragraph_sha256"]]
    if len(matches) != 1:
        raise ValueError("Official free contract requires revalidation")
    return {
        **record,
        "observed_at": now,
        "method": "official-https-get",
        "document_sha256": sha(parser.paragraphs),
    }


def canonical(value):
    """Provider/country/group order is immaterial; every field value is retained."""
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return sorted((canonical(v) for v in value), key=encoded)
    return value


def catalog_policy(data, definition=None, route=ROUTE, approved=None):
    model = exact_model(route)
    if not isinstance(data, dict) or data.get("model_name") != model or data.get("is_free") is not True:
        raise ValueError("Exact free model unavailable")
    if data.get("online") is not True:
        raise ValueError("Free model offline")
    for name in ("input_price", "output_price", "fixed_price"):
        if type(data.get(name)) not in (int, float) or data[name] != 0:
            raise ValueError("Free model price changed")
    own = data.get("own_purpose")
    groups = data.get("group_providers")
    enabled = data.get("enable_groups")
    if not isinstance(own, dict) or own.get("consent_required") is not False:
        raise ValueError("Model consent evidence required")
    if (
        type(own.get("trains_or_retains")) is not bool
        or not isinstance(own.get("countries"), list)
        or not own["countries"]
    ):
        raise ValueError("Incomplete model privacy evidence")
    if not isinstance(groups, dict) or not groups or not isinstance(enabled, list) or not enabled:
        raise ValueError("Missing provider privacy evidence")
    chain = data.get("auto_chain", enabled)
    if not isinstance(chain, list) or any(not isinstance(g, str) or g not in enabled for g in chain):
        raise ValueError("Unknown automatic provider group")
    if any(not isinstance(g, str) or g not in groups for g in enabled):
        raise ValueError("Missing active provider evidence")
    if approved is None:
        approved = (
            definition["privacy"]
            if definition is not None and route == ROUTE
            else route_profile(policy(), route)["privacy"]
        )
    if canonical(own) != canonical(approved["own_purpose"]):
        raise ValueError("Model privacy policy changed")
    if "group_providers" in approved:
        actual = {
            "group_providers": groups,
            "enable_groups": enabled,
            "auto_chain": chain,
        }
        if any(canonical(actual[k]) != canonical(approved[k]) for k in actual):
            raise ValueError("Provider privacy policy changed")
    elif any(canonical(provider) != canonical(approved["provider"]) for provider in groups.values()):
        raise ValueError("Provider privacy policy changed")
    ratios = data.get("group_ratio")
    if not isinstance(ratios, dict) or any(type(ratios.get(g)) not in (int, float) or ratios[g] != 0 for g in enabled):
        raise ValueError("Active provider price changed")
    for provider in groups.values():
        if (
            not isinstance(provider, dict)
            or any(
                not isinstance(provider.get(k), str) or not provider[k] for k in ("prompts", "seat_country", "transfer")
            )
            or any(provider.get(k) is not False for k in ("unsafe_third_country", "onward_unvetted"))
        ):
            raise ValueError("Provider privacy/transfer evidence changed")
    prices = {k: v for k, v in data.items() if "price" in k and not k.startswith("original_")}
    for name, value in prices.items():
        if name == "is_fixed_price":
            if type(value) is not bool:
                raise ValueError("Malformed pricing mode")
        elif type(value) not in (int, float) or value != 0:
            raise ValueError("Nonzero or malformed applicable price")
    return canonical(
        {
            "model": model,
            "is_free": True,
            "prices": prices,
            "own_purpose": own,
            "group_providers": groups,
            "enable_groups": enabled,
            "auto_chain": chain,
            "group_ratio": ratios,
        }
    )


def counter(data):
    """The actual API wraps token_usage inside success/data, unlike its example."""
    if not isinstance(data, dict) or data.get("success") is not True:
        raise ValueError("Usage query failed")
    value = data.get("data")
    if not isinstance(value, dict) or value.get("object") != "token_usage":
        raise ValueError("Malformed usage query")
    if type(value.get("total_used")) is not int or value["total_used"] < 0:
        raise ValueError("Invalid cumulative usage")
    # Never retain key name or unrelated account fields.
    return {"object": "token_usage", "total_used": value["total_used"]}


def observe(key, fetch=get, clock=time.time, route=ROUTE):
    started = clock()
    contract = contract_observation(started, fetch)
    profile = route_profile(policy(), route)
    catalog_raw = fetch(profile["catalog_url"])
    catalog = json.loads(catalog_raw)
    effective = catalog_policy(catalog, route=route, approved=profile["privacy"])
    usage = counter(json.loads(fetch(COUNTER, key)))
    ended = clock()
    if ended - started > WINDOW:
        raise ValueError("Route evidence expired during observation")
    return {
        "started": started,
        "ended": ended,
        "contract": contract,
        "route": route,
        "catalog_url": profile["catalog_url"],
        "catalog": catalog,
        "catalog_sha256": sha(catalog),
        "effective": effective,
        "counter_url": COUNTER,
        "counter": usage,
        "credential_sha256": hashlib.sha256(key.encode()).hexdigest(),
    }


def check_observation(record, contract_policy=None, route=None):
    contract = record["contract"]
    recorded = contract_policy or policy()
    route = route or record.get("route", recorded.get("route", ROUTE))
    if record.get("route", route) != route:
        raise ValueError("Observation route differs from dispatch")
    definition = recorded.get("contract_definition")
    if definition is None:
        # Old executions retain their original definition in the pre-observation.
        definition = contract
        recorded = {**recorded, "contract_definition": definition}
    profile = route_profile(recorded, route)
    if sha(definition) != recorded["contract"] or recorded["mode"] != MODE:
        raise ValueError("Recorded contract policy mismatch")
    fields = ("version", "url", "statement", "paragraph_sha256", "privacy")
    if any(contract.get(field) != definition.get(field) for field in fields):
        raise ValueError("Observation differs from its recorded policy")
    if contract["url"] != CONTRACT_URL or contract["statement"] != CONTRACT_TEXT:
        raise ValueError("Contract identity changed")
    if not 0 <= record["started"] - contract["observed_at"] <= recorded["recheck_seconds"]:
        raise ValueError("Stale contract evidence")
    if not 0 <= record["ended"] - record["started"] <= recorded["observation_window_seconds"]:
        raise ValueError("Stale route evidence")
    if record["catalog_url"] != profile["catalog_url"] or record["counter_url"] != COUNTER:
        raise ValueError("Evidence origin changed")
    if catalog_policy(record["catalog"], definition, route, profile["privacy"]) != record["effective"]:
        raise ValueError("Catalogue evidence mismatch")
    counter({"success": True, "data": record["counter"]})
    if record["catalog_sha256"] != sha(record["catalog"]):
        raise ValueError("Catalogue hash mismatch")
    if not isinstance(record["credential_sha256"], str) or len(record["credential_sha256"]) != 64:
        raise ValueError("Credential binding absent")


def check_pair(pre, post, started, received, ended, contract_policy=None, route=None):
    route = route or pre.get("route", ROUTE)
    check_observation(pre, contract_policy, route)
    check_observation(post, contract_policy, route)
    window = (contract_policy or policy())["observation_window_seconds"]
    if not 0 <= started - pre["ended"] <= window or not received <= post["started"] <= ended:
        raise ValueError("Evidence observation order changed")
    if ended - received > window:
        raise ValueError("Postcheck was not immediate")
    if pre["effective"] != post["effective"] or pre["credential_sha256"] != post["credential_sha256"]:
        raise ValueError("Price, privacy or credential changed")
    if pre["counter"] != post["counter"]:
        raise ValueError("Cumulative usage changed; attribution unavailable")


def initialize(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS quality_retry_links(
      dispatch TEXT PRIMARY KEY, previous TEXT NOT NULL, ordinal INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS quality_cooldowns(
      dispatch TEXT PRIMARY KEY, not_before REAL NOT NULL, basis TEXT NOT NULL);
    CREATE TRIGGER IF NOT EXISTS retry_link_no_update BEFORE UPDATE ON quality_retry_links
      BEGIN SELECT RAISE(ABORT,'immutable retry link'); END;
    CREATE TRIGGER IF NOT EXISTS retry_link_no_delete BEFORE DELETE ON quality_retry_links
      BEGIN SELECT RAISE(ABORT,'immutable retry link'); END;
    CREATE TRIGGER IF NOT EXISTS cooldown_no_update BEFORE UPDATE ON quality_cooldowns
      BEGIN SELECT RAISE(ABORT,'immutable cooldown'); END;
    CREATE TRIGGER IF NOT EXISTS cooldown_no_delete BEFORE DELETE ON quality_cooldowns
      BEGIN SELECT RAISE(ABORT,'immutable cooldown'); END;
    CREATE TABLE IF NOT EXISTS quality_preflights(
      id INTEGER PRIMARY KEY, request TEXT NOT NULL, observed REAL NOT NULL, reason TEXT NOT NULL);
    CREATE TRIGGER IF NOT EXISTS preflight_no_update BEFORE UPDATE ON quality_preflights
      BEGIN SELECT RAISE(ABORT,'immutable preflight event'); END;
    CREATE TRIGGER IF NOT EXISTS preflight_no_delete BEFORE DELETE ON quality_preflights
      BEGIN SELECT RAISE(ABORT,'immutable preflight event'); END;
    CREATE TABLE IF NOT EXISTS quality_dispatches(
      id TEXT PRIMARY KEY, route TEXT NOT NULL, payload TEXT NOT NULL, prompt TEXT NOT NULL,
      policy TEXT NOT NULL, pre TEXT NOT NULL, state TEXT NOT NULL, started REAL NOT NULL,
      received REAL, ended REAL, raw TEXT, metadata TEXT, post TEXT, reason TEXT);
    CREATE TRIGGER IF NOT EXISTS dispatch_final_no_update BEFORE UPDATE ON quality_dispatches
      WHEN OLD.state IN ('verified','rejected','unknown')
      BEGIN SELECT RAISE(ABORT,'immutable dispatch'); END;
    CREATE TRIGGER IF NOT EXISTS dispatch_binding_no_update BEFORE UPDATE ON quality_dispatches
      WHEN NEW.id!=OLD.id OR NEW.route!=OLD.route OR NEW.payload!=OLD.payload
        OR NEW.prompt!=OLD.prompt OR NEW.policy!=OLD.policy OR NEW.pre!=OLD.pre OR NEW.started!=OLD.started
      BEGIN SELECT RAISE(ABORT,'immutable dispatch binding'); END;
    CREATE TRIGGER IF NOT EXISTS dispatch_no_delete BEFORE DELETE ON quality_dispatches
      BEGIN SELECT RAISE(ABORT,'immutable dispatch'); END;
    """)
    db.commit()


def holds(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='quality_dispatches'").fetchone():
        return []
    return [
        {"dispatch": r["id"], "reason": r["state"]}
        for r in db.execute("SELECT id,state FROM quality_dispatches WHERE state NOT IN ('verified','rejected')")
    ]


def conflict(metadata):
    usage = metadata.get("usage") if isinstance(metadata, dict) else None
    if usage is None:
        return False  # Absence stays absent; the contract is a different evidence basis.
    if not isinstance(usage, dict):
        return True
    return ("cost" in usage and (type(usage["cost"]) not in (int, float) or usage["cost"] != 0)) or (
        "is_byok" in usage and usage["is_byok"] is not False
    )


def verified(db, raw, metadata, current_policy, expected=None):
    """Validate the actual immutable local dispatch, never a metadata assertion alone."""
    if db is None or not isinstance(metadata, dict) or not isinstance(metadata.get("contract_dispatch"), str):
        return False
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='quality_dispatches'").fetchone():
        return False
    row = db.execute("SELECT * FROM quality_dispatches WHERE id=?", (metadata["contract_dispatch"],)).fetchone()
    actual = {k: v for k, v in metadata.items() if k not in ("contract_dispatch", "quality_execution")}
    if not row or row["state"] != "verified" or row["raw"] != raw or row["metadata"] != encoded(actual):
        return False
    if conflict(actual) or row["route"] not in ROUTES or row["policy"] != encoded(current_policy):
        return False
    payload = json.loads(row["payload"])
    if not valid_attempt(db, row) or (expected is not None and expected != (payload, row["route"], row["prompt"])):
        return False
    try:
        check_pair(
            json.loads(row["pre"]),
            json.loads(row["post"]),
            row["started"],
            row["received"],
            row["ended"],
            current_policy["direct_contract"],
            row["route"],
        )
    except (ValueError, TypeError, KeyError):
        return False
    return True


def latest_attempt(db, payload, route, prompt):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='quality_dispatches'").fetchone():
        return None
    return db.execute(
        "SELECT * FROM quality_dispatches WHERE payload=? AND route=? AND prompt=? ORDER BY started DESC,rowid DESC LIMIT 1",
        (encoded(payload), route, prompt),
    ).fetchone()


def retry_link(db, identity):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='quality_retry_links'").fetchone():
        return None
    return db.execute("SELECT * FROM quality_retry_links WHERE dispatch=?", (identity,)).fetchone()


def valid_attempt(db, row):
    base = sha(
        [
            json.loads(row["payload"]),
            row["route"],
            row["prompt"],
            json.loads(row["policy"]),
        ]
    )
    link = retry_link(db, row["id"])
    if not link:
        return row["id"] == base
    if not 1 <= link["ordinal"] < MAX_ATTEMPTS:
        return False
    previous = db.execute("SELECT * FROM quality_dispatches WHERE id=?", (link["previous"],)).fetchone()
    if not previous or any(previous[k] != row[k] for k in ("payload", "route", "prompt")):
        return False
    prior_link = retry_link(db, previous["id"])
    prior_ordinal = prior_link["ordinal"] if prior_link else 0
    return (
        link["ordinal"] == prior_ordinal + 1
        and row["id"] == sha([base, previous["id"], link["ordinal"]])
        and eligible_429(db, previous)
    )


def eligible_429(db, row):
    """A known rejection with intact historical policy evidence is the only retry source."""
    if not row or row["state"] != "rejected" or row["raw"] is not None:
        return False
    try:
        metadata = json.loads(row["metadata"])
        if (
            not isinstance(metadata, dict)
            or type(metadata.get("http_status")) is not int
            or metadata.get("http_status") != 429
            or conflict(metadata)
            or metadata.get("model") not in (None, exact_model(row["route"]))
        ):
            return False
        if not valid_attempt(db, row):
            return False
        check_pair(
            json.loads(row["pre"]),
            json.loads(row["post"]),
            row["started"],
            row["received"],
            row["ended"],
            json.loads(row["policy"])["direct_contract"],
            row["route"],
        )
        return True
    except (KeyError, TypeError, ValueError):
        return False


def retry_deadline(metadata, ended, ordinal, identity):
    headers = metadata.get("response_headers") or {}
    headers = headers if isinstance(headers, dict) else {}
    value = headers.get("retry-after", "")
    value = value.strip() if isinstance(value, str) else ""
    delay, basis = None, "fallback"
    if value.isascii() and value.isdigit():
        try:
            delay = float(value)
        except (ValueError, OverflowError):
            delay = math.inf
        basis = "retry-after-seconds"
    elif value:
        try:
            target = parsedate_to_datetime(value)
            target = target.replace(tzinfo=timezone.utc) if target.tzinfo is None else target
            reference = ended
            try:
                server_date = headers.get("date", "")
                if not isinstance(server_date, str):
                    raise ValueError("Date header is not text")
                server = parsedate_to_datetime(server_date)
                if server.tzinfo is None:
                    server = server.replace(tzinfo=timezone.utc)
                reference = server.timestamp()
            except (ValueError, TypeError, OverflowError):
                pass  # Optional Date cannot invalidate a valid Retry-After date.
            delay = max(0, target.timestamp() - reference)
            basis = "retry-after-date"
        except (ValueError, TypeError, OverflowError):
            pass
    if delay is None:
        delay = min(900, FALLBACK_DELAY * 2**ordinal)
    jitter = 1 + int(identity[:4], 16) % 6
    return ended + delay + jitter, basis


def cooldown(db, row):
    existing = db.execute("SELECT * FROM quality_cooldowns WHERE dispatch=?", (row["id"],)).fetchone()
    link = retry_link(db, row["id"])
    deadline, basis = retry_deadline(
        json.loads(row["metadata"]),
        row["ended"],
        link["ordinal"] if link else 0,
        row["id"],
    )
    if existing:
        # Preserve old evidence while enforcing corrected valid server advice.
        return (
            max(existing["not_before"], deadline)
            if basis in ("retry-after-seconds", "retry-after-date")
            else existing["not_before"]
        )
    with db:
        db.execute("INSERT INTO quality_cooldowns VALUES(?,?,?)", (row["id"], deadline, basis))
    return deadline


class RetryDeferred(ValueError):
    """Known429 is waiting; submitted distinguishes a new POST from a local cooldown."""

    def __init__(self, row, retry_at, submitted=False):
        super().__init__("Known429 cooldown")
        self.retry_at = retry_at
        self.submitted = submitted
        self.raw = row["raw"]
        self.metadata = {**json.loads(row["metadata"]), "contract_dispatch": row["id"]}


class RetryExhausted(RetryDeferred):
    """All three immutable POST attempts ended in known429 rejection."""


def retry_error(db, row, submitted=False):
    link = retry_link(db, row["id"])
    error_type = RetryExhausted if link and link["ordinal"] + 1 >= MAX_ATTEMPTS else RetryDeferred
    return error_type(row, cooldown(db, row), submitted)


def safe_retry_error(db, error, payload=None, route=None, prompt=None):
    if not isinstance(error, RetryDeferred):
        return False
    row = db.execute(
        "SELECT * FROM quality_dispatches WHERE id=?",
        (error.metadata.get("contract_dispatch"),),
    ).fetchone()
    if not eligible_429(db, row):
        return False
    return payload is None or (row["payload"] == encoded(payload) and row["route"] == route and row["prompt"] == prompt)


class PreSubmissionError(ValueError):
    """A durable preflight event proves this dispatch did not reach POST."""


def preflight_failure(db, identity, reason, observed):
    with db:
        db.execute(
            "INSERT INTO quality_preflights(request,observed,reason) VALUES(?,?,?)",
            (identity, observed, reason),
        )
    raise PreSubmissionError(reason)


def preflight_safe(db, payload, route, prompt, recorded_policy, started):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='quality_preflights'").fetchone():
        return False
    identity = sha([payload, route, prompt, recorded_policy])
    claimed = db.execute(
        "SELECT 1 FROM quality_dispatches WHERE payload=? AND route=? AND prompt=? AND started>=?",
        (encoded(payload), route, prompt, started),
    ).fetchone()
    event = db.execute(
        "SELECT 1 FROM quality_preflights WHERE request=? AND observed>=?",
        (identity, started),
    ).fetchone()
    return not claimed and bool(event)


def recorded_response(db, payload, route, prompt, recorded_policy):
    """Recover only exact verified durable evidence; this never submits a request."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='quality_dispatches'").fetchone():
        return None
    row = latest_attempt(db, payload, route, prompt)
    if not row or row["state"] != "verified":
        return None
    metadata = {
        **(json.loads(row["metadata"] or "{}") or {}),
        "contract_dispatch": row["id"],
    }
    if not verified(db, row["raw"], metadata, recorded_policy, (payload, route, prompt)):
        return None
    return row["raw"], metadata


def dispatch(
    db,
    payload,
    route,
    prompt,
    current_policy,
    *,
    observe_fn=None,
    key_fn=credentials,
    send=transport.raw_request,
    clock=time.time,
):
    """One explicit submission with durable claim, raw response and independent postcheck."""
    model = exact_model(route)
    if observe_fn is None:

        def observe_fn(key):
            return observe(key, route=route)

    initialize(db)
    base = sha([payload, route, prompt, current_policy])
    existing = latest_attempt(db, payload, route, prompt)
    if holds(db):
        raise RuntimeError("Unreconciled direct dispatch")
    previous, ordinal, identity = None, 0, base
    if existing:
        metadata = {
            **(json.loads(existing["metadata"] or "{}") or {}),
            "contract_dispatch": existing["id"],
        }
        if verified(db, existing["raw"], metadata, current_policy, (payload, route, prompt)):
            return transport.checked_completion(existing["raw"], metadata)
        if eligible_429(db, existing):
            pending = retry_error(db, existing)
            if isinstance(pending, RetryExhausted) or clock() < pending.retry_at:
                raise pending
            previous = existing["id"]
            link = retry_link(db, previous)
            ordinal = (link["ordinal"] if link else 0) + 1
            identity = sha([base, previous, ordinal])
        elif existing["state"] != "verified" or existing["policy"] == encoded(current_policy):
            raise RuntimeError("Existing dispatch cannot be resubmitted")
    try:
        key = key_fn()
        fingerprint = hashlib.sha256(key.encode()).hexdigest()
        for limited in db.execute(
            "SELECT * FROM quality_dispatches WHERE route=? AND state='rejected'",
            (route,),
        ):
            if (
                eligible_429(db, limited)
                and json.loads(limited["pre"])["credential_sha256"] == fingerprint
                and clock() < cooldown(db, limited)
            ):
                raise RetryDeferred(limited, cooldown(db, limited))
        pre = observe_fn(key)
        check_observation(pre, current_policy["direct_contract"], route)
    except RetryDeferred:
        raise
    except Exception as error:
        preflight_failure(db, base, "precheck-" + type(error).__name__, clock())
    started = clock()
    if not 0 <= started - pre["ended"] <= WINDOW:
        preflight_failure(db, base, "precheck-expired", clock())
    with db:
        db.execute(
            "INSERT INTO quality_dispatches VALUES(?,?,?,?,?,?,'dispatching',?,NULL,NULL,NULL,NULL,NULL,NULL)",
            (
                identity,
                route,
                encoded(payload),
                prompt,
                encoded(current_policy),
                encoded(pre),
                started,
            ),
        )
        if previous is not None:
            db.execute(
                "INSERT INTO quality_retry_links VALUES(?,?,?)",
                (identity, previous, ordinal),
            )
    failure = None
    try:
        result = send(encoded(payload), model, prompt, BASE, key)
        raw, metadata = str(result), result.metadata
    except BaseException as error:
        failure = error
        raw, metadata = getattr(error, "raw", None), getattr(error, "metadata", None)
    # An interruption here may lose raw text; the durable claim still blocks resubmission.
    received = clock()
    with db:
        db.execute(
            "UPDATE quality_dispatches SET raw=?,metadata=?,received=?,state='received' WHERE id=?",
            (raw, encoded(metadata), received, identity),
        )
    post = None
    try:
        post = observe_fn(key)
        ended = clock()
        check_pair(
            pre,
            post,
            started,
            received,
            ended,
            current_policy["direct_contract"],
            route,
        )
        if isinstance(metadata, dict) and metadata.get("model") not in (None, model):
            raise RuntimeError("Returned model differs from exact direct pin")
        if conflict(metadata):
            raise RuntimeError("Explicit billing evidence contradicts free contract")
        state = "unknown" if failure and not isinstance(failure, ValueError) else "verified"
        if failure and isinstance(failure, ValueError) and (metadata or {}).get("http_status") != 200:
            state = "rejected"
        reason = type(failure).__name__ if failure else None
    except BaseException as error:
        state, reason = "unknown", "Postcheck:" + type(error).__name__
        ended = clock()
        if failure is None:
            failure = error
    with db:
        db.execute(
            "UPDATE quality_dispatches SET post=?,ended=?,state=?,reason=? WHERE id=?",
            (encoded(post), ended, state, reason, identity),
        )
    attached = {**(metadata or {}), "contract_dispatch": identity}
    if state == "unknown":
        error = (
            failure
            if isinstance(failure, (KeyboardInterrupt, SystemExit))
            else RuntimeError("Direct outcome or policy evidence unknown")
        )
        error.raw, error.metadata = raw, attached
        raise error
    row = db.execute("SELECT * FROM quality_dispatches WHERE id=?", (identity,)).fetchone()
    if eligible_429(db, row):
        raise retry_error(db, row, submitted=True)
    if failure:
        failure.raw, failure.metadata = raw, attached
        raise failure
    return transport.checked_completion(raw, attached)


if __name__ == "__main__":
    try:
        print(json.dumps(get_once(**json.load(sys.stdin))))
    except Exception:
        sys.exit(1)  # No credentials, account fields or provider bodies on stderr.
