"""Tiny NetBox REST client (standard library only) shared by the demo scripts.

Reads NETBOX_URL (default http://127.0.0.1:8001, the documented port-forward) and the
Authorization header from the netbox-api-token Secret via kubectl, so the token never has
to be copied into a shell variable, a file or the terminal.
"""
import base64
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request

URL = os.environ.get("NETBOX_URL", "http://127.0.0.1:8001").rstrip("/")


def _auth_header() -> str:
    out = subprocess.run(
        ["kubectl", "-n", "netbox", "get", "secret", "netbox-api-token",
         "-o", "jsonpath={.data.authorization}"],
        check=True, capture_output=True, text=True).stdout
    return base64.b64decode(out).decode()


_AUTH = _auth_header()


def call(method: str, path: str, body=None):
    req = urllib.request.Request(
        f"{URL}/api/{path.lstrip('/')}",
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": _AUTH, "Content-Type": "application/json",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def get_one(path: str, **params):
    """Return the single object matching the filter, or None."""
    q = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    status, data = call("GET", f"{path}?{q}")
    if status != 200:
        raise RuntimeError(f"GET {path} -> {status}: {data}")
    if data["count"] > 1:
        raise RuntimeError(f"GET {path}?{q} matched {data['count']} objects")
    return data["results"][0] if data["count"] else None


def ensure(path: str, match: dict, body: dict):
    """Get-or-create: returns (object, created)."""
    found = get_one(path, **match)
    if found:
        return found, False
    status, data = call("POST", path, body)
    if status != 201:
        raise RuntimeError(f"POST {path} -> {status}: {data}")
    return data, True
