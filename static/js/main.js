const stationForm = document.getElementById("station-form");
const stationResult = document.getElementById("station-result");
const simulationForm = document.getElementById("simulation-form");
const simIdInput = document.getElementById("sim-id");
const checkStatusBtn = document.getElementById("check-status");
const simulationStatus = document.getElementById("simulation-status");
const coveragePlot = document.getElementById("coverage-plot");
const analyticsSimId = document.getElementById("analytics-sim-id");
const analyticsResult = document.getElementById("analytics-result");
const runAnalyticsBtn = document.getElementById("run-analytics");
const summarySectors = document.getElementById("summary-sectors");
const summaryLayers = document.getElementById("summary-layers");

const apiBase = "/api";

const logJSON = (el, data) => {
  el.textContent = JSON.stringify(data, null, 2);
};

stationForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(stationForm).entries());
  const projectId = data.project_id;
  delete data.project_id;
  try {
    const res = await fetch(`${apiBase}/project/${projectId}/station`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    const json = await res.json();
    logJSON(stationResult, json);
  } catch (err) {
    stationResult.textContent = err.message;
  }
});

simulationForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(simulationForm).entries());
  try {
    const res = await fetch(`${apiBase}/simulation/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        station_id: Number(data.station_id),
        radius_km: Number(data.radius_km),
      }),
    });
    const json = await res.json();
    logJSON(simulationStatus, json);
    if (json.simulation_id) {
      simIdInput.value = json.simulation_id;
      checkStatusBtn.disabled = false;
      analyticsSimId.value = json.simulation_id;
    }
  } catch (err) {
    simulationStatus.textContent = err.message;
  }
});

checkStatusBtn?.addEventListener("click", async () => {
  const simId = simIdInput.value.trim();
  if (!simId) return;
  const res = await fetch(`${apiBase}/simulation/${simId}/status`);
  const json = await res.json();
  logJSON(simulationStatus, json);
  if (json.result_path) {
    coveragePlot.innerHTML = `<img src="/${json.result_path.replace(/^app\\//,'').replace(/^\\.\\//,'')}" alt="coverage">`;
  }
});

runAnalyticsBtn?.addEventListener("click", async () => {
  const simId = analyticsSimId.value.trim();
  if (!simId) return;
  const res = await fetch(`${apiBase}/analytics/population?simulation_id=${encodeURIComponent(simId)}`);
  const json = await res.json();
  logJSON(analyticsResult, json);
});

async function loadSummary() {
  try {
    const res = await fetch(`${apiBase}/analytics/summary`);
    const json = await res.json();
    summarySectors.textContent = `Setores: ${json.sectors ?? "--"}`;
    summaryLayers.textContent = `Camadas: ${json.layers ?? "--"}`;
  } catch {
    summarySectors.textContent = "Setores: --";
    summaryLayers.textContent = "Camadas: --";
  }
}
loadSummary();
