"""HTTP helpers: resilient _get/_post, Ollama streaming, and AnythingLLM helpers."""

import json
import time
import urllib.request
import urllib.error
import mimetypes
import uuid
from typing import Optional, Iterator
from pathlib import Path

from .config import OLLAMA_BASE, ANYTHINGLLM_BASE
from .auth import _auth_anything, _auth_browser, _auth_ollama


def _post(url: str, payload: Optional[dict], headers: Optional[dict] = None, timeout: int = 30, retries: int = 3) -> dict:
    body = (json.dumps(payload).encode() if payload is not None else None)
    req_headers = {"Content-Type": "application/json", **(headers or {})}
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=body, headers=req_headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if attempt == retries:
                raise
            time.sleep(2 ** (attempt - 1))
        except Exception:
            if attempt == retries:
                raise
            time.sleep(2 ** (attempt - 1))
    return {}


def _get(url: str, headers: Optional[dict] = None, timeout: int = 5, retries: int = 3) -> dict:
    req_headers = headers or {}
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, headers=req_headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError:
            if attempt == retries:
                raise
            time.sleep(2 ** (attempt - 1))
        except Exception:
            if attempt == retries:
                raise
            time.sleep(2 ** (attempt - 1))
    return {}


def _ollama_post(payload: dict, timeout: int = 120) -> dict:
    return _post(f"{OLLAMA_BASE.rstrip('/')}/api/chat", payload, headers=_auth_ollama(), timeout=timeout)


def _ollama_stream(payload: dict, timeout: int = 300) -> Iterator[str]:
    payload = dict(payload)
    payload["stream"] = True
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", **_auth_ollama()}
    req = urllib.request.Request(
        f"{OLLAMA_BASE.rstrip('/')}/api/chat",
        data=body,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        buffer = b""
        for chunk in r:
            if not chunk:
                continue
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                if line.startswith(b"data:"):
                    line = line[len(b"data:"):].strip()
                try:
                    text = line.decode("utf-8", errors="replace")
                    parsed = json.loads(text)
                except Exception:
                    continue
                delta = parsed.get("message", {}).get("content", "")
                if delta:
                    yield delta
                if parsed.get("done"):
                    return


def _alm_request(method: str, path: str, body: Optional[dict] = None, timeout: int = 15) -> dict:
    """Call AnythingLLM admin API under /api/v1/ to keep endpoints consistent.

    Example: _alm_request("GET", "workspace/my-ws") -> GET http://host:3001/api/v1/workspace/my-ws
    """
    base = ANYTHINGLLM_BASE.rstrip('/')
    url = f"{base}/api/v1/{path.lstrip('/')}"
    data = (json.dumps(body).encode() if body is not None else None)
    headers = {"Content-Type": "application/json", "Accept": "application/json", **_auth_anything()}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def upload_file(url: str, filepath: str, field_name: str = "file", extra_fields: Optional[dict] = None, timeout: int = 60) -> dict:
    """Upload a single file with a multipart/form-data POST. Returns parsed JSON on success."""
    path = filepath
    boundary = f"----RouterBoundary{uuid.uuid4().hex[:8]}"
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    lines = []
    if extra_fields:
        for k, v in (extra_fields or {}).items():
            lines.append(f"--{boundary}")
            lines.append(f'Content-Disposition: form-data; name="{k}"')
            lines.append("")
            lines.append(str(v))
    lines.append(f"--{boundary}")
    lines.append(f'Content-Disposition: form-data; name="{field_name}"; filename="{Path(path).name}"')
    lines.append(f"Content-Type: {mime}")
    lines.append("")
    body = "\r\n".join(lines).encode() + b"\r\n" + Path(path).read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", **_auth_anything()}
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}
