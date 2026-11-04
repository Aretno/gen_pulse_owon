"""Servidor HTTP/JSON para controlar el generador remotamente.

    python -m owon_dge.server [--host 127.0.0.1] [--port 8750] [--token T]

Por seguridad escucha solo en localhost; para acceso remoto usa un túnel SSH
(como senda con la HV) o --host 0.0.0.0 junto con --token.

Endpoints:
  GET  /api/idn
  GET  /api/channels/<n>            estado completo
  PUT  /api/channels/<n>            {"freq":..,"width":..,"high":..,...}
  POST /api/channels/<n>/output     {"on": true}
  POST /api/align                   sincroniza la fase de CH1 y CH2
  GET  /api/waveform                memoria de edición (8192 pts, solo modo TMC)
  POST /api/raw                     {"cmd": "SOUR1:FREQ?"}
"""
from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .device import Dge2070
from .transport import DeviceError

CH = re.compile(r"^/api/channels/([12])(/output)?$")


def make_handler(dev: Dge2070, token: str | None):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def _run(self, method):
            if token and self.headers.get("Authorization") != f"Bearer {token}":
                return self._send(401, {"error": "token inválido"})
            try:
                path = self.path.split("?")[0]
                m = CH.match(path)
                if method == "GET" and path == "/api/idn":
                    return self._send(200, {"idn": dev.idn()})
                if method == "GET" and m and not m.group(2):
                    return self._send(200, dev.get_state(int(m.group(1))))
                if method == "PUT" and m and not m.group(2):
                    dev.set_pulse(int(m.group(1)), **self._body())
                    return self._send(200, dev.get_state(int(m.group(1))))
                if method == "POST" and m and m.group(2):
                    dev.set_output(int(m.group(1)), bool(self._body()["on"]))
                    return self._send(200, dev.get_state(int(m.group(1))))
                if method == "POST" and path == "/api/align":
                    return self._send(200, {"method": dev.align_phase()})
                if method == "GET" and path == "/api/waveform":
                    return self._send(200, {"points": dev.read_waveform()})
                if method == "POST" and path == "/api/raw":
                    b = self._body()
                    return self._send(200, {"reply": dev.raw(b["cmd"], b.get("query"))})
                self._send(404, {"error": "no encontrado"})
            except (ValueError, KeyError) as e:
                self._send(400, {"error": str(e)})
            except DeviceError as e:
                self._send(502, {"error": str(e)})
            except Exception as e:  # noqa: BLE001
                self._send(500, {"error": f"{type(e).__name__}: {e}"})

        def do_GET(self): self._run("GET")
        def do_PUT(self): self._run("PUT")
        def do_POST(self): self._run("POST")
        def log_message(self, *a): pass

    return H


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8750)
    ap.add_argument("--token")
    a = ap.parse_args()
    if a.host not in ("127.0.0.1", "localhost") and not a.token:
        ap.error("--token es obligatorio si escuchas fuera de localhost")
    dev = Dge2070()
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(dev, a.token))
    print(f"Servidor DGE2070 en http://{a.host}:{a.port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        dev.close()


if __name__ == "__main__":
    main()
