"""Cliente remoto con la misma interfaz que Dge2070 (solo stdlib)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from .transport import DeviceError


class RemoteDge2070:
    def __init__(self, url="http://127.0.0.1:8750", token=None, timeout=5.0):
        self.url, self.token, self.timeout = url.rstrip("/"), token, timeout

    def _call(self, method, path, body=None):
        req = urllib.request.Request(
            self.url + path, method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            try:
                msg = json.load(e).get("error", e.reason)
            except Exception:
                msg = e.reason
            raise DeviceError(msg)
        except OSError as e:
            raise DeviceError(f"Sin conexión con el servidor: {e}")

    def idn(self): return self._call("GET", "/api/idn")["idn"]
    def get_state(self, ch=1): return self._call("GET", f"/api/channels/{ch}")
    def set_pulse(self, ch=1, **kw): self._call("PUT", f"/api/channels/{ch}", kw)
    def set_output(self, ch, on): self._call("POST", f"/api/channels/{ch}/output", {"on": on})
    def raw(self, cmd, query=None): return self._call("POST", "/api/raw", {"cmd": cmd, "query": query})["reply"]
    def close(self): pass
