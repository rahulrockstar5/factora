"""Request handling shared by the Vercel function (api/check.py) and the local server (server.py)."""
import json

from . import engine
from .service import check

MAX_BODY = 20_000


def handle_check(body):
    """Take a raw request body, return (status_code, dict)."""
    if len(body) > MAX_BODY:
        return 413, {"error": "too_large", "message": "That request is too large."}
    try:
        data = json.loads(body or b"{}")
        if not isinstance(data, dict):
            raise ValueError
    except ValueError:
        return 400, {"error": "bad_json", "message": "Send a JSON body like {\"url\": \"...\", \"text\": \"...\"}."}
    url = data.get("url")
    text = data.get("text", "")
    if not isinstance(url, str) or not isinstance(text, str):
        return 400, {"error": "bad_input", "message": "url and text must be strings."}
    try:
        result = check(url, text)
    except Exception:  # never leak a stack trace to the browser
        return 500, {"error": "server_error", "message": "Something went wrong while checking this link."}
    if "error" in result:
        return 422, result
    return 200, result


def health():
    return {"ok": True, "engine": engine.engine_name()}
