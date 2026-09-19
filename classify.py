"""LLM path for mail that rules.py cannot handle. Uses Gemini REST, no extra packages.""" 

import json
import time
import urllib.error
import urllib.request

import config
import safety 

_calls = 0
_last_call = 0.0
_reachable = None
_failures = 0
_off = False
_started = 0.0
PROBE_TIMEOUT = 3 

def api_reachable():
    """Quick HTTPS check. Does not send a generateContent request."""
    global _reachable
    if _reachable is not None:
        return _reachable
    url = "https://generativelanguage.googleapis.com/"
    try:
        urllib.request.urlopen(url, timeout=PROBE_TIMEOUT)
        _reachable = True
    except urllib.error.HTTPError:
        _reachable = True
    except Exception as err:
        _reachable = False
        print(
            "Gemini API not reachable (office network / firewall?). "
            "Skipping LLM calls. Reason:",
            type(err).__name__,
            str(err)[:160],
        )
        return _reachable

    print("Gemini API reachable.")
    return _reachable 

def call_gemini(prompt):
    """One generateContent call. On failure or time budget, skip and finish the run."""
    global _calls, _last_call, _failures, _off, _started

    if _off or not config.ready():
        return None
    if not api_reachable():
        return None
    if config.MAX_CALLS and _calls >= config.MAX_CALLS:
        return None
    if _started == 0.0:
        _started = time.time()
    if config.MAX_SECONDS and (time.time() - _started) >= config.MAX_SECONDS:
        _off = True
        print(
            f"Gemini time budget ({config.MAX_SECONDS:g}s) reached. "
            "Remaining messages keep the rules/stub disposition."
        )
        return None 

    wait = config.SLEEP_SECONDS - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        + config.MODEL
        + ":generateContent?key="
        + config.API_KEY
    )
    payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    attempts = 1 + config.RETRIES
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            _last_call = time.time()
            _calls += 1
            _failures = 0
            parts = data["candidates"][0]["content"]["parts"]
            text = ""
            for p in parts:
                text += p.get("text", "")
            return text
        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8", errors="replace")
            _last_call = time.time()
            can_retry = err.code in (429, 503) and attempt + 1 < attempts
            if can_retry:
                print(f"Gemini HTTP {err.code}; retrying once in {config.RETRY_WAIT:g}s")
                time.sleep(config.RETRY_WAIT)
                continue
            print("Gemini HTTP", err.code, body[:200])
            break
        except Exception as err:
            print("Gemini error:", type(err).__name__, str(err)[:200])
            _last_call = time.time()
            break
    _failures += 1
    if _failures >= config.MAX_FAILURES:
        _off = True
        print("Gemini disabled for the rest of this run: quota or busy model.")
        print("Remaining messages keep the rules/stub disposition.")
    else:
        print("Skipping this message, moving to the next one.")
    return None

def classify_one(msg):
    """Return disposition + reason, or None to keep the R1 stub."""
    wrapped = safety.wrap_untrusted(msg)
    prompt = (
        wrapped
        + "\n\nChoose exactly one disposition: reply, archive, defer, delegate, escalate.\n"
        "Reply with two lines:\nDISPOSITION: <word>\nREASON: <one sentence>\n"
    )
    text = call_gemini(prompt)
    if not text:
        return None

    disposition = "escalate"
    reason = text.strip().replace("\n", " ")[:200]
    for line in text.splitlines():
        line = line.strip()
        if line.upper().startswith("DISPOSITION:"):
            word = line.split(":", 1)[1].strip().lower()
            if word in ("reply", "archive", "defer", "delegate", "escalate"):
                disposition = word
        if line.upper().startswith("REASON:"):
            reason = line.split(":", 1)[1].strip()
    return {
        "disposition": disposition,
        "reason": reason,
        "via": "llm",
        "rule_id": "gemini",
    }