from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from pi_boat_core.config import LocalWebConfig


EngineProvider = Callable[[], dict[str, Any]]


class LocalWebServer:
    def __init__(self, config: LocalWebConfig, engine_provider: EngineProvider) -> None:
        self.config = config
        self.engine_provider = engine_provider
        self._server: asyncio.Server | None = None

    async def run_until_stopped(self, stop: asyncio.Event) -> None:
        self._server = await asyncio.start_server(self._handle_connection, self.config.host, self.config.port)
        async with self._server:
            await stop.wait()
            self._server.close()
            await self._server.wait_closed()

    async def _handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=2)
            if not request_line:
                return

            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=2)
                if line in (b"\r\n", b"\n", b""):
                    break

            method, path = _parse_request_line(request_line)
            if method != "GET":
                _write_response(writer, 405, "text/plain; charset=utf-8", b"Method not allowed")
            elif path == "/api/engine":
                body = json.dumps(self.engine_provider(), separators=(",", ":")).encode("utf-8")
                _write_response(writer, 200, "application/json; charset=utf-8", body)
            elif path == "/" or path == "/engine":
                _write_response(writer, 200, "text/html; charset=utf-8", ENGINE_PAGE.encode("utf-8"))
            else:
                _write_response(writer, 404, "text/plain; charset=utf-8", b"Not found")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


def _parse_request_line(request_line: bytes) -> tuple[str, str]:
    try:
        method, raw_path, _version = request_line.decode("ascii", errors="replace").strip().split(" ", 2)
    except ValueError:
        return "", ""
    return method.upper(), raw_path.split("?", 1)[0]


def _write_response(writer: asyncio.StreamWriter, status: int, content_type: str, body: bytes) -> None:
    reason = {
        200: "OK",
        404: "Not Found",
        405: "Method Not Allowed",
    }.get(status, "OK")
    writer.write(
        "\r\n".join(
            [
                f"HTTP/1.1 {status} {reason}",
                f"Content-Type: {content_type}",
                f"Content-Length: {len(body)}",
                "Cache-Control: no-store",
                "Connection: close",
                "",
                "",
            ]
        ).encode("ascii")
        + body
    )


ENGINE_PAGE = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>PiBoat Engine</title>
    <style>
      :root { color-scheme: dark; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
      body { margin: 0; min-height: 100vh; background: #071014; color: #eef7f5; }
      main { box-sizing: border-box; min-height: 100vh; padding: 24px; display: grid; gap: 18px; grid-template-rows: auto 1fr auto; }
      header { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
      h1 { margin: 0; font-size: 22px; font-weight: 700; }
      #status { color: #9fb2ae; font-size: 14px; }
      .gauges { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; align-content: center; }
      .gauge { border: 1px solid #214044; background: #0b1a1f; border-radius: 8px; padding: 18px; min-height: 118px; display: grid; align-content: center; }
      .label { color: #8da4a2; font-size: 13px; text-transform: uppercase; letter-spacing: 0.08em; }
      .value { font-size: clamp(38px, 12vw, 78px); line-height: 1; font-weight: 800; font-variant-numeric: tabular-nums; }
      .unit { color: #9fb2ae; font-size: 18px; margin-left: 6px; }
      .wide { grid-column: 1 / -1; }
      pre { margin: 0; color: #9fb2ae; white-space: pre-wrap; font-size: 12px; }
      @media (max-width: 640px) { .gauges { grid-template-columns: 1fr; } .wide { grid-column: auto; } }
    </style>
  </head>
  <body>
    <main>
      <header>
        <h1>Engine</h1>
        <span id="status">Connecting</span>
      </header>
      <section class="gauges">
        <div class="gauge">
          <span class="label">RPM</span>
          <div><span class="value" id="rpm">--</span><span class="unit">rpm</span></div>
        </div>
        <div class="gauge">
          <span class="label">MAP</span>
          <div><span class="value" id="map">--</span><span class="unit">kPa</span></div>
        </div>
        <div class="gauge wide">
          <span class="label">Battery</span>
          <div><span class="value" id="voltage">--</span><span class="unit">V</span></div>
        </div>
      </section>
      <pre id="detail"></pre>
    </main>
    <script>
      const els = {
        status: document.querySelector("#status"),
        rpm: document.querySelector("#rpm"),
        map: document.querySelector("#map"),
        voltage: document.querySelector("#voltage"),
        detail: document.querySelector("#detail"),
      };

      async function refresh() {
        try {
          const response = await fetch("/api/engine", { cache: "no-store" });
          const data = await response.json();
          els.status.textContent = data.status === "ok" ? `Live - ${data.last_success_age_seconds ?? 0}s old` : data.error || data.status;
          els.rpm.textContent = Number.isFinite(data.rpm) ? Math.round(data.rpm) : "--";
          els.map.textContent = Number.isFinite(data.map_kpa) ? data.map_kpa.toFixed(1) : "--";
          els.voltage.textContent = Number.isFinite(data.voltage) ? data.voltage.toFixed(2) : "--";
          els.detail.textContent = JSON.stringify(data, null, 2);
        } catch (error) {
          els.status.textContent = error.message;
        }
      }

      refresh();
      setInterval(refresh, 500);
    </script>
  </body>
</html>
"""
