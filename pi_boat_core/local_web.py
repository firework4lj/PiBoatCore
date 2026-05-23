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
      body { margin: 0; min-height: 100vh; background: #061015; color: #eef7f5; }
      main { box-sizing: border-box; min-height: 100vh; padding: 18px; display: grid; gap: 14px; }
      header { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
      h1 { margin: 0; font-size: 22px; font-weight: 700; }
      h2 { margin: 0; font-size: 16px; }
      #status { color: #9fb2ae; font-size: 14px; }
      .metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; align-content: center; }
      .metric, .panel { border: 1px solid #214044; background: #0b1a1f; border-radius: 8px; padding: 14px; }
      .metric { display: grid; gap: 12px; min-height: 112px; }
      .metric-label { color: #8da4a2; font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; }
      .metric-value { font-size: clamp(34px, 5vw, 58px); line-height: 1; font-weight: 800; font-variant-numeric: tabular-nums; }
      .metric-unit { color: #9fb2ae; font-size: 15px; margin-left: 6px; font-weight: 500; }
      .bar { height: 9px; border-radius: 99px; background: #173036; overflow: hidden; }
      .bar-fill { width: 0%; height: 100%; border-radius: inherit; background: var(--color); transition: width 120ms linear; }
      .metric-range { display: flex; justify-content: space-between; color: #708681; font-size: 12px; }
      .charts { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(280px, 0.7fr); gap: 10px; }
      .chart-stack { display: grid; gap: 10px; }
      .panel header { margin-bottom: 10px; }
      canvas { display: block; width: 100%; height: 210px; background: #071014; border-radius: 6px; }
      .small canvas { height: 132px; }
      .legend { display: flex; flex-wrap: wrap; gap: 10px; color: #9fb2ae; font-size: 13px; }
      .legend span::before { content: ""; display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 5px; background: var(--color); }
      .analysis { display: grid; gap: 10px; }
      .analysis-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; }
      .analysis-card { border: 1px solid #214044; border-radius: 8px; padding: 12px; background: #071014; }
      .analysis-card span { display: block; color: #8da4a2; font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; }
      .analysis-card strong { display: block; margin-top: 6px; font-size: 20px; }
      .analysis strong { font-size: 20px; }
      .analysis p { margin: 0; color: #9fb2ae; line-height: 1.35; }
      pre { margin: 0; color: #9fb2ae; white-space: pre-wrap; font-size: 12px; }
      @media (max-width: 900px) { .metrics, .charts, .analysis-grid { grid-template-columns: 1fr 1fr; } .chart-stack { grid-column: 1 / -1; } }
      @media (max-width: 640px) { main { padding: 12px; } .metrics, .charts, .analysis-grid { grid-template-columns: 1fr; } canvas { height: 180px; } }
    </style>
  </head>
  <body>
    <main>
      <header>
        <h1>Engine</h1>
        <span id="status">Connecting</span>
      </header>
      <section class="metrics">
        <div class="metric">
          <span class="metric-label">RPM</span>
          <div><span class="metric-value" id="rpmValue">--</span><span class="metric-unit">rpm</span></div>
          <div class="bar"><div class="bar-fill" id="rpmBar" style="--color:#54d6a5"></div></div>
          <div class="metric-range"><span>0</span><span>4000</span></div>
        </div>
        <div class="metric">
          <span class="metric-label">MAP</span>
          <div><span class="metric-value" id="mapValue">--</span><span class="metric-unit">kPa</span></div>
          <div class="bar"><div class="bar-fill" id="mapBar" style="--color:#f4c15d"></div></div>
          <div class="metric-range"><span>0</span><span>110</span></div>
        </div>
        <div class="metric">
          <span class="metric-label">Load</span>
          <div><span class="metric-value" id="loadValue">--</span><span class="metric-unit">%</span></div>
          <div class="bar"><div class="bar-fill" id="loadBar" style="--color:#66a8ff"></div></div>
          <div class="metric-range"><span>0</span><span>100</span></div>
        </div>
        <div class="metric">
          <span class="metric-label">Battery</span>
          <div><span class="metric-value" id="voltageValue">--</span><span class="metric-unit">V</span></div>
          <div class="bar"><div class="bar-fill" id="voltageBar" style="--color:#e97b68"></div></div>
          <div class="metric-range"><span>11</span><span>15</span></div>
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
        <h2>Engine Read</h2>
        <strong id="mapState">--</strong>
        <div class="analysis-grid">
          <div class="analysis-card"><span>State</span><strong id="engineState">--</strong></div>
          <div class="analysis-card"><span>Idle Quality</span><strong id="idleQuality">--</strong></div>
          <div class="analysis-card"><span>MAP Stability</span><strong id="mapStability">--</strong></div>
          <div class="analysis-card"><span>Efficiency</span><strong id="efficiencyHint">--</strong></div>
          <div class="analysis-card"><span>Warnings</span><strong id="engineWarnings">--</strong></div>
        </div>
        <p id="mixtureNote">MAP by itself cannot determine lean or rich. For mixture you need an O2, wideband AFR, or EGT sensor. MAP can still show load, vacuum behavior, throttle changes, and possible vacuum leak clues.</p>
      </section>
      <pre id="detail"></pre>
    </main>
    <script>
      const HISTORY_MS = 60 * 1000;
      const MAP_LOAD_IDLE_KPA = 35;
      const MAP_LOAD_WOT_KPA = 100;
      const els = {
        status: document.querySelector("#status"),
        rpmValue: document.querySelector("#rpmValue"),
        mapValue: document.querySelector("#mapValue"),
        loadValue: document.querySelector("#loadValue"),
        voltageValue: document.querySelector("#voltageValue"),
        rpmBar: document.querySelector("#rpmBar"),
        mapBar: document.querySelector("#mapBar"),
        loadBar: document.querySelector("#loadBar"),
        voltageBar: document.querySelector("#voltageBar"),
        mapState: document.querySelector("#mapState"),
        engineState: document.querySelector("#engineState"),
        idleQuality: document.querySelector("#idleQuality"),
        mapStability: document.querySelector("#mapStability"),
        efficiencyHint: document.querySelector("#efficiencyHint"),
        engineWarnings: document.querySelector("#engineWarnings"),
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
          renderMetrics(sample);
          els.mapState.textContent = describeMapState(sample);
          renderAnalysis(data);
          els.detail.textContent = JSON.stringify(data, null, 2);
          drawCharts();
        } catch (error) {
          els.status.textContent = error.message;
        }
      }

      function renderAnalysis(data) {
        els.engineState.textContent = labelValue(data.engine_state);
        els.idleQuality.textContent = formatScore(data.idle_quality, data.idle_quality_score);
        els.mapStability.textContent = formatScore(data.map_stability, data.map_stability_score);
        els.efficiencyHint.textContent = formatScore(data.efficiency_hint, data.efficiency_score);
        const warnings = [];
        if (data.bog_detected) warnings.push("Bog");
        if (data.stall_risk) warnings.push("Stall risk");
        els.engineWarnings.textContent = warnings.join(" / ") || "None";
      }

      function renderMetrics(sample) {
        renderMetric(els.rpmValue, els.rpmBar, sample.rpm, 0, 4000, 0);
        renderMetric(els.mapValue, els.mapBar, sample.mapKpaAvg, 0, 110, 1);
        renderMetric(els.loadValue, els.loadBar, sample.loadPercent, 0, 100, 0);
        renderMetric(els.voltageValue, els.voltageBar, sample.voltage, 11, 15, 2);
      }

      function renderMetric(valueElement, barElement, value, min, max, decimals) {
        valueElement.textContent = Number.isFinite(value) ? value.toFixed(decimals) : "--";
        const ratio = Number.isFinite(value) ? clamp((value - min) / (max - min), 0, 1) : 0;
        barElement.style.width = `${ratio * 100}%`;
      }

      function normalizeSample(data) {
        const mapKpa = Number(data.map_kpa);
        const mapKpaAvg = Number(data.map_kpa_avg);
        const loadPercent = Number(data.map_load_percent);
        return {
          status: data.status,
          timestamp: Date.now(),
          rpm: finiteOrNull(Number(data.rpm)),
          rpmWindow: finiteOrNull(Number(data.rpm_window)),
          rpmInstant: finiteOrNull(Number(data.rpm_instant)),
          mapKpa: finiteOrNull(mapKpa),
          mapKpaAvg: Number.isFinite(mapKpaAvg) ? mapKpaAvg : finiteOrNull(mapKpa),
          loadPercent: Number.isFinite(loadPercent) ? loadPercent : estimateLoadPercent(mapKpaAvg || mapKpa),
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
        const mapKpa = sample.mapKpaAvg;
        if (!Number.isFinite(mapKpa)) {
          return "Waiting for MAP";
        }
        if (!Number.isFinite(sample.rpm) || sample.rpm < 250) {
          return mapKpa > 85 ? "Engine off / atmospheric MAP" : "Engine off or MAP calibration suspect";
        }
        if (sample.loadPercent <= 8) {
          return "Near idle / very light load";
        }
        if (sample.loadPercent <= 35) {
          return "Efficient light load range";
        }
        if (sample.loadPercent <= 75) {
          return "Moderate engine load";
        }
        return "High load / throttle open";
      }

      function drawCharts() {
        drawCompositeChart(els.compositeChart, history);
        drawMetricChart(els.mapChart, history, "mapKpaAvg", "kPa", "#f4c15d", 0, 110);
        const maxRpm = Math.max(1000, ...history.map((sample) => sample.rpm || 0)) * 1.15;
        drawMetricChart(els.rpmChart, history, "rpm", "rpm", "#54d6a5", 0, maxRpm);
      }

      function drawCompositeChart(canvas, samples) {
        const series = [
          { key: "rpm", label: "RPM", color: "#54d6a5", min: 0, max: Math.max(1000, ...samples.map((sample) => sample.rpm || 0)) * 1.15 },
          { key: "mapKpaAvg", label: "MAP", color: "#f4c15d", min: 0, max: 110 },
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

      function formatScore(label, score) {
        if (!label || label === "unknown") {
          return "--";
        }
        const text = labelValue(label);
        return Number.isFinite(score) ? `${text} ${Math.round(score)}` : text;
      }

      function labelValue(value) {
        return String(value || "--").replaceAll("_", " ").replace(/\\b\\w/g, (letter) => letter.toUpperCase());
      }

      function estimateLoadPercent(mapKpa) {
        return Number.isFinite(mapKpa)
          ? clamp(((mapKpa - MAP_LOAD_IDLE_KPA) / (MAP_LOAD_WOT_KPA - MAP_LOAD_IDLE_KPA)) * 100, 0, 100)
          : null;
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
