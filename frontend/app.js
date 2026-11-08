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
    const button = L.DomUtil.create("button", "map-button");
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

function trainIdentifier({ carrierId, number }) {
  return carrierId && number ? `${carrierId}-${number.split("/")[0]}` : null;
}

function appUrl(params) {
  return `${location.origin}${location.pathname}?${new URLSearchParams(params)}`;
}

function shareButtonHtml(url) {
  return url ? `<button type="button" class="share-button" data-url="${escapeHtml(url)}">Udostępnij</button>` : "";
}

function routeButtonHtml(trainId) {
  if (!trainId || trainId.startsWith("gps:")) return "";
  return `<button type="button" class="route-button" data-train-id="${escapeHtml(trainId)}">Trasa</button>`;
}

function trainActionsHtml(train, url) {
  return `<div class="popup-actions">${shareButtonHtml(url)}${routeButtonHtml(train.trainId)}</div>`;
}

function trainShareUrl(train) {
  const identifier = trainIdentifier(train);
  return identifier ? appUrl({ follow: identifier }) : null;
}

function stationShareUrl(station) {
  return appUrl({ station: station.stationId });
}

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
    ${trainActionsHtml(p, trainShareUrl(p))}
  </div>`;
}

const SHARE_FEEDBACK_MS = 2000;

function copyWithTextarea(text) {
  const area = document.createElement("textarea");
  area.value = text;
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  const copied = document.execCommand("copy");
  area.remove();
  return copied;
}

async function copyToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return true;
  }
  return copyWithTextarea(text);
}

document.addEventListener("click", async (e) => {
  const button = e.target.closest(".share-button");
  if (!button) return;
  const url = button.dataset.url;
  const copied = await copyToClipboard(url).catch(() => false);
  if (!copied) {
    window.prompt("Skopiuj link do pociągu:", url);
    return;
  }
  button.textContent = "Skopiowano link ✓";
  setTimeout(() => { button.textContent = "Udostępnij"; }, SHARE_FEEDBACK_MS);
});

async function showRoute(trainId) {
  routeLayer.clearLayers();
  if (trainId.startsWith("gps:")) return null;
  const res = await fetch(`/api/v1/trains/${encodeURIComponent(trainId)}`);
  if (!res.ok || selectedTrainId !== trainId) return null;
  const detail = await res.json();
  const latLngs = detail.route.coordinates.map(([lon, lat]) => [lat, lon]);
  const line = L.polyline(latLngs, { color: color("--route"), weight: 6, opacity: 0.85, interactive: false });
  line.addTo(routeLayer);
  return { bounds: line.getBounds(), stops: detail.stops };
}

const urlState = {
  read() {
    const params = new URLSearchParams(location.search);
    const [lat, lon, zoom] = ["lat", "lon", "z"].map((k) => Number.parseFloat(params.get(k)));
    const viewIsValid = Math.abs(lat) <= 90 && Math.abs(lon) <= 180 && zoom >= 0 && zoom <= 19;
    return {
      view: viewIsValid ? { lat, lon, zoom } : null,
      query: params.get("q") ?? "",
      trainId: params.get("train"),
      follow: parseFollowParam(params.get("follow"), params.get("date")),
      stationId: params.get("station"),
    };
  },
  write(changes) {
    const params = new URLSearchParams(location.search);
    for (const [key, value] of Object.entries(changes)) {
      if (value) params.set(key, value);
      else params.delete(key);
    }
    const search = params.toString();
    history.replaceState(null, "", search ? `?${search}` : location.pathname);
  },
};

function viewParams() {
  const center = map.getCenter();
  return { lat: center.lat.toFixed(5), lon: center.lng.toFixed(5), z: String(map.getZoom()) };
}

const FOLLOW_PARAM = /^([A-Za-z0-9]{1,8})-([0-9]{1,6})$/;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const FOLLOW_KEEP_IN_VIEW_PADDING = -0.2;
let followed = null;

function parseFollowParam(raw, date) {
  const match = FOLLOW_PARAM.exec(raw ?? "");
  if (!match) return null;
  return { carrier: match[1], number: match[2], date: ISO_DATE.test(date ?? "") ? date : null, trainId: null };
}

function stopFollowing() {
  if (!followed) return;
  followed = null;
  urlState.write({ follow: null, date: null });
}

async function resolveFollowed() {
  const params = new URLSearchParams({ carrier: followed.carrier, number: followed.number });
  if (followed.date) params.set("date", followed.date);
  const res = await fetch(`/api/v1/follow?${params}`);
  return res.ok ? res.json() : null;
}

async function trackFollowedTrain() {
  const result = await resolveFollowed();
  if (!followed || !result || result.trainId !== followed.trainId) return;
  if (!result.train) {
    trainPopup.setContent(notRunningPopupHtml(result));
    return;
  }
  const [lon, lat] = result.train.geometry.coordinates;
  trainPopup.setLatLng([lat, lon]).setContent(popupHtml(result.train.properties));
  if (!map.getBounds().pad(FOLLOW_KEEP_IN_VIEW_PADDING).contains([lat, lon])) map.panTo([lat, lon]);
}

async function startFollowing(follow) {
  followed = follow;
  showInSearchInput(`${follow.carrier}-${follow.number}`);
  const result = await resolveFollowed().catch(() => null);
  if (!result) {
    statusEl.textContent = `Nie znaleziono pociągu ${follow.carrier} ${follow.number}.`;
    stopFollowing();
    return;
  }
  followed.trainId = result.trainId;
  focusResult(result);
}

function setSelectedTrain(trainId) {
  if (followed?.trainId && trainId !== followed.trainId) stopFollowing();
  selectedTrainId = trainId;
  urlState.write({ train: trainId });
}

map.on("dragstart", stopFollowing);

function selectTrain(feature) {
  setSelectedTrain(feature.properties.trainId);
  const [lon, lat] = feature.geometry.coordinates;
  trainPopup.setLatLng([lat, lon]).setContent(popupHtml(feature.properties)).openOn(map);
  showInSearchInput(trainIdentifier(feature.properties));
  showRoute(selectedTrainId);
}

trainPopup.on("remove", () => {
  clearAutoFilledSearchInput();
  stopFollowing();
  setSelectedTrain(null);
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

function trainCountText(n) {
  if (n === 1) return "1 pociąg";
  const lastDigit = n % 10;
  const lastTwoDigits = n % 100;
  const isPaucal = lastDigit >= 2 && lastDigit <= 4 && !(lastTwoDigits >= 12 && lastTwoDigits <= 14);
  return `${n} ${isPaucal ? "pociągi" : "pociągów"}`;
}

async function refresh() {
  clearTimeout(refreshTimer);
  inFlight?.abort();
  inFlight = new AbortController();
  try {
    if (followed?.trainId) await trackFollowedTrain().catch(() => null);
    refreshRouteDialog().catch(() => null);
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
    const following = followed ? ` · śledzę ${followed.carrier} ${followed.number}` : "";
    statusEl.textContent =
      `${trainCountText(data.features.length)} · ${time}${overview}${following}${describeSources(data.sources)}`;
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

const FOCUS_MIN_ZOOM = 13;
const SEARCH_DEBOUNCE_MS = 350;
const SEARCH_MIN_LENGTH = 2;
const COMPASS_POINTS = [
  "północ", "północny wschód", "wschód", "południowy wschód",
  "południe", "południowy zachód", "zachód", "północny zachód",
];

const searchForm = document.getElementById("search-form");
const searchInput = document.getElementById("search-input");
const searchResultsEl = document.getElementById("search-results");
let searchController = null;
let autoFilledSearchValue = null;

function showInSearchInput(value) {
  if (!value) return;
  searchInput.value = value;
  autoFilledSearchValue = value;
  urlState.write({ q: null });
}

function clearAutoFilledSearchInput() {
  if (autoFilledSearchValue !== null && searchInput.value === autoFilledSearchValue) searchInput.value = "";
  autoFilledSearchValue = null;
}

function compassPoint(bearing) {
  return COMPASS_POINTS[Math.round(bearing / 45) % COMPASS_POINTS.length];
}

function clockTime(iso) {
  return new Date(iso).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
}

function currentPlaceText(p) {
  const est = p.position.estimate;
  if (!est) return "pozycja GPS";
  if (p.status === "moving") return `${est.fromStop} → ${est.toStop}`;
  if (p.status === "held") return `przed stacją ${est.toStop}`;
  return `na stacji ${est.fromStop}`;
}

function positionText(result) {
  switch (result.state) {
    case "running": return currentPlaceText(result.train.properties);
    case "not_started": return `odjazd ${clockTime(result.departure)}`;
    case "finished": return `zakończył kurs o ${clockTime(result.arrival)}`;
    default: return "odwołany";
  }
}

function directionHtml(result) {
  const bearing = result.train?.properties.bearing;
  if (bearing == null) return "";
  return `<span class="heading"><span class="heading-arrow" style="transform: rotate(${bearing}deg)">↑</span>
    ${escapeHtml(compassPoint(bearing))}</span>`;
}

function delayHtml(result) {
  const delay = result.train?.properties.delayMinutes;
  if (!delay) return "";
  return ` <span class="${delay >= LATE_THRESHOLD_MIN ? "late" : "muted"}">+${delay} min</span>`;
}

function trainItemHtml(result, index) {
  const title = [result.category, result.number, result.name].filter(Boolean).join(" ");
  return `<li><button type="button" class="result state-${result.state}" data-index="${index}">
    <span class="result-title">${escapeHtml(title)}${delayHtml(result)}</span>
    <span class="result-course">${escapeHtml(result.origin)} → ${escapeHtml(result.destination)}</span>
    <span class="result-position">${escapeHtml(positionText(result))} ${directionHtml(result)}</span>
  </button></li>`;
}

function stationItemHtml(station, index) {
  return `<li><button type="button" class="result station" data-index="${index}">
    <span class="result-title">${escapeHtml(station.name)}</span>
    <span class="result-course">stacja kolejowa</span>
  </button></li>`;
}

function sectionHtml(title, items) {
  return items.length ? `<li class="result-section">${title}</li>${items.join("")}` : "";
}

function showResults(found, message) {
  searchResultsEl.hidden = false;
  const entries = [
    ...found.stations.map((station) => ({ station })),
    ...found.trains.map((train) => ({ train })),
  ];
  if (!entries.length) {
    const text = message ?? "Brak stacji i pociągów o tej nazwie lub numerze.";
    searchResultsEl.innerHTML = `<li class="result-empty">${escapeHtml(text)}</li>`;
    return;
  }
  const stationItems = found.stations.map((s, i) => stationItemHtml(s, i));
  const trainItems = found.trains.map((r, i) => trainItemHtml(r, found.stations.length + i));
  searchResultsEl.innerHTML = sectionHtml("Stacje", stationItems) + sectionHtml("Pociągi", trainItems);
  searchResultsEl.querySelectorAll("button.result").forEach((button) => {
    button.addEventListener("click", () => focusEntry(entries[Number(button.dataset.index)]));
  });
}

function hideResults() {
  searchResultsEl.hidden = true;
  searchResultsEl.innerHTML = "";
}

function focusRunningTrain(feature) {
  const [lon, lat] = feature.geometry.coordinates;
  map.setView([lat, lon], Math.max(map.getZoom(), FOCUS_MIN_ZOOM));
  selectTrain(feature);
}

function notRunningPopupHtml(result) {
  const title = [result.category, result.number, result.name].filter(Boolean).join(" ");
  return `<div class="popup">
    <h2>${escapeHtml(title)}</h2>
    <p class="muted">${escapeHtml(result.carrier)}</p>
    <p>${escapeHtml(result.origin)} → ${escapeHtml(result.destination)}</p>
    <p>${escapeHtml(positionText(result))}</p>
    ${trainActionsHtml(result, trainShareUrl(result))}
  </div>`;
}

async function focusNotRunningTrain(result) {
  setSelectedTrain(result.trainId);
  const route = await showRoute(result.trainId);
  if (!route) return;
  map.fitBounds(route.bounds, { padding: [30, 30] });
  const [lon, lat] = route.stops[0].coordinates;
  trainPopup.setLatLng([lat, lon]).setContent(notRunningPopupHtml(result)).openOn(map);
  showInSearchInput(trainIdentifier(result));
}

function focusResult(result) {
  hideResults();
  if (result.train) focusRunningTrain(result.train);
  else focusNotRunningTrain(result);
}

const STATION_ZOOM = 15;
const stationPopup = L.popup({ offset: [0, -4] });
stationPopup.on("remove", () => {
  clearAutoFilledSearchInput();
  urlState.write({ station: null });
});

function stationPopupHtml(station) {
  return `<div class="popup">
    <h2>${escapeHtml(station.name)}</h2>
    <p class="muted">stacja kolejowa</p>
    ${shareButtonHtml(stationShareUrl(station))}
  </div>`;
}

function focusStation(station) {
  hideResults();
  const [lon, lat] = station.coordinates;
  map.setView([lat, lon], STATION_ZOOM);
  stationPopup.setLatLng([lat, lon]).setContent(stationPopupHtml(station)).openOn(map);
  showInSearchInput(station.name);
  urlState.write({ station: station.stationId });
}

function focusEntry(entry) {
  if (entry.station) focusStation(entry.station);
  else focusResult(entry.train);
  if (isNarrowScreen()) setPanelCollapsed(true);
}

function unambiguousEntry(found) {
  const exactStation = found.stations.find((s) => s.exactMatch);
  if (exactStation) return { station: exactStation };
  const running = found.trains.filter((r) => r.state === "running");
  if (!found.stations.length && running.length === 1) return { train: running[0] };
  if (found.stations.length + found.trains.length !== 1) return null;
  return found.stations.length ? { station: found.stations[0] } : { train: found.trains[0] };
}

async function fetchSearch(query) {
  searchController?.abort();
  searchController = new AbortController();
  const res = await fetch(`/api/v1/search?q=${encodeURIComponent(query)}`, { signal: searchController.signal });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const body = await res.json();
  return { stations: body.stations, trains: body.results };
}

async function runSearch({ focusIfUnambiguous }) {
  const query = searchInput.value.trim();
  urlState.write({ q: query });
  if (query.length < SEARCH_MIN_LENGTH) return hideResults();
  try {
    const found = await fetchSearch(query);
    const unambiguous = focusIfUnambiguous ? unambiguousEntry(found) : null;
    if (unambiguous) focusEntry(unambiguous);
    else showResults(found);
  } catch (err) {
    if (err.name !== "AbortError") showResults({ stations: [], trains: [] }, `Błąd wyszukiwania: ${err.message}`);
  }
}

searchForm.addEventListener("submit", (e) => {
  e.preventDefault();
  runSearch({ focusIfUnambiguous: true });
});
searchInput.addEventListener("input", debounce(() => runSearch({ focusIfUnambiguous: false }), SEARCH_DEBOUNCE_MS));
searchInput.addEventListener("keydown", (e) => {
  if (e.key === "Escape") hideResults();
});

const routeDialog = document.getElementById("route-dialog");
const routeTitleEl = document.getElementById("route-title");
const routeSubtitleEl = document.getElementById("route-subtitle");
const routeStopsEl = document.getElementById("route-stops");
let routeDialogTrainId = null;

const STOP_PROGRESS_LABEL = { current: "pociąg na stacji", next: "następna stacja" };
const TIME_BASIS_TITLE = { confirmed: "potwierdzone przez PLK", forecast: "prognoza PLK" };

function delayBadgeHtml(delay, basis) {
  const forecastClass = basis === "forecast" ? " forecast" : "";
  const title = escapeHtml(TIME_BASIS_TITLE[basis]);
  if (delay === 0) return `<span class="delay-badge on-time${forecastClass}" title="${title}">o czasie</span>`;
  const severity = delay < 0 ? "early" : delay >= LATE_THRESHOLD_MIN ? "late" : "slight";
  const text = delay > 0 ? `+${delay} min` : `−${-delay} min`;
  return `<span class="delay-badge ${severity}${forecastClass}" title="${title}">${text}</span>`;
}

function stopTimeHtml(label, scheduled, actual, delay, basis) {
  const labelHtml = `<span class="route-time-label">${label}</span>`;
  if (basis === "schedule" || delay == null) {
    return `<span class="route-time">${labelHtml} ${clockTime(scheduled ?? actual)}</span>`;
  }
  const scheduledHtml = delay !== 0 ? `<s>${clockTime(scheduled)}</s> ` : "";
  return `<span class="route-time">${labelHtml} ${scheduledHtml}${clockTime(actual)} ${delayBadgeHtml(delay, basis)}</span>`;
}

function displayProgress(stops) {
  const firstUpcoming = stops.findIndex((s) => s.progress === "upcoming");
  const trainIsUnderway = stops.some((s) => s.progress !== "upcoming");
  return stops.map((s, i) => (i === firstUpcoming && trainIsUnderway ? "next" : s.progress));
}

function routeStopHtml(stop, progress, index, count) {
  const arrival = index === 0 ? "" : stopTimeHtml("przyj.", stop.scheduledArrival, stop.arrival,
    stop.arrivalDelayMinutes, stop.timeBasis);
  const departure = index === count - 1 ? "" : stopTimeHtml("odj.", stop.scheduledDeparture, stop.departure,
    stop.departureDelayMinutes, stop.timeBasis);
  const label = STOP_PROGRESS_LABEL[progress];
  return `<li class="route-stop ${progress}">
    <span class="route-dot" aria-hidden="true"></span>
    <span class="route-stop-name">${escapeHtml(stop.name)}${label ? `<span class="route-stop-label">${label}</span>` : ""}</span>
    <span class="route-stop-times">${arrival}${departure}</span>
  </li>`;
}

function renderRouteDialog(detail) {
  const summary = detail.summary;
  routeTitleEl.textContent = [summary.category, summary.number, summary.name].filter(Boolean).join(" ");
  routeSubtitleEl.textContent = `${summary.origin} → ${summary.destination} · ${summary.carrier ?? ""}`;
  const progress = displayProgress(detail.stops);
  routeStopsEl.innerHTML = detail.stops
    .map((stop, i) => routeStopHtml(stop, progress[i], i, detail.stops.length))
    .join("");
}

async function fetchTrainDetail(trainId) {
  const res = await fetch(`/api/v1/trains/${encodeURIComponent(trainId)}`);
  return res.ok ? res.json() : null;
}

async function openRouteDialog(trainId) {
  const detail = await fetchTrainDetail(trainId);
  if (!detail?.summary) return;
  routeDialogTrainId = trainId;
  renderRouteDialog(detail);
  routeDialog.showModal();
  routeStopsEl.querySelector(".current, .next")?.scrollIntoView({ block: "center" });
}

async function refreshRouteDialog() {
  if (!routeDialog.open || !routeDialogTrainId) return;
  const detail = await fetchTrainDetail(routeDialogTrainId);
  if (!detail?.summary || !routeDialog.open) return;
  const scrollTop = routeStopsEl.scrollTop;
  renderRouteDialog(detail);
  routeStopsEl.scrollTop = scrollTop;
}

function bindDialogClosing(dialog) {
  dialog.querySelector(".dialog-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog) dialog.close();
  });
}

document.querySelectorAll(".app-dialog").forEach(bindDialogClosing);
routeDialog.addEventListener("close", () => { routeDialogTrainId = null; });
document.addEventListener("click", (e) => {
  const button = e.target.closest(".route-button");
  if (button) openRouteDialog(button.dataset.trainId);
});

async function focusSharedStation(stationId) {
  const res = await fetch(`/api/v1/stations/${encodeURIComponent(stationId)}`);
  if (res.ok) focusStation(await res.json());
  else urlState.write({ station: null });
}

async function focusSharedTrain(trainId) {
  const res = await fetch(`/api/v1/trains/${encodeURIComponent(trainId)}`);
  const summary = res.ok ? (await res.json()).summary : null;
  if (summary) focusResult(summary);
  else urlState.write({ train: null });
}

async function showInitialView(sharedView) {
  if (sharedView) {
    map.setView([sharedView.lat, sharedView.lon], sharedView.zoom, { animate: false });
    return;
  }
  statusEl.textContent = "Ustalam położenie…";
  const here = await userLocation();
  if (here) map.fitBounds(boundsAround(here, DEFAULT_RADIUS_KM), { animate: false });
}

async function start() {
  const shared = urlState.read();
  searchInput.value = shared.query;
  const linkFocusesSomething = Boolean(shared.follow || shared.trainId || shared.stationId);
  if (shared.view || !linkFocusesSomething) await showInitialView(shared.view);
  urlState.write(viewParams());
  map.on("moveend", () => urlState.write(viewParams()));
  map.on("moveend", debounce(refresh, MOVE_DEBOUNCE_MS));
  refresh();
  if (shared.follow) startFollowing(shared.follow);
  else if (shared.trainId) focusSharedTrain(shared.trainId);
  else if (shared.stationId) focusSharedStation(shared.stationId);
  else if (shared.query) runSearch({ focusIfUnambiguous: !shared.view });
}

function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker.register("sw.js").catch((err) => console.warn("Service worker nie został zarejestrowany:", err));
}

window.addEventListener("offline", () => { statusEl.textContent = "Brak połączenia z internetem – dane nie są aktualne."; });
window.addEventListener("online", () => refresh());

const PANEL_COLLAPSED_KEY = "trainspotter.panelCollapsed";
const NARROW_SCREEN_QUERY = window.matchMedia("(max-width: 600px)");
const panelEl = document.getElementById("panel");
const panelToggle = document.getElementById("panel-toggle");

function isNarrowScreen() {
  return NARROW_SCREEN_QUERY.matches;
}

function readStoredPanelCollapsed() {
  try {
    const stored = localStorage.getItem(PANEL_COLLAPSED_KEY);
    return stored === null ? null : stored === "true";
  } catch {
    return null;
  }
}

function storePanelCollapsed(collapsed) {
  try {
    localStorage.setItem(PANEL_COLLAPSED_KEY, String(collapsed));
  } catch {
    // Storage may be blocked; remembering the panel state is optional.
  }
}

function setPanelCollapsed(collapsed) {
  panelEl.classList.toggle("collapsed", collapsed);
  panelToggle.setAttribute("aria-expanded", String(!collapsed));
}

panelToggle.addEventListener("click", () => {
  const collapsed = !panelEl.classList.contains("collapsed");
  setPanelCollapsed(collapsed);
  storePanelCollapsed(collapsed);
});

setPanelCollapsed(readStoredPanelCollapsed() ?? isNarrowScreen());
const installDialog = document.getElementById("install-dialog");
const IS_APPLE_MOBILE = /iPad|iPhone|iPod/.test(navigator.userAgent)
  || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
const INSTALL_ICON_SVG = `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" fill="none"
  stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M12 3v11M7.5 9.5 12 14l4.5-4.5M5 15v4a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-4"/></svg>`;
