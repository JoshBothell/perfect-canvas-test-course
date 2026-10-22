"""Minimal Canvas REST client: auth, pagination, retries, file-upload flow, masquerading."""

from __future__ import annotations

import time
from pathlib import Path

import requests


class CanvasError(Exception):
    def __init__(self, resp: requests.Response):
        self.status = resp.status_code
        try:
            body = resp.json()
        except ValueError:
            body = resp.text[:500]
        super().__init__(f"{resp.request.method} {resp.url} -> {resp.status_code}: {body}")


class Canvas:
    def __init__(self, base_url: str, token: str, verbose: bool = False):
        self.base = base_url.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})
        self.verbose = verbose

    # ---------------------------------------------------------------- core
    def request(self, method: str, path: str, *, as_user: int | None = None, **kw):
        url = path if path.startswith("http") else f"{self.base}/api/v1/{path.lstrip('/')}"
        if as_user is not None:
            kw.setdefault("params", {})["as_user_id"] = as_user
        for attempt in range(6):
            resp = self.s.request(method, url, timeout=120, **kw)
            if resp.status_code == 429 or resp.status_code >= 500:  # throttled / transient
                time.sleep(min(2 ** attempt, 30))
                continue
            break
        if self.verbose:
            print(f"  {method} {resp.url} -> {resp.status_code}")
        if resp.status_code >= 400:
            raise CanvasError(resp)
        return resp.json() if resp.content and "json" in resp.headers.get("Content-Type", "") else None

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, data=None, **kw):
        return self.request("POST", path, data=data, **kw)

    def put(self, path, data=None, **kw):
        return self.request("PUT", path, data=data, **kw)

    def delete(self, path, **kw):
        return self.request("DELETE", path, **kw)

    def paginate(self, path: str, params: dict | None = None, **kw) -> list:
        params = {"per_page": 100, **(params or {})}
        url = f"{self.base}/api/v1/{path.lstrip('/')}"
        out = []
        while url:
            resp = self.s.get(url, params=params, timeout=120, **kw)
            if resp.status_code >= 400:
                raise CanvasError(resp)
            data = resp.json()
            out.extend(data if isinstance(data, list) else [data])
            url = resp.links.get("next", {}).get("url")
            params = None  # next link already carries the query
        return out

    def get_or_none(self, path, **kw):
        try:
            return self.get(path, **kw)
        except CanvasError as e:
            if e.status in (404, 401):
                return None
            raise

    # -------------------------------------------------------------- uploads
    def upload(self, preflight: dict, file_path: Path) -> dict:
        """Second + third leg of Canvas's file upload flow, given the preflight response."""
        with open(file_path, "rb") as f:
            resp = requests.post(preflight["upload_url"], data=preflight["upload_params"],
                                 files={"file": (file_path.name, f)}, allow_redirects=False, timeout=600)
        if resp.status_code in (301, 302, 303):
            return self.request("GET", resp.headers["Location"])
        if resp.status_code >= 400:
            raise CanvasError(resp)
        return resp.json() if resp.content else {}

    def wait_progress(self, progress_url: str, timeout: int = 1800) -> dict:
        start = time.time()
        last = None
        while time.time() - start < timeout:
            p = self.request("GET", progress_url)
            if p.get("workflow_state") != last:
                last = p.get("workflow_state")
            pct = p.get("completion")
            print(f"  migration: {last} {pct if pct is not None else ''}%", end="\r", flush=True)
            if last in ("completed", "failed"):
                print()
                return p
            time.sleep(3)
        raise TimeoutError(f"content migration did not finish within {timeout}s")
