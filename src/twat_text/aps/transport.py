# this_file: src/twat_text/aps/transport.py
"""One raw FreeLLM POST per attempt, with a killable whole-request deadline."""

import http.client
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit

DEADLINE = 300
DIAGNOSTIC_BYTES = 64 * 1024


class ValidationError(ValueError):
    """A controlled local validation message, never a provider exception body."""


class Completion(str):
    """Text accepted by existing validators, plus provider reconciliation evidence."""

    def __new__(cls, content, metadata):
        result = super().__new__(cls, content)
        result.metadata = metadata
        return result


def settings():
    """Reuse the installed bridge's env-file, credential and desktop-port discovery."""
    directory = os.environ.get("TWAT_TEXT_FREELLM_PATH") or str(Path.home() / "Developer/llm2/2611/askpoe2")
    sys.path.insert(0, directory)
    try:
        freellm = importlib.import_module("freellm")
        key = freellm.setting("FREELLMAPI_API_KEY", "FREE_LLMAPI_API_KEY")
        if not key:
            raise ValueError("Set FREELLMAPI_API_KEY before generation")
        return freellm.base_url(), key
    except ImportError:
        raise ValueError("Use the documented Python runtime containing the FreeLLM dependencies") from None
    finally:
        sys.path.remove(directory)


def require_free_route(route):
    if route.startswith("poe:"):
        raise ValueError("Poe submission disabled until its transport has verified no hidden retries")
    model = route.removeprefix("freellm:")
    if model.startswith("openrouter:"):
        concrete = model.removeprefix("openrouter:").removesuffix(":free")
        if (
            not model.endswith(":free")
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*", concrete)
            or any(part.startswith(("auto", "fusion")) for part in concrete.split("/"))
        ):
            raise ValueError("Use a concrete provider-qualified OpenRouter :free model")
        return model
    if model.startswith("unorouter:"):
        concrete = model.removeprefix("unorouter:").removesuffix(":free")
        if (
            not model.endswith(":free")
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", concrete)
            or concrete.lower().startswith(("auto", "fusion", "default", "random"))
        ):
            raise ValueError("Use a concrete provider-qualified UnoRouter :free model")
        return model
    concrete = model.removeprefix("nvidia:")
    if not concrete or ":" in concrete or concrete.startswith(("auto", "fusion", "claude")):
        raise ValueError("Use a concrete FreeLLM model")
    return model


def request(text, route, prompt, timeout=DEADLINE):
    """Return raw completion text; 5xx, deadline and malformed envelopes are unknown."""
    model = require_free_route(route)
    url, key = settings()
    return raw_request(text, model, prompt, url, key, timeout)


def sanitize(value, key):
    """Remove the configured credential, including mixed JSON character escapes."""
    if isinstance(value, dict):
        return {sanitize(k, key): sanitize(v, key) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v, key) for v in value]
    if not isinstance(value, str) or not key:
        return value
    return redact_text(value, key)


def redact_text(text, key):
    """Finite-state matching: O(text length * key length), never backtracking paths."""
    if "\\" not in text:
        return text.replace(key, "[REDACTED]")
    variants = []
    for char in key:
        escaped = json.dumps(char, ensure_ascii=True)[1:-1]
        unicode_escape = "\\u" + f"{ord(char):04x}" if ord(char) <= 65535 else escaped
        forms = {(char, False), (escaped, False), (unicode_escape.lower(), True)}
        if char == "/":
            forms.add(("\\/", False))
        variants.append(forms)
    pending, spans = {}, []
    for position in range(len(text) + 1):
        states = pending.pop(position, {})
        if len(key) in states:
            start = states.pop(len(key))
            while spans and spans[-1][1] >= start:
                start = min(start, spans.pop()[0])
            spans.append((start, position))
        if position == len(text):
            break
        states[0] = position
        for progress, start in states.items():
            for form, folded in variants[progress]:
                end = position + len(form)
                if text[position] != form[0]:
                    continue
                matches = text[position:end].lower() == form if folded else text.startswith(form, position)
                if matches:
                    following = pending.setdefault(end, {})
                    # Equivalent states merge; an earlier start covers later matches.
                    following[progress + 1] = min(following.get(progress + 1, start), start)
    pieces, previous = [], 0
    for start, end in spans:
        pieces.extend((text[previous:start], "[REDACTED]"))
        previous = end
    pieces.append(text[previous:])
    return "".join(pieces)


def malformed_diagnostic(body, key):
    """Bound private diagnostic text; the digest describes the complete sanitized body."""
    sanitized = sanitize(body, key)
    data = sanitized.encode("utf-8")
    return {
        "text": data[:DIAGNOSTIC_BYTES].decode("utf-8", errors="ignore"),
        "encoding": "utf-8",
        "sanitized_bytes": len(data),
        "sanitized_sha256": hashlib.sha256(data).hexdigest(),
        "truncated": len(data) > DIAGNOSTIC_BYTES,
        "credential_redacted": sanitized != body,
    }


