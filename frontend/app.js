const POZNAN = { lon: 16.9252, lat: 52.4064 };
const DEFAULT_RADIUS_KM = 15;
const GEOLOCATION_TIMEOUT_MS = 5000;
const REFRESH_INTERVAL_MS = 20000;
const MOVE_DEBOUNCE_MS = 300;
const LATE_THRESHOLD_MIN = 5;
const LABELS_MIN_ZOOM = 12;
const KM_PER_DEGREE_LAT = 111.32;

const statusEl = document.getElementById("status");
const css = getComputedStyle(document.documentElement);
const color = (name) => css.getPropertyValue(name).trim();

function boundsAround({ lon, lat }, radiusKm) {
  const dLat = radiusKm / KM_PER_DEGREE_LAT;
  const dLon = radiusKm / (KM_PER_DEGREE_LAT * Math.cos((lat * Math.PI) / 180));
  return L.latLngBounds([lat - dLat, lon - dLon], [lat + dLat, lon + dLon]);
}

function userLocation() {
  return new Promise((resolve) => {
    if (!("geolocation" in navigator)) return resolve(null);
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({ lon: pos.coords.longitude, lat: pos.coords.latitude }),
      () => resolve(null),
      { timeout: GEOLOCATION_TIMEOUT_MS, maximumAge: 300000, enableHighAccuracy: false },
    );
  });
}

const map = L.map("map", { preferCanvas: true, zoomSnap: 0.5, zoomControl: false });
L.control.zoom({ position: "topright" }).addTo(map);
map.fitBounds(boundsAround(POZNAN, DEFAULT_RADIUS_KM));
map.attributionControl.addAttribution('Dane: <a href="https://mkuran.pl/gtfs/">PKP PLK / mkuran.pl</a>');

L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  className: "base-tiles",
  attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);
L.tileLayer("https://{s}.tiles.openrailwaymap.org/standard/{z}/{x}/{y}.png", {
  maxZoom: 19,
  subdomains: "abc",
  opacity: 0.9,
  attribution: '<a href="https://www.openrailwaymap.org/">OpenRailwayMap</a> (CC-BY-SA)',
}).addTo(map);

const LocateControl = L.Control.extend({
  options: { position: "topright" },
  onAdd() {
    const button = L.DomUtil.create("button", "locate-button");
    button.type = "button";
    button.title = "Pokaż moje okolice";
    button.textContent = "◎";
    L.DomEvent.disableClickPropagation(button);
    button.addEventListener("click", async () => {
      const here = await userLocation();
      if (here) map.fitBounds(boundsAround(here, DEFAULT_RADIUS_KM));
      else statusEl.textContent = "Brak zgody na lokalizację albo jest niedostępna.";
    });
    return button;
  },
});
new LocateControl().addTo(map);

const trainsLayer = L.layerGroup().addTo(map);
const routeLayer = L.layerGroup().addTo(map);
const trainPopup = L.popup({ offset: [0, -4], autoPan: false });
let selectedTrainId = null;

function basisOf(p) {
  return p.position.estimate?.basedOn ?? "gps";
}

function isLate(p) {
  return (p.delayMinutes ?? 0) >= LATE_THRESHOLD_MIN;
}

function labelOf(p) {
  return [p.category, p.number].filter(Boolean).join(" ");
}

