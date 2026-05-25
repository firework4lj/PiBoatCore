from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable

from pi_boat_core.config import LocalWebConfig


EngineProvider = Callable[[], dict[str, Any]]


class LocalWebServer:
    def __init__(self, config: LocalWebConfig, engine_provider: EngineProvider) -> None:
        self.config = config
        self.engine_provider = engine_provider
        self.run_store = EngineRunStore(Path("./engine_runs.json"))
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

            headers: dict[str, str] = {}
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=2)
                if line in (b"\r\n", b"\n", b""):
                    break
                name, _, value = line.decode("ascii", errors="replace").partition(":")
                if name and value:
                    headers[name.strip().lower()] = value.strip()

            method, path = _parse_request_line(request_line)
            body = b""
            content_length = int(headers.get("content-length") or 0)
            if content_length > 0:
                body = await asyncio.wait_for(reader.readexactly(min(content_length, 1_000_000)), timeout=3)

            if method == "GET" and path == "/api/engine":
                body = json.dumps(self.engine_provider(), separators=(",", ":")).encode("utf-8")
                _write_response(writer, 200, "application/json; charset=utf-8", body)
            elif method == "GET" and path == "/api/engine-runs":
                body = json.dumps({"runs": self.run_store.list_runs()}, separators=(",", ":")).encode("utf-8")
                _write_response(writer, 200, "application/json; charset=utf-8", body)
            elif method == "POST" and path == "/api/engine-runs":
                try:
                    run = self.run_store.save(json.loads(body.decode("utf-8")))
                    response = json.dumps({"status": "saved", "run": run}, separators=(",", ":")).encode("utf-8")
                    _write_response(writer, 201, "application/json; charset=utf-8", response)
                except (ValueError, json.JSONDecodeError) as exc:
                    response = json.dumps({"error": str(exc)}, separators=(",", ":")).encode("utf-8")
                    _write_response(writer, 400, "application/json; charset=utf-8", response)
            elif method == "DELETE" and path.startswith("/api/engine-runs/"):
                run_id = path.rsplit("/", 1)[-1]
                response = json.dumps(self.run_store.delete(run_id), separators=(",", ":")).encode("utf-8")
                _write_response(writer, 200, "application/json; charset=utf-8", response)
            elif method != "GET":
                _write_response(writer, 405, "text/plain; charset=utf-8", b"Method not allowed")
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
        201: "Created",
        400: "Bad Request",
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


class EngineRunStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def list_runs(self) -> list[dict[str, Any]]:
        runs = self._load()
        runs.sort(key=lambda run: run.get("started_at") or run.get("saved_at") or "", reverse=True)
        return runs[:50]

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        samples = payload.get("samples")
        if not isinstance(samples, list) or len(samples) < 2:
            raise ValueError("run must include at least two samples")

        normalized_samples = [_normalize_run_sample(sample) for sample in samples]
        normalized_samples = [sample for sample in normalized_samples if sample is not None]
        if len(normalized_samples) < 2:
            raise ValueError("run must include at least two valid samples")

        run_id = _safe_run_id(payload.get("id") or f"run-{normalized_samples[0]['timestamp']}")
        run = {
            "id": run_id,
            "name": str(payload.get("name") or "Engine run")[:80],
            "saved_at": _iso_now(),
            "started_at": payload.get("started_at") or _timestamp_to_iso(normalized_samples[0]["timestamp"]),
            "ended_at": payload.get("ended_at") or _timestamp_to_iso(normalized_samples[-1]["timestamp"]),
            "stats": payload.get("stats") if isinstance(payload.get("stats"), dict) else _run_stats(normalized_samples),
            "samples": normalized_samples,
        }

        runs = [existing for existing in self._load() if existing.get("id") != run_id]
        runs.insert(0, run)
        self._write(runs[:50])
        return run

    def delete(self, run_id: str) -> dict[str, Any]:
        safe_id = _safe_run_id(run_id)
        runs = self._load()
        kept = [run for run in runs if run.get("id") != safe_id]
        self._write(kept)
        return {"deleted": len(runs) - len(kept)}

    def _load(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    def _write(self, runs: list[dict[str, Any]]) -> None:
        self.path.write_text(json.dumps(runs, indent=2) + "\n", encoding="utf-8")


def _normalize_run_sample(sample: Any) -> dict[str, float] | None:
    if not isinstance(sample, dict):
        return None
    timestamp = _number_or_none(sample.get("timestamp"))
    if timestamp is None:
        return None
    return {
        "timestamp": timestamp,
        "rpm": _number_or_none(sample.get("rpm")),
        "mapKpaAvg": _number_or_none(sample.get("mapKpaAvg")),
        "loadPercent": _number_or_none(sample.get("loadPercent")),
        "voltage": _number_or_none(sample.get("voltage")),
    }


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _run_stats(samples: list[dict[str, float]]) -> dict[str, Any]:
    return {
        "duration_seconds": round((samples[-1]["timestamp"] - samples[0]["timestamp"]) / 1000, 1),
        "average_rpm": _average([sample["rpm"] for sample in samples if sample.get("rpm") is not None]),
        "max_rpm": max((sample["rpm"] for sample in samples if sample.get("rpm") is not None), default=None),
        "average_map_kpa": _average([sample["mapKpaAvg"] for sample in samples if sample.get("mapKpaAvg") is not None]),
        "average_load_percent": _average([sample["loadPercent"] for sample in samples if sample.get("loadPercent") is not None]),
    }


def _average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _safe_run_id(value: Any) -> str:
    safe = "".join(char if char.isalnum() or char in "._-" else "_" for char in str(value))
    return safe[:80] or "run"


def _iso_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp_to_iso(timestamp: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(timestamp / 1000, UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
      .run-controls { align-items: center; display: flex; flex-wrap: wrap; gap: 8px; }
      button { background: #102a32; border: 1px solid #2b555c; border-radius: 7px; color: #eef7f5; cursor: pointer; font: inherit; font-weight: 700; padding: 8px 11px; }
      button:disabled { cursor: not-allowed; opacity: 0.45; }
      .primary-button { background: #0f5f78; border-color: #1784a5; }
      .run-status { color: #9fb2ae; font-size: 13px; }
      .saved-runs { display: grid; gap: 8px; margin-top: 10px; }
      .saved-run { align-items: center; background: #071014; border: 1px solid #214044; border-radius: 7px; display: grid; gap: 8px; grid-template-columns: minmax(0, 1fr) auto auto; padding: 9px; }
      .saved-run strong, .saved-run small { display: block; min-width: 0; overflow-wrap: anywhere; }
      .saved-run small { color: #9fb2ae; font-size: 12px; margin-top: 2px; }
      .tune-panel { display: grid; gap: 10px; }
      .tune-instruction { background: #071014; border: 1px solid #2b555c; border-radius: 8px; font-size: clamp(22px, 4vw, 42px); font-weight: 900; line-height: 1.12; padding: 16px; }
      .tune-detail { color: #9fb2ae; line-height: 1.35; margin: 0; }
      .tune-stats { display: grid; gap: 8px; grid-template-columns: repeat(4, minmax(0, 1fr)); }
      .tune-stats div { background: #071014; border: 1px solid #214044; border-radius: 8px; padding: 10px; }
      .tune-stats span { color: #8da4a2; display: block; font-size: 11px; font-weight: 800; letter-spacing: 0.08em; text-transform: uppercase; }
      .tune-stats strong { display: block; font-size: 18px; margin-top: 4px; }
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
      @media (max-width: 640px) { main { padding: 12px; } .metrics, .charts, .analysis-grid, .tune-stats { grid-template-columns: 1fr; } canvas { height: 180px; } }
      @media (max-width: 640px) { .saved-run { grid-template-columns: 1fr; } }
    </style>
  </head>
  <body>
    <main>
      <header>
        <h1>Engine</h1>
        <span id="status">Connecting</span>
      </header>
      <section class="panel">
        <header>
          <h2>Engine Runs</h2>
          <span class="run-status" id="runStatus">Not recording</span>
        </header>
        <div class="run-controls">
          <button type="button" id="startRunButton">Start Run</button>
          <button type="button" id="saveRunButton" disabled>Save Run</button>
          <button type="button" id="discardRunButton" disabled>Discard</button>
        </div>
        <div id="savedRuns" class="saved-runs"></div>
      </section>
      <section class="panel tune-panel">
        <header>
          <h2>Carb Tune</h2>
          <span class="run-status">Fuel screws</span>
        </header>
        <div class="tune-instruction" id="tuneInstruction">Warm engine fully, set idle, then start tune.</div>
        <p class="tune-detail" id="tuneDetail">This guides fuel screws by looking for highest/smoothest idle vacuum. Fuel screw OUT is richer; IN is leaner.</p>
        <div class="run-controls">
          <button type="button" id="startTuneButton" class="primary-button">Start Tune</button>
          <button type="button" id="tuneStepButton" disabled>Done With Step</button>
          <button type="button" id="tuneSwitchButton" disabled>Switch Screw</button>
          <button type="button" id="stopTuneButton" disabled>Stop</button>
        </div>
        <div class="tune-stats">
          <div><span>Step</span><strong id="tuneStep">--</strong></div>
          <div><span>Avg RPM</span><strong id="tuneRpm">--</strong></div>
          <div><span>Avg MAP</span><strong id="tuneMap">--</strong></div>
          <div><span>Stability</span><strong id="tuneStability">--</strong></div>
        </div>
      </section>
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
        runStatus: document.querySelector("#runStatus"),
        startRunButton: document.querySelector("#startRunButton"),
        saveRunButton: document.querySelector("#saveRunButton"),
        discardRunButton: document.querySelector("#discardRunButton"),
        savedRuns: document.querySelector("#savedRuns"),
        startTuneButton: document.querySelector("#startTuneButton"),
        tuneStepButton: document.querySelector("#tuneStepButton"),
        tuneSwitchButton: document.querySelector("#tuneSwitchButton"),
        stopTuneButton: document.querySelector("#stopTuneButton"),
        tuneInstruction: document.querySelector("#tuneInstruction"),
        tuneDetail: document.querySelector("#tuneDetail"),
        tuneStep: document.querySelector("#tuneStep"),
        tuneRpm: document.querySelector("#tuneRpm"),
        tuneMap: document.querySelector("#tuneMap"),
        tuneStability: document.querySelector("#tuneStability"),
        compositeChart: document.querySelector("#compositeChart"),
        mapChart: document.querySelector("#mapChart"),
        rpmChart: document.querySelector("#rpmChart"),
      };
      const history = [];
      let activeRun = null;
      let reviewSamples = null;
      let carbTune = null;

      els.startRunButton.addEventListener("click", startRun);
      els.saveRunButton.addEventListener("click", saveRun);
      els.discardRunButton.addEventListener("click", discardRun);
      els.startTuneButton.addEventListener("click", startCarbTune);
      els.tuneStepButton.addEventListener("click", markTuneStep);
      els.tuneSwitchButton.addEventListener("click", switchTuneScrew);
      els.stopTuneButton.addEventListener("click", stopCarbTune);

      async function refresh() {
        try {
          const response = await fetch("/api/engine", { cache: "no-store" });
          const data = await response.json();
          const sample = normalizeSample(data);
          if (sample.status === "ok") {
            history.push(sample);
            if (activeRun) {
              activeRun.samples.push(sample);
            }
            trimHistory();
          }
          els.status.textContent = data.status === "ok" ? `Live - ${data.last_success_age_seconds ?? 0}s old` : data.error || data.status;
          renderMetrics(sample);
          els.mapState.textContent = describeMapState(sample);
          renderAnalysis(data);
          els.detail.textContent = JSON.stringify(data, null, 2);
          drawCharts();
          renderRunStatus();
          renderCarbTune();
        } catch (error) {
          els.status.textContent = error.message;
        }
      }

      async function refreshRuns() {
        try {
          const response = await fetch("/api/engine-runs", { cache: "no-store" });
          const data = await response.json();
          renderSavedRuns(data.runs || []);
        } catch (error) {
          els.savedRuns.textContent = error.message;
        }
      }

      function startRun() {
        activeRun = { startedAt: new Date().toISOString(), samples: [] };
        reviewSamples = null;
        renderRunStatus();
      }

      async function saveRun() {
        if (!activeRun || activeRun.samples.length < 2) {
          return;
        }
        const name = window.prompt("Run name", defaultRunName());
        if (name === null) {
          return;
        }
        const payload = {
          id: `run-${Date.now()}`,
          name: name.trim() || defaultRunName(),
          started_at: activeRun.startedAt,
          ended_at: new Date().toISOString(),
          samples: activeRun.samples,
          stats: calculateRunStats(activeRun.samples),
        };
        const response = await fetch("/api/engine-runs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!response.ok) {
          throw new Error(`Save failed: ${response.status}`);
        }
        activeRun = null;
        renderRunStatus();
        await refreshRuns();
      }

      function discardRun() {
        activeRun = null;
        renderRunStatus();
      }

      function renderRunStatus() {
        if (!activeRun) {
          els.runStatus.textContent = reviewSamples ? "Reviewing saved run" : "Not recording";
          els.startRunButton.disabled = false;
          els.saveRunButton.disabled = true;
          els.discardRunButton.disabled = true;
          return;
        }
        const duration = activeRun.samples.length
          ? formatDuration(activeRun.samples.at(-1).timestamp - activeRun.samples[0].timestamp)
          : "0s";
        els.runStatus.textContent = `Recording ${duration} / ${activeRun.samples.length} samples`;
        els.startRunButton.disabled = true;
        els.saveRunButton.disabled = activeRun.samples.length < 2;
        els.discardRunButton.disabled = false;
      }

      function renderSavedRuns(runs) {
        if (!runs.length) {
          els.savedRuns.textContent = "No saved engine runs";
          return;
        }
        els.savedRuns.replaceChildren(...runs.map((run) => {
          const row = document.createElement("div");
          const meta = document.createElement("div");
          const title = document.createElement("strong");
          const detail = document.createElement("small");
          const review = document.createElement("button");
          const remove = document.createElement("button");
          row.className = "saved-run";
          title.textContent = run.name || "Engine run";
          detail.textContent = runSummary(run);
          review.textContent = "Review";
          remove.textContent = "Delete";
          review.addEventListener("click", () => {
            reviewSamples = Array.isArray(run.samples) ? run.samples : [];
            els.detail.textContent = JSON.stringify(run, null, 2);
            drawCharts();
            renderRunStatus();
          });
          remove.addEventListener("click", () => deleteRun(run.id));
          meta.append(title, detail);
          row.append(meta, review, remove);
          return row;
        }));
      }

      async function deleteRun(id) {
        if (!window.confirm("Delete saved engine run?")) {
          return;
        }
        await fetch(`/api/engine-runs/${encodeURIComponent(id)}`, { method: "DELETE" });
        if (reviewSamples) {
          reviewSamples = null;
        }
        await refreshRuns();
        drawCharts();
        renderRunStatus();
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

      function startCarbTune() {
        carbTune = {
          active: true,
          screw: "A",
          direction: "out",
          step: 0,
          phase: "baseline",
          baseline: null,
          best: null,
          previous: null,
          measureStart: Date.now(),
          lastInstruction: "Hold warm idle steady. Capturing baseline...",
        };
        renderCarbTune();
      }

      function stopCarbTune() {
        carbTune = null;
        renderCarbTune();
      }

      function switchTuneScrew() {
        if (!carbTune) {
          return;
        }
        carbTune.screw = carbTune.screw === "A" ? "B" : "A";
        carbTune.direction = "out";
        carbTune.step = 0;
        carbTune.phase = "baseline";
        carbTune.baseline = null;
        carbTune.best = null;
        carbTune.previous = null;
        carbTune.measureStart = Date.now();
        carbTune.lastInstruction = `Now tuning Screw ${carbTune.screw}. Hold idle steady for baseline.`;
        renderCarbTune();
      }

      function markTuneStep() {
        if (!carbTune) {
          return;
        }
        const reading = tuneReadingSince(carbTune.measureStart);
        if (!reading) {
          carbTune.lastInstruction = "Need a few more seconds of stable readings.";
          renderCarbTune();
          return;
        }

        if (carbTune.phase === "baseline") {
          carbTune.baseline = reading;
          carbTune.best = reading;
          carbTune.previous = reading;
          carbTune.phase = "adjust";
          carbTune.measureStart = Date.now();
          carbTune.lastInstruction = `Screw ${carbTune.screw}: turn OUT 1/8 turn.`;
          renderCarbTune();
          return;
        }

        const score = scoreTuneReading(reading);
        const previousScore = scoreTuneReading(carbTune.previous);
        const bestScore = scoreTuneReading(carbTune.best);
        const improved = score > previousScore + 0.5;
        const bestImproved = score > bestScore + 0.5;
        carbTune.step += 1;

        if (bestImproved) {
          carbTune.best = reading;
        }

        if (improved) {
          carbTune.lastInstruction = `Good. Screw ${carbTune.screw}: continue ${carbTune.direction.toUpperCase()} 1/8 turn.`;
        } else {
          carbTune.direction = carbTune.direction === "out" ? "in" : "out";
          carbTune.lastInstruction = `Worse. Reverse last move. Screw ${carbTune.screw}: turn ${carbTune.direction.toUpperCase()} 1/8 turn.`;
        }

        if (Number.isFinite(reading.rpmAvg) && Number.isFinite(carbTune.baseline?.rpmAvg) && reading.rpmAvg > carbTune.baseline.rpmAvg + 150) {
          carbTune.lastInstruction += " Idle rose; reset idle speed lower after this screw.";
        }

        carbTune.previous = reading;
        carbTune.measureStart = Date.now();
        renderCarbTune();
      }

      function renderCarbTune() {
        if (!carbTune) {
          els.tuneInstruction.textContent = "Warm engine fully, set idle, then start tune.";
          els.tuneDetail.textContent = "This guides fuel screws by looking for highest/smoothest idle vacuum. Fuel screw OUT is richer; IN is leaner.";
          els.tuneStep.textContent = "--";
          els.tuneRpm.textContent = "--";
          els.tuneMap.textContent = "--";
          els.tuneStability.textContent = "--";
          els.startTuneButton.disabled = false;
          els.tuneStepButton.disabled = true;
          els.tuneSwitchButton.disabled = true;
          els.stopTuneButton.disabled = true;
          return;
        }

        const current = tuneReadingSince(carbTune.measureStart);
        els.tuneInstruction.textContent = carbTune.lastInstruction;
        els.tuneDetail.textContent = carbTune.phase === "baseline"
          ? "Hold the current idle steady, then press Done With Step after about 8-10 seconds."
          : "Make the instructed 1/8-turn adjustment, wait 8-10 seconds for idle to settle, then press Done With Step.";
        els.tuneStep.textContent = `Screw ${carbTune.screw} / ${carbTune.phase === "baseline" ? "baseline" : `step ${carbTune.step + 1}`}`;
        els.tuneRpm.textContent = current ? `${Math.round(current.rpmAvg)} rpm` : "--";
        els.tuneMap.textContent = current ? `${current.mapAvg.toFixed(1)} kPa` : "--";
        els.tuneStability.textContent = current ? `${current.stability.toFixed(1)}` : "--";
        els.startTuneButton.disabled = true;
        els.tuneStepButton.disabled = false;
        els.tuneSwitchButton.disabled = carbTune.phase === "baseline";
        els.stopTuneButton.disabled = false;
      }

      function tuneReadingSince(startTime) {
        const samples = history.filter((sample) =>
          sample.timestamp >= startTime &&
          Number.isFinite(sample.rpm) &&
          Number.isFinite(sample.mapKpaAvg)
        );
        if (samples.length < 40) {
          return null;
        }
        const rpmValues = samples.map((sample) => sample.rpm);
        const mapValues = samples.map((sample) => sample.mapKpaAvg);
        return {
          rpmAvg: average(rpmValues),
          mapAvg: average(mapValues),
          rpmStddev: stddev(rpmValues),
          mapStddev: stddev(mapValues),
          stability: (stddev(rpmValues) / 25) + (stddev(mapValues) * 4),
        };
      }

      function scoreTuneReading(reading) {
        if (!reading) {
          return -Infinity;
        }
        return (reading.rpmAvg / 40) - (reading.mapAvg * 1.4) - (reading.rpmStddev / 20) - (reading.mapStddev * 5);
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
        const samples = reviewSamples || history;
        drawCompositeChart(els.compositeChart, samples);
        drawMetricChart(els.mapChart, samples, "mapKpaAvg", "kPa", "#f4c15d", 0, 110);
        const maxRpm = Math.max(1000, ...samples.map((sample) => sample.rpm || 0)) * 1.15;
        drawMetricChart(els.rpmChart, samples, "rpm", "rpm", "#54d6a5", 0, maxRpm);
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
        const range = chartRange(reviewSamples || history);
        drawContent(ctx, area, range);
        ctx.fillStyle = "#8da4a2";
        ctx.font = "12px system-ui";
        ctx.fillText(reviewSamples ? "start" : "-60s", area.left, height - 7);
        ctx.fillText(reviewSamples ? "end" : "now", area.right - 26, height - 7);
      }

      function drawSeries(ctx, area, range, samples, item) {
        const points = samples
          .filter((sample) => Number.isFinite(sample[item.key]))
          .map((sample) => ({
            x: area.left + ((sample.timestamp - range.start) / Math.max(1, range.end - range.start)) * (area.right - area.left),
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

      function chartRange(samples) {
        if (reviewSamples && samples.length >= 2) {
          return { start: samples[0].timestamp, end: samples.at(-1).timestamp };
        }
        const now = Date.now();
        return { start: now - HISTORY_MS, end: now };
      }

      function calculateRunStats(samples) {
        return {
          duration_seconds: samples.length >= 2 ? Math.round((samples.at(-1).timestamp - samples[0].timestamp) / 100) / 10 : 0,
          average_rpm: average(samples.map((sample) => sample.rpm).filter(Number.isFinite)),
          max_rpm: Math.max(...samples.map((sample) => sample.rpm).filter(Number.isFinite), 0),
          average_map_kpa: average(samples.map((sample) => sample.mapKpaAvg).filter(Number.isFinite)),
          average_load_percent: average(samples.map((sample) => sample.loadPercent).filter(Number.isFinite)),
        };
      }

      function average(values) {
        return values.length ? Math.round((values.reduce((sum, value) => sum + value, 0) / values.length) * 10) / 10 : null;
      }

      function stddev(values) {
        if (values.length < 2) {
          return 0;
        }
        const avg = values.reduce((sum, value) => sum + value, 0) / values.length;
        const variance = values.reduce((sum, value) => sum + ((value - avg) ** 2), 0) / values.length;
        return Math.sqrt(variance);
      }

      function runSummary(run) {
        const stats = run.stats || {};
        const parts = [
          `${formatDuration((stats.duration_seconds || 0) * 1000)}`,
          Number.isFinite(stats.average_rpm) ? `${Math.round(stats.average_rpm)} rpm avg` : null,
          Number.isFinite(stats.max_rpm) ? `${Math.round(stats.max_rpm)} rpm max` : null,
          Number.isFinite(stats.average_map_kpa) ? `${stats.average_map_kpa.toFixed(1)} kPa avg` : null,
        ].filter(Boolean);
        return parts.join(" / ");
      }

      function formatDuration(ms) {
        const seconds = Math.max(0, Math.round(ms / 1000));
        const minutes = Math.floor(seconds / 60);
        const remainder = seconds % 60;
        return minutes > 0 ? `${minutes}m ${remainder}s` : `${remainder}s`;
      }

      function defaultRunName() {
        return new Intl.DateTimeFormat(undefined, { dateStyle: "short", timeStyle: "short" }).format(new Date());
      }

      refresh();
      refreshRuns();
      setInterval(refresh, 50);
    </script>
  </body>
</html>
"""