def raw_request(text, model, prompt, url, key, timeout=DEADLINE):
    """One explicit endpoint/model submission; credentials never enter evidence."""
    started = time.monotonic()
    payload = {
        "url": url,
        "key": key,
        "body": {
            "model": model,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ],
            "max_tokens": 8192,
            "temperature": 0.1,
            "top_p": 1.0,
        },
    }
    remaining = timeout - (time.monotonic() - started)
    if remaining <= 0:
        raise TimeoutError("Request deadline expired before submission")
    try:
        result = subprocess.run(
            [sys.executable, "-m", __spec__.name],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=remaining,
            check=False,
        )
    except subprocess.TimeoutExpired:
        # subprocess.run kills and reaps this worker; it never launches children.
        raise TimeoutError("Request deadline expired; completion outcome unknown") from None
    if result.returncode:
        raise RuntimeError("Transport failed; completion outcome unknown")
    try:
        response = json.loads(result.stdout)
        status = response["status"]
    except (ValueError, KeyError, TypeError):
        raise RuntimeError("Malformed provider response; completion outcome unknown") from None
    request_id = response.get("request_id")
    if isinstance(request_id, str) and key:
        request_id = request_id.replace(key, "[REDACTED]")
    metadata = {"http_status": status, "request_id": request_id}
    if response.get("headers"):
        metadata["response_headers"] = {
            name: value.replace(key, "[REDACTED]") if key else value for name, value in response["headers"].items()
        }
    if status != 200:
        body = response.get("body", "")
        metadata["error_response"] = body.replace(key, "[REDACTED]") if key else body
    try:
        data = json.loads(response["body"])
    except (ValueError, KeyError, TypeError):
        data = None
    if isinstance(data, dict):
        metadata.update({key: data.get(key) for key in ("id", "model", "usage")})
    check_status(metadata)
    try:
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError):
            raise RuntimeError("Malformed completion envelope; completion outcome unknown") from None
        metadata["finish_reason"] = finish_reason
        return checked_completion(content, metadata)
    except RuntimeError as failure:
        failure.metadata = {
            **sanitize(metadata, key),
            "malformed_response": malformed_diagnostic(response["body"], key),
        }
        raw = getattr(failure, "raw", None)
        if isinstance(raw, str):
            failure.raw = sanitize(raw, key)
            failure.metadata["completion_credential_redacted"] = failure.raw != raw
        raise


def check_status(metadata):
    """Classify recorded and HTTP outcomes identically, including missing status."""
    status = metadata.get("http_status") if isinstance(metadata, dict) else None
    if type(status) is int and status == 200:
        return
    kind = ValueError if type(status) is int and status in (400, 401, 403, 404, 422, 429) else RuntimeError
    failure = kind("Provider rejected request" if kind is ValueError else "Provider completion outcome unknown")
    failure.metadata = metadata
    raise failure


def checked_completion(content, metadata):
    """Do not let imported or replayed text bypass terminal completion checks."""
    try:
        check_status(metadata)
        finish = metadata.get("finish_reason")
        if (
            not isinstance(content, str)
            or not isinstance(finish, str)
            or finish not in ("stop", "length", "content_filter", "tool_calls", "function_call")
        ):
            raise RuntimeError("Malformed completion fields; completion outcome unknown")
        if finish != "stop" or not content.strip():
            raise ValidationError(
                "Incomplete provider completion: " + finish if finish != "stop" else "Empty provider completion"
            )
    except (ValueError, RuntimeError) as failure:
        failure.metadata = metadata
        failure.raw = content if isinstance(content, str) else None
        raise
    return Completion(content, metadata)


def post(payload):
    """Send exactly one HTTP request; http.client has no retry or redirect handling."""
    url = urlsplit(payload["url"].rstrip("/") + "/chat/completions")
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
        raise ValueError("Invalid FreeLLM endpoint")
    connection_type = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(url.hostname, url.port, timeout=DEADLINE)
    try:
        connection.request(
            "POST",
            url.path + ("?" + url.query if url.query else ""),
            body=json.dumps(payload["body"]).encode(),
            headers={
                "Authorization": "Bearer " + payload["key"],
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        return {
            "status": response.status,
            "request_id": response.getheader("x-request-id"),
            "body": response.read().decode("utf-8"),
            "headers": {
                name: response.getheader(name)
                for name in ("retry-after", "date", "content-type", "x-request-id")
                if response.getheader(name) is not None
            },
        }
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        print(json.dumps(post(json.load(sys.stdin))))
    except Exception:
        # Never expose credentials, source, or provider error bodies on stderr.
        sys.exit(1)
