/**
 * Battery Manager Dashboard Card
 * Custom Lovelace card — 48h battery planning chart.
 *
 * Usage in Lovelace YAML:
 *   type: custom:battery-dashboard-card
 *   entity: sensor.battery_manager_chart
 *   title: "Batterij Planning – komende 48 uur"   # optional
 */

const CHART_JS_URL =
  "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js";

// Background highlight colours per action
const ACTION_COLORS = {
  all_on:      "rgba(239, 68,  68,  0.10)", // neutraal rood
  charge:      "rgba(34,  197, 94,  0.10)", // neutraal groen
  discharge:   "rgba(249, 115, 22,  0.10)", // neutraal oranje
  forced_off:  "rgba(107, 114, 128, 0.08)", // neutraal grijs
  normal:      "rgba(0,   0,   0,   0)",
};

// ─── Load Chart.js from CDN once ────────────────────────────────────────────
let _chartJsPromise = null;
function ensureChartJs() {
  if (_chartJsPromise) return _chartJsPromise;
  if (window.Chart) { _chartJsPromise = Promise.resolve(); return _chartJsPromise; }
  _chartJsPromise = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = CHART_JS_URL;
    s.onload = resolve;
    s.onerror = () => {
      _chartJsPromise = null;
      reject(new Error("Failed to load Chart.js from CDN"));
    };
    document.head.appendChild(s);
  });
  return _chartJsPromise;
}