let deferredInstallPrompt = null;

function runsAsInstalledApp() {
  return window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
}

function canOfferInstall() {
  return !runsAsInstalledApp() && (deferredInstallPrompt !== null || IS_APPLE_MOBILE);
}

async function installApp() {
  if (!deferredInstallPrompt) {
    installDialog.showModal();
    return;
  }
  const installPrompt = deferredInstallPrompt;
  deferredInstallPrompt = null;
  await installPrompt.prompt();
  await installPrompt.userChoice;
  updateInstallButton();
}

const InstallControl = L.Control.extend({
  options: { position: "bottomleft" },
  onAdd() {
    const button = L.DomUtil.create("button", "map-button");
    button.type = "button";
    button.title = "Zainstaluj aplikację";
    button.setAttribute("aria-label", "Zainstaluj aplikację");
    button.innerHTML = INSTALL_ICON_SVG;
    L.DomEvent.disableClickPropagation(button);
    button.addEventListener("click", installApp);
    return button;
  },
});
const installControl = new InstallControl().addTo(map);

function trackAttributionHeight() {
  const attribution = map.attributionControl.getContainer();
  const publishHeight = () => {
    document.documentElement.style.setProperty("--attribution-height", `${attribution.offsetHeight}px`);
  };
  new ResizeObserver(publishHeight).observe(attribution);
  publishHeight();
}

trackAttributionHeight();

function updateInstallButton() {
  installControl.getContainer().hidden = !canOfferInstall();
}

window.addEventListener("beforeinstallprompt", (e) => {
  // Suppress Chrome's own install banner; the map button offers install instead.
  e.preventDefault();
  deferredInstallPrompt = e;
  updateInstallButton();
});
window.addEventListener("appinstalled", () => {
  deferredInstallPrompt = null;
  updateInstallButton();
});
updateInstallButton();

registerServiceWorker();
start();
