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
      main { box-sizing: border-box; min-height: 100vh; padding: 18px; display: grid; gap: 14px; }
      header { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
      h1 { margin: 0; font-size: 22px; font-weight: 700; }
      h2 { margin: 0; font-size: 16px; }
      #status { color: #9fb2ae; font-size: 14px; }
      .gauges { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; align-content: center; }
      .gauge, .panel { border: 1px solid #214044; background: #0b1a1f; border-radius: 8px; padding: 14px; }
      .gauge { min-height: 96px; display: grid; align-content: center; }
      .label { color: #8da4a2; font-size: 13px; text-transform: uppercase; letter-spacing: 0.08em; }
      .value { font-size: clamp(30px, 7vw, 62px); line-height: 1; font-weight: 800; font-variant-numeric: tabular-nums; }
      .unit { color: #9fb2ae; font-size: 16px; margin-left: 6px; }
      .charts { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(280px, 0.7fr); gap: 10px; }
      .chart-stack { display: grid; gap: 10px; }
      .panel header { margin-bottom: 10px; }
      canvas { display: block; width: 100%; height: 210px; background: #071014; border-radius: 6px; }
      .small canvas { height: 132px; }
      .legend { display: flex; flex-wrap: wrap; gap: 10px; color: #9fb2ae; font-size: 13px; }
      .legend span::before { content: ""; display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 5px; background: var(--color); }
      .analysis { display: grid; gap: 10px; }
      .analysis strong { font-size: 20px; }
      .analysis p { margin: 0; color: #9fb2ae; line-height: 1.35; }
      pre { margin: 0; color: #9fb2ae; white-space: pre-wrap; font-size: 12px; }
      @media (max-width: 900px) { .gauges, .charts { grid-template-columns: 1fr 1fr; } .chart-stack { grid-column: 1 / -1; } }
      @media (max-width: 640px) { main { padding: 12px; } .gauges, .charts { grid-template-columns: 1fr; } canvas { height: 180px; } }
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
        <div class="gauge">
          <span class="label">Load</span>
          <div><span class="value" id="load">--</span><span class="unit">%</span></div>
        </div>
        <div class="gauge">
          <span class="label">Battery</span>
          <div><span class="value" id="voltage">--</span><span class="unit">V</span></div>
        </div>
      </section>
      <section class="charts">
        <div class="panel">
          <header>
            <h2>Engine Composite - Last Minute</h2>
            <div class="legend">
              <span style="--color:#54d6a5">RPM</span>
              <span style="--color:#f4c15d">MAP</span>
              <span style="--color:#66a8ff">Load</span>
            </div>
          </header>
          <canvas id="compositeChart" width="900" height="260"></canvas>
        </div>
        <div class="chart-stack">
          <div class="panel small">
            <header><h2>MAP</h2></header>
            <canvas id="mapChart" width="420" height="160"></canvas>
          </div>
          <div class="panel small">
            <header><h2>RPM</h2></header>
            <canvas id="rpmChart" width="420" height="160"></canvas>
          </div>
        </div>
      </section>
      <section class="panel analysis">
        <h2>MAP Read</h2>
        <strong id="mapState">--</strong>
        <p id="mixtureNote">MAP by itself cannot determine lean or rich. For mixture you need an O2, wideband AFR, or EGT sensor. MAP can still show load, vacuum behavior, throttle changes, and possible vacuum leak clues.</p>
      </section>
      <pre id="detail"></pre>
    </main>
    <script>
      const HISTORY_MS = 60 * 1000;
      const MAP_MIN_KPA = 10;
      const MAP_MAX_KPA = 105;
      const els = {
        status: document.querySelector("#status"),
        rpm: document.querySelector("#rpm"),
        map: document.querySelector("#map"),
        load: document.querySelector("#load"),
        voltage: document.querySelector("#voltage"),
        mapState: document.querySelector("#mapState"),
        detail: document.querySelector("#detail"),
        compositeChart: document.querySelector("#compositeChart"),
        mapChart: document.querySelector("#mapChart"),
        rpmChart: document.querySelector("#rpmChart"),
      };
      const history = [];

      async function refresh() {
        try {
          const response = await fetch("/api/engine", { cache: "no-store" });
          const data = await response.json();
          const sample = normalizeSample(data);
          if (sample.status === "ok") {
            history.push(sample);
            trimHistory();
          }
          els.status.textContent = data.status === "ok" ? `Live - ${data.last_success_age_seconds ?? 0}s old` : data.error || data.status;
          els.rpm.textContent = Number.isFinite(sample.rpm) ? Math.round(sample.rpm) : "--";
          els.map.textContent = Number.isFinite(sample.mapKpa) ? sample.mapKpa.toFixed(1) : "--";
          els.load.textContent = Number.isFinite(sample.loadPercent) ? sample.loadPercent.toFixed(0) : "--";
          els.voltage.textContent = Number.isFinite(sample.voltage) ? sample.voltage.toFixed(2) : "--";
          els.mapState.textContent = describeMapState(sample);
          els.detail.textContent = JSON.stringify(data, null, 2);
          drawCharts();
        } catch (error) {
          els.status.textContent = error.message;
        }
      }

      function normalizeSample(data) {
        const mapKpa = Number(data.map_kpa);
        return {
          status: data.status,
          timestamp: Date.now(),
          rpm: finiteOrNull(Number(data.rpm)),
          mapKpa: finiteOrNull(mapKpa),
          loadPercent: Number.isFinite(mapKpa) ? clamp(((mapKpa - MAP_MIN_KPA) / (MAP_MAX_KPA - MAP_MIN_KPA)) * 100, 0, 100) : null,
          voltage: finiteOrNull(Number(data.voltage)),
        };
      }

      function trimHistory() {
        const cutoff = Date.now() - HISTORY_MS;
        while (history.length && history[0].timestamp < cutoff) {
          history.shift();
        }
      }

      function describeMapState(sample) {
        if (!Number.isFinite(sample.mapKpa)) {
          return "Waiting for MAP";
        }
        if (!Number.isFinite(sample.rpm) || sample.rpm < 250) {
          return sample.mapKpa > 85 ? "Engine off / atmospheric MAP" : "Engine off or MAP calibration suspect";
        }
        if (sample.mapKpa < 35) {
          return "High vacuum / light load";
        }
        if (sample.mapKpa < 60) {
          return "Idle or easy cruise load";
        }
        if (sample.mapKpa < 85) {
          return "Moderate engine load";
        }
        return "High load / throttle open";
      }

      function drawCharts() {
        drawCompositeChart(els.compositeChart, history);
        drawMetricChart(els.mapChart, history, "mapKpa", "kPa", "#f4c15d", 0, 110);
        const maxRpm = Math.max(1000, ...history.map((sample) => sample.rpm || 0)) * 1.15;
        drawMetricChart(els.rpmChart, history, "rpm", "rpm", "#54d6a5", 0, maxRpm);
      }

      function drawCompositeChart(canvas, samples) {
        const series = [
          { key: "rpm", label: "RPM", color: "#54d6a5", min: 0, max: Math.max(1000, ...samples.map((sample) => sample.rpm || 0)) * 1.15 },
          { key: "mapKpa", label: "MAP", color: "#f4c15d", min: 0, max: 110 },
          { key: "loadPercent", label: "Load", color: "#66a8ff", min: 0, max: 100 },
        ];
        drawBase(canvas, (ctx, area, range) => {
          series.forEach((item) => drawSeries(ctx, area, range, samples, item));
        });
      }

      function drawMetricChart(canvas, samples, key, unit, color, min, max) {
        drawBase(canvas, (ctx, area, range) => {
          drawSeries(ctx, area, range, samples, { key, color, min, max });
          ctx.fillStyle = "#8da4a2";
          ctx.font = "12px system-ui";
          ctx.fillText(`${Math.round(max)} ${unit}`, area.left, area.top + 12);
          ctx.fillText(`${Math.round(min)} ${unit}`, area.left, area.bottom - 4);
        });
      }

      function drawBase(canvas, drawContent) {
        const ctx = canvas.getContext("2d");
        const scale = window.devicePixelRatio || 1;
        const bounds = canvas.getBoundingClientRect();
        canvas.width = Math.max(1, Math.floor(bounds.width * scale));
        canvas.height = Math.max(1, Math.floor(bounds.height * scale));
        ctx.setTransform(scale, 0, 0, scale, 0, 0);
        const width = bounds.width;
        const height = bounds.height;
        const area = { left: 34, right: width - 12, top: 14, bottom: height - 24 };
        ctx.clearRect(0, 0, width, height);
        ctx.strokeStyle = "#173036";
        ctx.lineWidth = 1;
        for (let index = 0; index <= 4; index += 1) {
          const y = area.top + ((area.bottom - area.top) * index / 4);
          ctx.beginPath();
          ctx.moveTo(area.left, y);
          ctx.lineTo(area.right, y);
          ctx.stroke();
        }
        const now = Date.now();
        const range = { start: now - HISTORY_MS, end: now };
        drawContent(ctx, area, range);
        ctx.fillStyle = "#8da4a2";
        ctx.font = "12px system-ui";
        ctx.fillText("-60s", area.left, height - 7);
        ctx.fillText("now", area.right - 26, height - 7);
      }

      function drawSeries(ctx, area, range, samples, item) {
        const points = samples
          .filter((sample) => Number.isFinite(sample[item.key]))
          .map((sample) => ({
            x: area.left + ((sample.timestamp - range.start) / HISTORY_MS) * (area.right - area.left),
            y: area.bottom - ((sample[item.key] - item.min) / Math.max(1, item.max - item.min)) * (area.bottom - area.top),
          }));
        if (points.length < 2) {
          return;
        }
        ctx.strokeStyle = item.color;
        ctx.lineWidth = 2;
        ctx.beginPath();
        points.forEach((point, index) => {
          if (index === 0) {
            ctx.moveTo(point.x, point.y);
          } else {
            ctx.lineTo(point.x, point.y);
          }
        });
        ctx.stroke();
      }

      function finiteOrNull(value) {
        return Number.isFinite(value) ? value : null;
      }

      function clamp(value, min, max) {
        return Math.min(max, Math.max(min, value));
      }

      refresh();
      setInterval(refresh, 50);
    </script>
  </body>
</html>
"""