function markerRadius() {
  return Math.max(3, Math.min(9, map.getZoom() - 4));
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

function formatAge(iso) {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  return minutes <= 0 ? "przed chwilą" : `${minutes} min temu`;
}

const BASIS_LABEL = {
  gps: "GPS",
  confirmed: "szacowana (ostatni przejazd potwierdzony przez PLK)",
  forecast: "szacowana (prognoza PLK)",
  schedule: "szacowana (wg rozkładu)",
};

const STATUS_LABEL = {
  moving: "w drodze",
  at_stop: "na stacji",
  held: "czeka na potwierdzenie PLK",
  unknown: "",
};

function popupHtml(p) {
  const position = p.position;
  const est = position.estimate;
  const delay = p.delayMinutes == null ? "" :
    `<p class="${isLate(p) ? "late" : ""}">Opóźnienie: ${p.delayMinutes} min</p>`;
  const segment = !est ? "" :
    `<p>${escapeHtml(est.fromStop)}${est.fromStop !== est.toStop ? ` → ${escapeHtml(est.toStop)}` : ""}</p>`;
  const observed = position.observedAt ? ` · ${formatAge(position.observedAt)}` : "";
  return `<div class="popup">
    <h2>${escapeHtml(labelOf(p))} ${escapeHtml(p.name)}</h2>
    <p class="muted">${escapeHtml(p.carrier)}</p>
    ${p.origin ? `<p>${escapeHtml(p.origin)} → ${escapeHtml(p.destination)}</p>` : ""}
    ${delay}
    <p>${escapeHtml(STATUS_LABEL[p.status] ?? p.status)}</p>
    ${segment}
    <p class="muted">Pozycja: ${escapeHtml(BASIS_LABEL[basisOf(p)])}<br>
      Źródło: ${escapeHtml(position.source)}${observed}</p>
  </div>`;
}

async function showRoute(trainId) {
  routeLayer.clearLayers();
  if (trainId.startsWith("gps:")) return;
  const res = await fetch(`/api/v1/trains/${encodeURIComponent(trainId)}`);
  if (!res.ok || selectedTrainId !== trainId) return;
  const detail = await res.json();
  const latLngs = detail.route.coordinates.map(([lon, lat]) => [lat, lon]);
  L.polyline(latLngs, { color: color("--route"), weight: 4, opacity: 0.8, interactive: false }).addTo(routeLayer);
}

function selectTrain(feature) {
  selectedTrainId = feature.properties.trainId;
  const [lon, lat] = feature.geometry.coordinates;
  trainPopup.setLatLng([lat, lon]).setContent(popupHtml(feature.properties)).openOn(map);
  showRoute(selectedTrainId);
}

map.on("popupclose", () => {
  selectedTrainId = null;
  routeLayer.clearLayers();
});

function directionArrow(radius, bearing) {
  if (bearing == null) return "";
  const base = radius + 1;
  const tip = base + Math.max(7, radius * 1.4);
  const halfWidth = Math.max(4.5, radius * 0.85);
  return `<polygon class="arrow" points="${-halfWidth},${-base} ${halfWidth},${-base} 0,${-tip}"
    transform="rotate(${bearing})"/>`;
}

function trainIcon(p) {
  const radius = markerRadius();
  const half = Math.ceil(radius * 2.5 + 9);
  const classes = `train basis-${basisOf(p)}${isLate(p) ? " late" : ""}`;
  const svg = `<svg class="${classes}" width="${half * 2}" height="${half * 2}"
      viewBox="${-half} ${-half} ${half * 2} ${half * 2}">
    ${directionArrow(radius, p.bearing)}
    <circle class="body" r="${radius}"/>
  </svg>`;
  return { icon: L.divIcon({ className: "train-icon", html: svg, iconSize: [half * 2, half * 2] }), radius };
}

function trainMarker(feature) {
  const p = feature.properties;
  const [lon, lat] = feature.geometry.coordinates;
  const { icon, radius } = trainIcon(p);
  const marker = L.marker([lat, lon], { icon, keyboard: false });
  marker.on("click", () => selectTrain(feature));
  if (map.getZoom() >= LABELS_MIN_ZOOM) {
    marker.bindTooltip(escapeHtml(labelOf(p)), {
      permanent: true, direction: "bottom", offset: [0, radius + 2], className: "train-label",
    });
  }
  return marker;
}

function render(collection) {
  trainsLayer.clearLayers();
  for (const feature of collection.features) {
    trainsLayer.addLayer(trainMarker(feature));
    if (feature.properties.trainId === selectedTrainId) {
      const [lon, lat] = feature.geometry.coordinates;
      trainPopup.setLatLng([lat, lon]).setContent(popupHtml(feature.properties));
    }
  }
}

let inFlight = null;
let refreshTimer = null;

function bboxParam() {
  const b = map.getBounds();
  return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((v) => v.toFixed(5)).join(",");
}

function describeSources(sources) {
  const live = sources.find((s) => s.kind === "live");
  return live && live.status !== "ok" ? ` · dane live: ${live.status}` : "";
}

async function refresh() {
  clearTimeout(refreshTimer);
  inFlight?.abort();
  inFlight = new AbortController();
  try {
    const res = await fetch(`/api/v1/trains?bbox=${bboxParam()}`, { signal: inFlight.signal });
    if (res.status === 503) {
      statusEl.textContent = "Importuję rozkład jazdy…";
      refreshTimer = setTimeout(refresh, 5000);
      return;
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    render(data);
    const time = new Date(data.generatedAt).toLocaleTimeString("pl-PL");
    const overview = data.mode === "overview" ? " · widok uproszczony" : "";
    statusEl.textContent = `${data.features.length} pociągów · ${time}${overview}${describeSources(data.sources)}`;
  } catch (err) {
    if (err.name === "AbortError") return;
    statusEl.textContent = `Błąd pobierania: ${err.message}`;
  }
  refreshTimer = setTimeout(refresh, REFRESH_INTERVAL_MS);
}

function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

async function start() {
  statusEl.textContent = "Ustalam położenie…";
  const here = await userLocation();
  if (here) map.fitBounds(boundsAround(here, DEFAULT_RADIUS_KM), { animate: false });
  map.on("moveend", debounce(refresh, MOVE_DEBOUNCE_MS));
  refresh();
}

start();