// ─── Custom element ──────────────────────────────────────────────────────────
class BatteryDashboardCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._chart = null;
    this._lastHash = null;
  }

  // Called by Lovelace when the card config is parsed
  setConfig(config) {
    if (!config.entity) {
      throw new Error(
        "Battery Dashboard Card: geef 'entity' op, bijv. sensor.battery_manager_chart"
      );
    }
    this._config = {
      title: "Batterij Planning – komende 48 uur",
      ...config,
    };
    this._buildSkeleton();
  }

  // Called by Lovelace on every HA state update
  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;

    const stateObj = hass.states[this._config.entity];
    if (!stateObj) {
      this._showMessage(`Sensor '${this._config.entity}' niet gevonden`);
      return;
    }

    const chartData = stateObj.attributes.chart_data;
    if (!chartData || !chartData.length) {
      this._showMessage("Nog geen data beschikbaar — wacht op de eerste update.");
      return;
    }

    // Controleer of er echte prijsdata is (niet alleen null-waarden)
    const hasPrices = chartData.some((d) => d.price != null);
    if (!hasPrices) {
      this._showMessage("Geen prijsdata van Tibber beschikbaar — wacht op de volgende API-update.");
      // Toch doorgaan met tekenen zodat de SOC-lijn zichtbaar is
    }

    // Quick content-hash to skip unnecessary re-renders
    // Include first solar_w value so chart updates when Forecast.Solar data arrives
    const hash = `${chartData.length}|${chartData[0]?.starts_at}|${chartData[0]?.price}`;
    if (hash === this._lastHash) return;
    this._lastHash = hash;

    // Update "last updated" timestamp
    const lu = stateObj.attributes.last_updated;
    const updatedEl = this.shadowRoot.getElementById("updated");
    if (updatedEl && lu) {
      try {
        updatedEl.textContent =
          "bijgewerkt " +
          new Date(lu).toLocaleTimeString("nl-NL", {
            hour: "2-digit",
            minute: "2-digit",
          });
      } catch (_) { /* ignore */ }
    }

    ensureChartJs()
      .then(() => this._drawChart(chartData))
      .catch((err) => this._showMessage("Kan Chart.js niet laden: " + err.message));
  }

  // ── DOM skeleton ────────────────────────────────────────────────────────
  _buildSkeleton() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { overflow: hidden; }
        .header {
          padding: 14px 16px 4px;
          font-size: 1.05em;
          font-weight: 500;
          color: var(--primary-text-color);
          display: flex;
          align-items: baseline;
          justify-content: space-between;
        }
        .updated {
          font-size: 0.74em;
          color: var(--secondary-text-color);
          font-weight: normal;
        }
        .chart-wrap {
          position: relative;
          padding: 4px 12px 8px;
          cursor: zoom-in;
        }
        .chart-wrap::after {
          content: "⛶";
          position: absolute; bottom: 14px; right: 18px;
          font-size: 18px; color: rgba(255,255,255,0.35);
          pointer-events: none; line-height: 1;
        }
        .chart-wrap:hover::after { color: rgba(255,255,255,0.75); }
        .chart-click-overlay {
          position: absolute; inset: 0;
          z-index: 5; cursor: zoom-in;
          background: transparent;
        }
        canvas { display: block; width: 100% !important; }
        .expand-btn {
          display: none;
        }
        .legend {
          display: flex;
          flex-wrap: wrap;
          column-gap: 20px;
          row-gap: 10px;
          padding: 10px 16px 18px;
          font-size: 0.80em;
          color: var(--primary-text-color, #d1d5db);
        }
        .li { display: flex; align-items: center; gap: 6px; }
        .dot { width: 12px; height: 12px; border-radius: 2px; flex-shrink: 0; }
        .msg {
          padding: 20px 16px;
          color: var(--secondary-text-color);
          font-size: 0.9em;
        }
      </style>
      <ha-card>
        <div class="header">
          <span>${this._config.title}</span>
          <span style="display:flex;align-items:center;gap:8px;">
            <span class="updated" id="updated"></span>
            <button class="expand-btn" id="expand-btn" title="Vergroot grafiek">&#x26F6;</button>
          </span>
        </div>
        <div class="chart-wrap">
          <canvas id="chart"></canvas>
          <div class="chart-click-overlay" id="chart-overlay"></div>
        </div>
        <div class="legend">
          <div class="li">
            <div class="dot" style="background:#86efac;"></div>
            <span>Prijs (€/kWh)</span>
          </div>
          <div class="li">
            <div class="dot" style="background:rgba(255,255,255,0.8);border-radius:50%;"></div>
            <span>Accu SOC (%)</span>
          </div>
          <div class="li">
            <div class="dot" style="background:rgba(250,204,21,0.7);border-radius:2px;"></div>
            <span>Besparing (€)</span>
          </div>
        </div>
      </ha-card>
    `;
    this.shadowRoot.getElementById("expand-btn").addEventListener("click", () => {
      if (this._chartData) this._openModal(this._chartData);
    });
    this.shadowRoot.getElementById("chart-overlay").addEventListener("click", () => {
      if (this._chartData) this._openModal(this._chartData);
    });
  }

  _showMessage(msg) {
    // Show message WITHOUT destroying the canvas so it can recover when data returns
    const wrap = this.shadowRoot.querySelector(".chart-wrap");
    if (!wrap) return;
    let overlay = wrap.querySelector(".msg-overlay");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.className = "msg-overlay msg";
      overlay.style.cssText = "position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:var(--card-background-color,#1c1c1c);z-index:10;";
      wrap.appendChild(overlay);
    }
    overlay.textContent = msg;
  }

  _clearMessage() {
    const overlay = this.shadowRoot.querySelector(".msg-overlay");
    if (overlay) overlay.remove();
  }

  // ── Chart drawing ────────────────────────────────────────────────────────
  _drawChart(data) {
    this._chartData = data;
    this._clearMessage();
    const canvas = this.shadowRoot.getElementById("chart");
    if (!canvas) return;
    if (this._chart) { this._chart.destroy(); this._chart = null; }
    try {
      // Defensive: if data is missing or malformed, show a message and skip rendering
      if (!Array.isArray(data) || data.length === 0) {
        this._showMessage("Nog geen data beschikbaar — wacht op de eerste update.");
        return;
      }
      // Defensive: check for required fields in at least one data point
      const hasValid = data.some(d => d && typeof d === 'object' && 'price' in d && 'time' in d);
      if (!hasValid) {
        this._showMessage("Dataformaat ongeldig — controleer sensor output.");
        return;
      }
      this._chart = new Chart(canvas, this._buildChartConfig(data));
    } catch (err) {
      this._showMessage("Fout bij tekenen grafiek: " + (err?.message || err));
    }
    // Always enable fullscreen button, even if chart is empty
    const expandBtn = this.shadowRoot.getElementById("expand-btn");
    if (expandBtn) expandBtn.disabled = false;
  }

  // ── Chart config builder (shared between card and fullscreen modal) ───────
  _buildChartConfig(data) {
    // Find which quarter is "now"
    const now = Date.now();
    let nowIndex = 0;
    let minDiff = Infinity;
    data.forEach((d, i) => {
      if (d.starts_at) {
        const diff = Math.abs(new Date(d.starts_at).getTime() - now);
        if (diff < minDiff) { minDiff = diff; nowIndex = i; }
      }
    });

    // X-axis labels: show every 2 uur (elke 8 kwartieren); eerste item altijd tonen
    const labels = data.map((d, i) => (i === 0 || i % 8 === 0 ? d.time : ""));

    const prices = data.map((d) => (d.price != null ? +d.price : null));
    // gebruik verwijderd

    // Cumulatieve besparing in € t.o.v. geen batterij
    let _cumSavings = 0;
    const cumSavings = data.map((d) => {
      _cumSavings += d.savings_delta ?? 0;
      return +_cumSavings.toFixed(4);
    });

    // SOC: split into past (actual, faded) and forecast (predicted, bright).
    // Python sets battery_soc=null for past quarters, non-null from 'now' onward.
    // Treat 0 as null — a real SOC of 0 % would shut the inverter down anyway.
    const socForecast = data.map((d) =>
      (d.battery_soc != null && d.battery_soc > 0) ? +d.battery_soc : null
    );
    // Find the first non-null SOC index (= nowIndex or first future quarter)
    const socStartIndex = socForecast.findIndex((v) => v != null);
    const socAnchor = socForecast.map((v, i) => (i === socStartIndex ? v : null));
    const socPointRadius = socForecast.map((v, i) => (i === socStartIndex ? 5 : 0));
    const socPointBg     = socForecast.map((v, i) => (i === socStartIndex ? "rgba(255,255,255,0.95)" : "transparent"));

    // ── Plugin: action background bands ─────────────────────────────────
    const bgPlugin = {
      id: "actionBg",
      beforeDraw(chart) {
        const { ctx, chartArea, scales } = chart;
        if (!chartArea) return;
        const barW = (chartArea.right - chartArea.left) / data.length;
        ctx.save();
        data.forEach((d, i) => {
          const col = ACTION_COLORS[d.action] || "rgba(0,0,0,0)";
          if (col === "rgba(0,0,0,0)") return;
          ctx.fillStyle = col;
          ctx.fillRect(
            chartArea.left + i * barW,
            chartArea.top,
            barW,
            chartArea.bottom - chartArea.top
          );
        });
        ctx.restore();
      },
    };

    // ── Plugin: vertical "nu" line ────────────────────────────────────────
    const nowPlugin = {
      id: "nowLine",
      afterDraw(chart) {
        const { ctx, chartArea } = chart;
        if (!chartArea) return;
        const barW = (chartArea.right - chartArea.left) / data.length;
        const x = chartArea.left + (nowIndex + 0.5) * barW;
        ctx.save();
        ctx.setLineDash([5, 4]);
        ctx.strokeStyle = "rgba(239, 68, 68, 0.9)";
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(x, chartArea.top);
        ctx.lineTo(x, chartArea.bottom);
        ctx.stroke();
        // "nu" label
        ctx.setLineDash([]);
        ctx.fillStyle = "rgba(239, 68, 68, 0.9)";
        ctx.font = "bold 10px sans-serif";
        ctx.textAlign = "center";
        ctx.fillText("nu", x, chartArea.top - 4);
        ctx.restore();
      },
    };

    return {
      data: {
        labels,
        datasets: [
          {
            // ── Prijs ── right axis, stepped line (prijs per kwartier)
            type: "line",
            label: "Prijs",
            data: prices,
            yAxisID: "yPrice",
            borderColor: "#86efac",
            backgroundColor: "rgba(134,239,172,0.06)",
            fill: true,
            stepped: "before",
            tension: 0,
            pointRadius: 0,
            borderWidth: 2,
            spanGaps: true,
            order: 1,
          },
          // gebruik dataset verwijderd
          {
            // ── Cumulatieve besparing ── linker-as, lijn
            type: "line",
            label: "Besparing",
            data: cumSavings,
            yAxisID: "ySavings",
            borderColor: "rgba(250,204,21,0.90)",
            backgroundColor: "rgba(250,204,21,0.08)",
            fill: true,
            tension: 0,
            pointRadius: 0,
            borderWidth: 1.5,
            borderDash: [4, 3],
            spanGaps: true,
            order: 3,
          },
          {
            // ── Batterij SOC voorspelling ── rechter-as, lijn vanaf 'nu'
            type: "line",
            label: "Accu SOC",
            data: socForecast,
            yAxisID: "ySOC",
            borderColor: "rgba(255, 255, 255, 0.90)",
            backgroundColor: "rgba(255, 255, 255, 0.06)",
            fill: true,
            tension: 0.3,
            pointRadius: socPointRadius,
            pointBackgroundColor: socPointBg,
            pointBorderColor: "rgba(255,255,255,0.95)",
            pointBorderWidth: 2,
            borderWidth: 2.5,
            spanGaps: false,
            order: 2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        animation: { duration: 250 },
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "rgba(17,24,39,0.92)",
            titleColor: "#f9fafb",
            bodyColor:  "#d1d5db",
            padding: 10,
            callbacks: {
              title(items) {
                const i = items[0].dataIndex;
                return data[i]?.time ?? "";
              },
              label(item) {
                if (item.dataset.yAxisID === "yPrice") {
                  const v = item.raw;
                  return v != null
                    ? ` Prijs: €${v.toFixed(4)}/kWh`
                    : " Prijs: onbekend";
                }
                if (item.dataset.label === "Accu SOC") {
                  const v = item.raw;
                  return v != null ? ` Voorspeld accu: ${v.toFixed(1)}%` : null;
                }
                if (item.dataset.label === "Besparing") {
                  const v = item.raw;
                  const sign = v >= 0 ? "+" : "";
                  return v != null ? ` Besparing: ${sign}€${v.toFixed(3)}` : null;
                }
                return null;
              },
              afterBody(items) {
                const i = items[0].dataIndex;
                const d = data[i];
                const action = d?.action;
                const flow   = d?.battery_flow_w ?? 0;
                const lines = [];
                // actie-labels verwijderd
                const lbl = action || "";
                if (lbl) lines.push(lbl);
                if (flow > 0)  lines.push(`   ↑ Laden:    ${flow} W`);
                if (flow < 0)  lines.push(`   ↓ Ontladen: ${Math.abs(flow)} W`);
                return lines;
              },
            },
          },
        },
        scales: {
          x: {
            ticks: {
              autoSkip: false,
              maxRotation: 45,
              minRotation: 45,
              color: "var(--secondary-text-color, #6b7280)",
              font: { size: 10 },
            },
            grid: { color: "rgba(128,128,128,0.12)" },
          },
          yPrice: {
            type: "linear",
            position: "right",
            title: {
              display: true,
              text: "€/kWh",
              color: "#86efac",
              font: { size: 11 },
            },
            ticks: {
              color: "#86efac",
              font: { size: 10 },
              callback: (v) => `€${v.toFixed(2)}`,
            },
            grid: { drawOnChartArea: false },
          },
          ySOC: {
            type: "linear",
            position: "right",
            min: 0,
            max: 100,
            title: {
              display: true,
              text: "SOC %",
              color: "rgba(255,255,255,0.7)",
              font: { size: 11 },
            },
            ticks: {
              color: "rgba(255,255,255,0.7)",
              font: { size: 10 },
              callback: (v) => `${v}%`,
              stepSize: 20,
            },
            grid: { drawOnChartArea: false },
            // Offset so it doesn't overlap with the price axis
            offset: true,
          },
          ySavings: {
            type: "linear",
            position: "left",
            title: {
              display: true,
              text: "€ bespaard",
              color: "rgba(250,204,21,0.85)",
              font: { size: 11 },
            },
            ticks: {
              color: "rgba(250,204,21,0.85)",
              font: { size: 10 },
              callback: (v) => `€${v.toFixed(2)}`,
            },
            grid: { drawOnChartArea: false },
          },
        },
      },
      plugins: [bgPlugin, nowPlugin],
    };
  }

  // ── Fullscreen modal ─────────────────────────────────────────────────────
  _openModal(data) {
    this._closeModal();

    const overlay = document.createElement("div");
    overlay.style.cssText =
      "position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.82);" +
      "display:flex;align-items:center;justify-content:center;" +
      "padding:20px;box-sizing:border-box;";

    const box = document.createElement("div");
    box.style.cssText =
      "background:var(--card-background-color,#1e1e2e);border-radius:12px;" +
      "padding:20px;width:100%;max-width:1400px;max-height:92vh;overflow:auto;" +
      "position:relative;box-shadow:0 8px 40px rgba(0,0,0,0.7);";

    const closeBtn = document.createElement("button");
    closeBtn.textContent = "✕";
    closeBtn.title = "Sluiten (Esc)";
    closeBtn.style.cssText =
      "position:absolute;top:10px;right:14px;background:rgba(255,255,255,0.1);" +
      "border:1px solid rgba(255,255,255,0.2);border-radius:5px;" +
      "color:rgba(255,255,255,0.85);font-size:16px;cursor:pointer;" +
      "padding:2px 9px;line-height:1.6;";
    closeBtn.addEventListener("click", () => this._closeModal());

    const titleEl = document.createElement("div");
    titleEl.textContent = this._config.title;
    titleEl.style.cssText =
      "font-size:1.1em;font-weight:500;color:var(--primary-text-color,#f9fafb);" +
      "margin-bottom:14px;padding-right:40px;";

    const canvas = document.createElement("canvas");
    canvas.style.cssText = "display:block;width:100%!important;height:65vh;";

    box.appendChild(closeBtn);
    box.appendChild(titleEl);
    box.appendChild(canvas);
    overlay.appendChild(box);
    document.body.appendChild(overlay);

    // Close on backdrop click
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) this._closeModal();
    });

    // Close on Escape
    this._modalKeyHandler = (e) => { if (e.key === "Escape") this._closeModal(); };
    document.addEventListener("keydown", this._modalKeyHandler);

    // Draw chart
    const cfg = this._buildChartConfig(data);
    cfg.options.maintainAspectRatio = false;
    this._modalChart = new Chart(canvas, cfg);
    this._modalOverlay = overlay;
  }

  _closeModal() {
    if (this._modalChart) { this._modalChart.destroy(); this._modalChart = null; }
    if (this._modalOverlay) { this._modalOverlay.remove(); this._modalOverlay = null; }
    if (this._modalKeyHandler) {
      document.removeEventListener("keydown", this._modalKeyHandler);
      this._modalKeyHandler = null;
    }
  }

  getCardSize() { return 5; }

  static getStubConfig() {
    return { entity: "sensor.battery_manager_chart" };
  }
}

customElements.define("battery-dashboard-card", BatteryDashboardCard);

// Register with HA custom cards picker
window.customCards = window.customCards || [];
if (!window.customCards.find((c) => c.type === "battery-dashboard-card")) {
  window.customCards.push({
    type: "battery-dashboard-card",
    name: "Battery Manager Dashboard",
    description:
      "48u planning: stroomprijzen en geschat verbruik per kwartier.",
    preview: false,
  });
}
