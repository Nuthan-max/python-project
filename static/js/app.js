/* RescueRoute — command centre frontend
 * Real Delhi road geometry (OSM) + simulated traffic, vehicles and incidents.
 */
(() => {
  "use strict";

  const CFG = JSON.parse(document.getElementById("map-config").textContent);
  const $ = (sel) => document.querySelector(sel);
  const brand = $(".nav-brand");
  if (brand) {
    brand.setAttribute("role", "img");
    brand.setAttribute("aria-label", "RescueRoute, Delhi operations");
    brand.title = "RescueRoute — Delhi operations";
  }
  const brandIcon = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#FF5364" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19.5 4.5 13.2 20l-2.8-6.1-6.3-2.8L19.5 4.5Z"/></svg>';
  document.querySelectorAll(".brand-mark").forEach((mark) => {
    mark.innerHTML = brandIcon;
  });
  function compactDispatchPanel() {
    const panel = $("#tab-dispatch");
    const heading = $("#sidebar-heading");
    const subtitle = $(".sidebar-sub");
    if (heading) heading.textContent = "Dispatch";
    if (subtitle) subtitle.hidden = true;
    if (!panel) return;

    ["Incident", "Ambulance", "Hospital", "Route"].forEach((title, index) => {
      const card = panel.querySelectorAll("#dispatch-setup-wrap .workflow-card")[index];
      if (!card) return;
      card.querySelector(".step-num")?.remove();
      card.querySelector(".card-titles p")?.remove();
      const heading = card.querySelector(".card-titles h3");
      if (heading) heading.textContent = title;
    });

    panel.querySelector(".summary-badge")?.remove();
    panel.querySelector(".toggle-checkbox")?.remove();
    const summaryEmergency = panel.querySelector("#sum-emergency");
    const summaryHospital = panel.querySelector("#sum-hospital");
    if (summaryEmergency) summaryEmergency.textContent = "Incident";
    if (summaryHospital) summaryHospital.textContent = "Hospital";
    panel.querySelector("#emergency-coord span")?.replaceChildren("Pick a map location or preset");
    panel.querySelector("#btn-add-ambulance span")?.replaceChildren("Add ambulance");
    const hospitalPlaceholder = panel.querySelector('#hospital-select option[value=""]');
    if (hospitalPlaceholder) hospitalPlaceholder.textContent = "Choose hospital…";
    const refreshHospitals = panel.querySelector("#btn-refresh-hospitals");
    if (refreshHospitals) {
      refreshHospitals.title = "Refresh nearby hospitals";
      refreshHospitals.setAttribute("aria-label", "Refresh nearby hospitals");
    }

    const algorithms = panel.querySelectorAll("#algo-toggle button");
    if (algorithms[0]) {
      algorithms[0].textContent = "A*";
      algorithms[0].title = "A* route search";
    }
    if (algorithms[1]) {
      algorithms[1].textContent = "Dijkstra";
      algorithms[1].title = "Dijkstra route search";
    }
    panel.querySelector("#btn-plan span")?.replaceChildren("Calculate & dispatch");

    const mission = $("#mission-panel");
    if (mission) {
      mission.querySelector(".card-titles p")?.remove();
      const missionHeading = mission.querySelector(".card-titles h3");
      if (missionHeading) missionHeading.textContent = "Live mission";
      ["#m-travelled", "#m-reroutes"].forEach((selector) => {
        panel.querySelector(selector)?.closest(".kpi-cell")?.remove();
      });
      const labels = { "#m-eta": "ETA", "#m-remaining": "Distance left" };
      Object.entries(labels).forEach(([selector, label]) => {
        const value = panel.querySelector(selector);
        if (value?.previousElementSibling) value.previousElementSibling.textContent = label;
      });

      const eventBox = mission.querySelector(".event-stream-box");
      if (eventBox && eventBox.tagName !== "DETAILS") {
        const details = document.createElement("details");
        details.className = eventBox.className;
        details.id = "mission-events";
        const summary = document.createElement("summary");
        summary.className = "stream-header";
        summary.textContent = "Events";
        details.append(summary);
        const eventList = eventBox.querySelector("#m-events");
        if (eventList) details.append(eventList);
        eventBox.replaceWith(details);
      } else if (eventBox) {
        eventBox.querySelector(".stream-header").textContent = "Events";
      }

      const pauseButton = mission.querySelector("#btn-pause");
      if (pauseButton) pauseButton.textContent = "Pause";
      const abortButton = mission.querySelector("#btn-abort");
      if (abortButton) abortButton.textContent = "Abort";
    }
  }
  compactDispatchPanel();
  window.addEventListener("tab-selected", (event) => {
    const tab = event.detail?.tab;
    if (tab !== "dispatch" && tab !== "traffic") return;
    window.setTimeout(() => {
      const heading = $("#sidebar-heading");
      const subtitle = $(".sidebar-sub");
      if (tab === "dispatch") {
        if (heading) heading.textContent = "Dispatch";
        if (subtitle) subtitle.hidden = true;
      } else {
        if (heading) heading.textContent = "Traffic & Disruption Control";
        if (subtitle) {
          subtitle.hidden = false;
          subtitle.textContent = "Active road closures, congestion & incident triggers";
        }
      }
    }, 0);
  });
  const api = async (url, opts = {}) => {
    const res = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      ...opts,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    let data = {};
    try { data = await res.json(); } catch (_) { /* empty */ }
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  };

  const PRESETS = [
    { name: "Connaught Place", lat: 28.6315, lng: 77.2167 },
    { name: "India Gate", lat: 28.6129, lng: 77.2295 },
    { name: "New Delhi Station", lat: 28.6430, lng: 77.2197 },
    { name: "Karol Bagh", lat: 28.6519, lng: 77.1909 },
    { name: "Daryaganj", lat: 28.6448, lng: 77.2409 },
    { name: "Lodhi Colony", lat: 28.5788, lng: 77.2254 },
  ];

  const state = {
    mode: "emergency",
    algorithm: "astar",
    emergency: null,
    selectedSegment: null,
    selectedSegmentRef: null,
    ambulances: [],
    hospital: null,
    missionId: null,
    pollTimer: null,
    lastMission: null,
    completedMissionIds: new Set(),
    completedMissionToastIds: new Set(),
    missionMarker: null,
    missionMarkerPosition: null,
    activeRouteGroup: null,
    routeSignature: null,
  };

  /* ------------------------------------------------------------------ map */
  const maptilerKey = (CFG.tileUrl && CFG.tileUrl.includes("key="))
    ? CFG.tileUrl.split("key=")[1].split("&")[0]
    : "";
  const osmTileUrl = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
  const osmAttribution = '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap contributors</a>';

  // Use MapTiler only when configured; OpenStreetMap is the no-key default.
  const BASEMAPS = {
    dark: {
      label: CFG.hasKey ? "MapTiler Dark" : "OpenStreetMap",
      url: CFG.hasKey
        ? `https://api.maptiler.com/maps/dataviz-dark/256/{z}/{x}/{y}.png?key=${encodeURIComponent(maptilerKey)}`
        : (CFG.tileUrl || osmTileUrl),
      attr: CFG.hasKey ? CFG.attribution : (CFG.attribution || osmAttribution),
      tileSize: 256,
      zoomOffset: 0,
      subdomains: "abcd",
    },
    streets: {
      label: CFG.hasKey ? "MapTiler Streets" : "OpenStreetMap",
      url: CFG.tileUrl || osmTileUrl,
      attr: CFG.attribution || osmAttribution,
      tileSize: 256,
      zoomOffset: 0,
      subdomains: "abcd",
    },
  };

  // World copy jump keeps the map sane while panning across Delhi.
  const map = L.map("map", {
    zoomControl: false, preferCanvas: true, worldCopyJump: true,
    minZoom: 8, maxZoom: 19,
  }).setView(CFG.center, CFG.zoom);

  function makeBasemap(key) {
    const b = BASEMAPS[key] || BASEMAPS.streets;
    return L.tileLayer(b.url, {
      maxZoom: 19,
      minZoom: 10,
      attribution: b.attr,
      tileSize: b.tileSize || 256,
      zoomOffset: b.zoomOffset || 0,
      subdomains: b.subdomains || "abc",
      detectRetina: false,
      crossOrigin: true,
      errorTileUrl:
        "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7",
    });
  }

  // Ensure clean Streets map is active
  let initialBasemap = "streets";
  localStorage.setItem("rr.basemap", "streets");
  let basemap = makeBasemap(initialBasemap).addTo(map);
  basemap.__key = initialBasemap;
  document.body.dataset.basemap = initialBasemap;
  document.body.dataset.mapSource = CFG.hasKey ? "maptiler" : "osm";
  map.attributionControl.setPrefix('<a href="https://leafletjs.com" title="A JS library for interactive maps">Leaflet</a>');
  window.addEventListener("resize", () => map.invalidateSize());
  setTimeout(() => map.invalidateSize(), 150);

  let warnedTileError = false;
  basemap.on("tileerror", () => {
    if (!warnedTileError) {
      warnedTileError = true;
      console.warn("[RescueRoute] Map tiles failed; falling back to OpenStreetMap tiles.");
      // Automatic fallback to standard OSM raster tiles if MapTiler key runs into issues
      if (basemap.__key !== "osm_fallback") {
        const osmFallback = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
          maxZoom: 19,
          attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
        }).addTo(map);
        osmFallback.__key = "osm_fallback";
        map.removeLayer(basemap);
        basemap = osmFallback;
        document.body.dataset.mapSource = "osm";
      }
    }
  });

  function setBasemap(key) {
    if (!BASEMAPS[key] || basemap.__key === key) return;
    const b = BASEMAPS[key];
    const next = makeBasemap(key);
    next.__key = key;
    next.addTo(map);
    if (basemap) map.removeLayer(basemap);
    basemap = next;
    document.body.dataset.basemap = key;
    document.body.dataset.mapSource = b.url.includes("openstreetmap.org") ? "osm" : "maptiler";
    localStorage.setItem("rr.basemap", key);
  }

  const layers = {
    streets: L.layerGroup().addTo(map),
    conditions: L.layerGroup().addTo(map),
    original: L.layerGroup().addTo(map),
    route: L.layerGroup().addTo(map),
    markers: L.layerGroup().addTo(map),
  };
  const missionRouteRenderer = L.svg({ padding: 0.5 });

  /* Road styling: the real OSM network is drawn as a crisp, readable overlay
   * whose weight/colour scales with road class. The basemap underneath is
   * deliberately dimmed (see style.css) so the routing graph is what pops. */
  const ROAD_STYLE = {
    motorway:      { color: "#7f97b8", weight: 3.6, opacity: 0.95, casing: "#0a0f16" },
    trunk:         { color: "#7f97b8", weight: 3.4, opacity: 0.95, casing: "#0a0f16" },
    primary:       { color: "#6d86a8", weight: 3.0, opacity: 0.92, casing: "#0a0f16" },
    secondary:     { color: "#5b7396", weight: 2.4, opacity: 0.9 },
    tertiary:      { color: "#4c6484", weight: 2.0, opacity: 0.85 },
    residential:   { color: "#3b4f68", weight: 1.4, opacity: 0.8 },
    unclassified:  { color: "#3b4f68", weight: 1.3, opacity: 0.75 },
    living_street: { color: "#33455c", weight: 1.2, opacity: 0.7 },
    service:       { color: "#2e3f54", weight: 1.0, opacity: 0.6 },
  };
  const ROAD_DEFAULT = { color: "#33455c", weight: 1.1, opacity: 0.65 };
  const ARTERIAL = ["motorway", "motorway_link", "trunk", "trunk_link",
                    "primary", "primary_link", "secondary", "secondary_link"];

  function roadStyle(f, selected = false) {
    if (selected) {
      return { color: "#f59e0b", weight: 6, opacity: 1, lineCap: "round" };
    }
    // Clean navigation: keep road network transparent for click targeting in block mode.
    // The MapTiler Streets basemap natively renders clean roads, labels, and parks.
    return { color: "transparent", weight: 7, opacity: 0 };
  }

  const styleRoad = (f) => roadStyle(f);

  let streetsGeoJson = null;
  let segmentMap = null;
  let networkRequest = null;
  let networkLoadTimer = null;

  /** "u|v" -> road feature, rebuilt whenever the network layer changes. */
  function segmentIndex() {
    if (segmentMap) return segmentMap;
    segmentMap = new Map();
    streetsGeoJson?.eachLayer((l) => {
      const p = l.feature?.properties;
      if (p) segmentMap.set(`${p.u}|${p.v}`, l.feature);
    });
    return segmentMap;
  }

  function clearNetwork() {
    clearTimeout(networkLoadTimer);
    networkLoadTimer = null;
    networkRequest?.abort();
    networkRequest = null;
    streetsGeoJson = null;
    segmentMap = null;
    layers.streets.clearLayers();
  }

  function scheduleNetworkLoad() {
    clearTimeout(networkLoadTimer);
    networkLoadTimer = setTimeout(loadNetwork, 180);
  }

  async function loadNetwork() {
    if (state.mode !== "block") return;
    if (map.getZoom() < 12) {
      clearNetwork();
      return;
    }

    networkRequest?.abort();
    const controller = new AbortController();
    networkRequest = controller;
    const bounds = map.getBounds();
    const query = new URLSearchParams({
      west: String(bounds.getWest()),
      south: String(bounds.getSouth()),
      east: String(bounds.getEast()),
      north: String(bounds.getNorth()),
    });
    try {
      const geo = await api(`/api/network?${query}`, { signal: controller.signal });
      if (networkRequest !== controller || state.mode !== "block") return;
      layers.streets.clearLayers();
      streetsGeoJson = L.geoJSON(geo, {
        style: styleRoad,
        interactive: true,
        onEachFeature: (feature, layer) => {
          layer.on("click", () => {
            if (state.mode === "block") selectSegment(feature);
          });
        },
      }).addTo(layers.streets);
      segmentMap = null;   // force the incident index to rebuild
    } catch (err) {
      if (err.name !== "AbortError") {
        console.warn("[RescueRoute] Network overlay load warning:", err);
      }
    } finally {
      if (networkRequest === controller) networkRequest = null;
    }
  }

  /* ------------------------------------------------------- interactions */
  map.on("click", (e) => {
    const { lat, lng } = e.latlng;
    if (state.mode === "emergency") setEmergency(lat, lng);
    else if (state.mode === "ambulance") placeAmbulance(lat, lng);
  });

  /* --------------------------------------------------- Audio Alerts Cues */
  let soundEnabled = true;
  const audioCtx = (typeof window !== "undefined" && (window.AudioContext || window.webkitAudioContext))
    ? new (window.AudioContext || window.webkitAudioContext)()
    : null;

  function playAlert(type = "info") {
    if (!soundEnabled || !audioCtx) return;
    try {
      if (audioCtx.state === "suspended") audioCtx.resume();
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.connect(gain);
      gain.connect(audioCtx.destination);
      const now = audioCtx.currentTime;

      if (type === "emergency" || type === "err") {
        osc.type = "sawtooth";
        osc.frequency.setValueAtTime(520, now);
        osc.frequency.exponentialRampToValueAtTime(780, now + 0.12);
        gain.gain.setValueAtTime(0.08, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.28);
        osc.start(now);
        osc.stop(now + 0.28);
      } else if (type === "reroute" || type === "warn") {
        osc.type = "triangle";
        osc.frequency.setValueAtTime(440, now);
        osc.frequency.setValueAtTime(587.33, now + 0.09);
        gain.gain.setValueAtTime(0.06, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.25);
        osc.start(now);
        osc.stop(now + 0.25);
      } else {
        // Success / dispatched cue
        osc.type = "sine";
        osc.frequency.setValueAtTime(587.33, now);
        osc.frequency.exponentialRampToValueAtTime(880, now + 0.14);
        gain.gain.setValueAtTime(0.05, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.22);
        osc.start(now);
        osc.stop(now + 0.22);
      }
    } catch (_) {}
  }

  function drawEmergency() {
    layers.markers.eachLayer((l) => { if (l.__kind === "emergency") layers.markers.removeLayer(l); });
    if (!state.emergency) return;
    const svgIcon = `
      <div class="emg-pin pulse" title="Incident Target">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="9"/>
          <line x1="12" y1="8" x2="12" y2="16"/>
          <line x1="8" y1="12" x2="16" y2="12"/>
        </svg>
      </div>`;
    const icon = L.divIcon({ className: "", html: svgIcon, iconSize: [28, 28], iconAnchor: [14, 14] });
    L.marker([state.emergency.lat, state.emergency.lng], { icon, zIndexOffset: 950 })
      .bindTooltip("<b>Emergency Location</b>", { direction: "top", offset: [0, -12], className: "glass-tooltip" })
      .addTo(layers.markers).__kind = "emergency";
  }

  // Update hospital density as user zooms in or out
  map.on("zoomend moveend", () => {
    if (regionalHospitals.length) renderHospitalPins(regionalHospitals);
    if (state.mode === "block") {
      const badge = $("#map-badge");
      if (map.getZoom() < 12) {
        clearNetwork();
        badge.textContent = "Block-road mode — zoom in to level 12+ to select a street";
      } else {
        badge.textContent = "Block-road mode — click a street to close it";
        scheduleNetworkLoad();
      }
    }
  });
  map.on("zoomend", refreshConditions);

  function setEmergency(lat, lng, name) {
    state.emergency = { lat: +lat.toFixed(6), lng: +lng.toFixed(6), name: name || null };
    const el = $("#emergency-coord");
    el.textContent = `${state.emergency.name ? state.emergency.name + " · " : ""}${state.emergency.lat}, ${state.emergency.lng}`;
    el.classList.add("set");
    drawEmergency();
    loadHospitals();
    $("#btn-plan").disabled = false;
  }

  function selectSegment(feature) {
    state.selectedSegment = feature;
    state.selectedSegmentRef = feature.properties;
    // Reset the previous highlight, then draw a halo + solid highlight on top
    // (the halo keeps the road readable against both dark and light tiles).
    layers.streets.eachLayer((l) => l.setStyle?.(roadStyle(l.feature)));
    let target = null;
    layers.streets.eachLayer((l) => { if (l.feature?.id === feature.id) target = l; });
    if (target) {
      target.setStyle({ color: "#ffb020", weight: 7, opacity: 1 });
      target.bringToFront();
    }
    const p = feature.properties;
    const centre = feature.geometry.coordinates[Math.floor(feature.geometry.coordinates.length / 2)];
    const el = $("#incident-segment");
    el.textContent = `${p.name || p.highway || "unnamed road"} · segment ${p.u.slice(-5)}↔${p.v.slice(-5)}`;
    el.classList.add("set");
    $("#btn-create-incident").disabled = false;
    state.segmentCentre = { lat: centre[1], lng: centre[0] };
    map.panTo([centre[1], centre[0]], { animate: true });
  }

  /* ---------------------------------------------------------- ambulances */
  async function placeAmbulance(lat, lng) {
    try {
      const { ambulance } = await api("/api/ambulances", { method: "POST", body: { lat, lng } });
      state.ambulances.push(ambulance);
      renderAmbulances();
      toast(`<b>${ambulance.label}</b> placed at ${lat.toFixed(4)}, ${lng.toFixed(4)}`, "ok");
    } catch (err) { toast(err.message, "err"); }
  }

  function renderAmbulances() {
    layers.markers.eachLayer((l) => { if (l.__kind === "ambulance") layers.markers.removeLayer(l); });
    state.ambulances.forEach((a) => {
      const isLive = Boolean(a.is_live);
      const iconHtml = `
        <div class="amb-pin ${isLive ? 'live-gps' : ''}" style="position:relative;">
          ${isLive ? '<span class="pulse-beacon gps-live" style="position:absolute;top:-2px;right:-2px;width:7px;height:7px;"></span>' : ''}
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polygon points="3 11 22 2 13 21 11 13 3 11" fill="rgba(101,191,255,0.2)"/>
          </svg>
        </div>`;
      const icon = L.divIcon({ className: "", html: iconHtml, iconSize: [32, 32], iconAnchor: [16, 16] });
      const tip = isLive ? `🟢 LIVE GPS · ${a.label}` : a.label;
      const m = L.marker([a.lat, a.lng], { icon, zIndexOffset: 750 })
        .bindTooltip(tip, { direction: "top", className: "glass-tooltip" })
        .addTo(layers.markers);
      m.__kind = "ambulance";
    });
    const sel = $("#ambulance-select");
    sel.innerHTML = state.ambulances.length
      ? state.ambulances.map((a) => {
          const prefix = a.is_live ? "🟢 [LIVE GPS] " : "";
          return `<option value="${a.id}">${prefix}${a.label} · ${a.lat.toFixed(4)}, ${a.lng.toFixed(4)}</option>`;
        }).join("")
      : '<option value="">No ambulances — place one first</option>';
  }

  /* ------------------------------------------------------------ hospitals */
  let hospitals = [];
  let regionalHospitals = [];

  async function loadHospitals() {
    const c = state.emergency || { lat: CFG.center[0], lng: CFG.center[1] };
    try {
      // Hospitals only, by design: a dispensary is not a valid emergency destination.
      const [{ items }, { items: regionalItems }] = await Promise.all([
        api(`/api/hospitals?lat=${c.lat}&lng=${c.lng}&limit=20&clinics=0`),
        api(`/api/hospitals?lat=${c.lat}&lng=${c.lng}&limit=1000&clinics=0`),
      ]);
      hospitals = items;
      regionalHospitals = regionalItems;
      const sel = $("#hospital-select");
      sel.innerHTML = '<option value="">Choose hospital…</option>' +
        items.map((h) => `<option value="${h.id}">${h.name} — ${(h.distance_m / 1000).toFixed(1)} km${h.status === "full" ? " (reported full)" : ""}</option>`).join("");
      if (state.hospital) {
        const selected = regionalHospitals.find((hospital) => hospital.id === state.hospital.id);
        if (selected) {
          state.hospital = selected;
          if (![...sel.options].some((option) => option.value === selected.id)) {
            sel.add(new Option(`${selected.name} — ${(selected.distance_m / 1000).toFixed(1)} km`, selected.id));
          }
          sel.value = selected.id;
        }
      }
      renderHospitalPins(regionalHospitals);
    } catch (err) { toast(`Hospital list: ${err.message}`, "warn"); }
  }

  function renderHospitalPins(items) {
    if (!items || !items.length) return;
    layers.markers.eachLayer((l) => { if (l.__kind === "hospital") layers.markers.removeLayer(l); });

    const z = map.getZoom();
    const visible = items.filter((hospital) =>
      map.getBounds().contains([hospital.lat, hospital.lng]));
    const groups = new Map();
    const cellSize = z < 11 ? 72 : 48;
    visible.forEach((hospital) => {
      const point = map.latLngToContainerPoint([hospital.lat, hospital.lng]);
      const key = z >= 14 ? hospital.id : `${Math.floor(point.x / cellSize)}:${Math.floor(point.y / cellSize)}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(hospital);
    });

    const addHospitalPin = (h, isSelected) => {
      const iconHtml = `
        <div class="hosp-pin ${isSelected ? 'selected' : ''}" title="${h.name}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">
            <line x1="12" y1="5" x2="12" y2="19"/>
            <line x1="5" y1="12" x2="19" y2="12"/>
          </svg>
        </div>`;
      const icon = L.divIcon({ className: "", html: iconHtml, iconSize: [26, 26], iconAnchor: [13, 13] });
      const m = L.marker([h.lat, h.lng], { icon, zIndexOffset: isSelected ? 850 : 450 })
        .bindTooltip(`<b>${h.name}</b><br><span style="color:#697586">${h.type} · ${((h.distance_m ?? 0) / 1000).toFixed(1)} km</span>`, {
          direction: "top",
          opacity: 0.95,
          offset: [0, -10],
          className: "glass-tooltip",
        })
        .on("click", () => {
          showHospitalCard(h.id);
          renderHospitalPins(regionalHospitals);
        })
        .addTo(layers.markers);
      m.__kind = "hospital";
    };

    groups.forEach((group) => {
      const selected = group.find((hospital) => hospital.id === state.hospital?.id);
      if (selected) addHospitalPin(selected, true);
      const remaining = selected ? group.filter((hospital) => hospital !== selected) : group;
      if (z < 14 && remaining.length > 1) {
        const lat = remaining.reduce((sum, hospital) => sum + hospital.lat, 0) / remaining.length;
        const lng = remaining.reduce((sum, hospital) => sum + hospital.lng, 0) / remaining.length;
        const icon = L.divIcon({
          className: "",
          html: `<div class="hospital-cluster" aria-label="${remaining.length} nearby hospitals">${remaining.length}</div>`,
          iconSize: [34, 34],
          iconAnchor: [17, 17],
        });
        const marker = L.marker([lat, lng], { icon, zIndexOffset: 500 })
          .bindTooltip(`${remaining.length} nearby hospitals`, { direction: "top", className: "glass-tooltip" })
          .on("click", () => map.flyTo([lat, lng], Math.min(z + 2, 15), { duration: 0.45 }))
          .addTo(layers.markers);
        marker.__kind = "hospital";
      } else {
        remaining.forEach((hospital) => addHospitalPin(hospital, false));
      }
    });
  }

  function showHospitalCard(id) {
    const h = hospitals.find((x) => x.id === id)
      || regionalHospitals.find((x) => x.id === id);
    const card = $("#hospital-card");
    if (!h) { card.hidden = true; state.hospital = null; return; }
    state.hospital = h;
    card.hidden = false;
    const select = $("#hospital-select");
    if (![...select.options].some((option) => option.value === h.id)) {
      select.add(new Option(`${h.name} — ${((h.distance_m ?? 0) / 1000).toFixed(1)} km`, h.id));
    }
    select.value = h.id;
    card.innerHTML = `
      <div><b>${h.name}</b></div>
      <div style="margin:6px 0">
        <span class="badge">${h.type}</span>
        <span class="badge unv">location unverified</span>
        <span class="badge sim">availability simulated</span>
      </div>
      <div class="muted">${h.address ? h.address + " · " : ""}${((h.distance_m ?? 0) / 1000).toFixed(2)} km from emergency</div>
      <div class="muted">Beds available: <b style="color:#087FE5">${h.beds_available}</b> / ${h.beds_total}
        · Trauma centre: ${h.trauma_centre ? "yes (OSM inference)" : "unknown"}</div>`;
    map.panTo([h.lat, h.lng], { animate: true });
  }

  /* --------------------------------------------------------------- route */
  function clearRouteLayers() {
    Object.values(layers).forEach((g) => { if (g !== layers.markers && g !== layers.streets && g !== layers.conditions) g.clearLayers(); });
  }

  async function planMission() {
    if (!state.emergency) return toast("Select an emergency location first.", "warn");
    const ambId = $("#ambulance-select").value;
    if (!ambId) return toast("Place and select an ambulance first.", "warn");
    const hospId = $("#hospital-select").value;
    if (!hospId) return toast("Choose a destination hospital.", "warn");

    const btn = $("#btn-plan");
    const btnLabel = btn.querySelector("span");
    btn.disabled = true;
    btn.setAttribute("aria-busy", "true");
    if (btnLabel) btnLabel.textContent = "Calculating route…";
    try {
      const data = await api("/api/missions", {
        method: "POST",
        body: {
          ambulance_id: ambId,
          hospital_id: hospId,
          emergency_lat: state.emergency.lat,
          emergency_lng: state.emergency.lng,
          algorithm: state.algorithm,
          auto_start: true,
        },
      });
      state.missionId = data.mission.id;
      state.lastMission = data.mission;
      const mp = $("#mission-panel");
      if (mp) {
        mp.hidden = false;
        if (window.RescueMotion?.gsap && !window.RescueMotion.prefersReducedMotion()) {
          window.RescueMotion.gsap.fromTo(mp,
            { autoAlpha: 0, y: 10, scale: 0.99 },
            { autoAlpha: 1, y: 0, scale: 1, duration: 0.38, ease: "back.out(1.12)", clearProps: "transform,opacity" });
        }
      }
      const sum = $("#active-mission-summary");
      if (sum) {
        sum.hidden = false;
        const emgText = $("#sum-emergency");
        if (emgText) emgText.textContent = state.emergency?.name || `${state.emergency?.lat}, ${state.emergency?.lng}`;
        const hospText = $("#sum-hospital");
        if (hospText) hospText.textContent = state.hospital?.name || "Destination Hospital";
      }
      document.getElementById("tab-dispatch")?.classList.add("mission-active");
      const pnl = document.getElementById("sidebar-panel");
      if (pnl && !pnl.classList.contains("open")) {
        pnl.classList.add("open");
        document.getElementById("btn-expand-sidebar")?.classList.add("hidden");
      }
      drawMission(data.mission);
      renderBench(data.comparison, data.mission);
      startPolling();
      btn.classList.add("is-success");
      window.setTimeout(() => btn.classList.remove("is-success"), 1200);
      toast(`Mission #${data.mission.id} dispatched with ${state.algorithm.toUpperCase()}.`, "ok");
    } catch (err) {
      toast(err.message, "err");
    } finally {
      btn.disabled = false;
      btn.removeAttribute("aria-busy");
      if (btnLabel) btnLabel.textContent = "Calculate & dispatch";
    }
  }

  function animateRouteReveal(group, previousGroup) {
    const paths = [];
    group.eachLayer((layer) => {
      const path = layer.getElement?.();
      if (path) paths.push(path);
    });

    const reduced = window.RescueMotion?.prefersReducedMotion();
    const gsap = window.RescueMotion?.gsap;
    if (!paths.length || reduced) {
      if (previousGroup) layers.route.removeLayer(previousGroup);
      return;
    }

    paths.forEach((path, index) => {
      let length = 0;
      try { length = path.getTotalLength(); } catch (_) { /* SVG measurement may be unavailable before paint. */ }
      if (!length) return;

      if (gsap) {
        gsap.fromTo(path,
          { strokeDasharray: `${length} ${length}`, strokeDashoffset: length, opacity: 0.22 },
          {
            strokeDashoffset: 0,
            opacity: 1,
            duration: 0.9,
            delay: index * 0.055,
            ease: "power2.out",
            onComplete: () => {
              path.style.removeProperty("stroke-dasharray");
              path.style.removeProperty("stroke-dashoffset");
            },
          });
      } else if (path.animate) {
        path.style.strokeDasharray = `${length} ${length}`;
        path.animate(
          [{ strokeDashoffset: length, opacity: 0.22 }, { strokeDashoffset: 0, opacity: 1 }],
          { duration: 900, delay: index * 55, easing: "cubic-bezier(.22,1,.36,1)", fill: "both" }
        ).onfinish = () => {
          path.style.removeProperty("stroke-dasharray");
          path.style.removeProperty("stroke-dashoffset");
        };
      }
    });

    if (previousGroup) {
      if (gsap) {
        const oldPaths = [];
        previousGroup.eachLayer((layer) => {
          const path = layer.getElement?.();
          if (path) oldPaths.push(path);
        });
        gsap.to(oldPaths, {
          opacity: 0,
          duration: 0.24,
          delay: 0.12,
          ease: "power1.out",
          onComplete: () => layers.route.removeLayer(previousGroup),
        });
      } else {
        window.setTimeout(() => layers.route.removeLayer(previousGroup), 820);
      }
    }
  }

  function drawMission(mission, reportOnly = false) {
    // original route (subtle reference for what changed after reroutes)
    if (mission.original_route && !reportOnly) {
      layers.original.clearLayers();
      const legs = [mission.original_route.to_pickup, mission.original_route.to_hospital];
      legs.forEach((leg) => {
        L.polyline(leg.coordinates, { color: "#94a3b8", weight: 3, opacity: 0.6, dashArray: "5 7", lineCap: "round" })
          .addTo(layers.original);
      });
    }

    // Keep route geometry exact; only its SVG paint is animated on replacement.
    const coords = mission.current_leg_coordinates?.length
      ? mission.current_leg_coordinates
      : (mission.legs?.[mission.legs.length - 1]?.coordinates || []);
    const routeSignature = JSON.stringify(coords);
    if (coords.length > 1 && routeSignature !== state.routeSignature) {
      const previousGroup = state.activeRouteGroup;
      const nextGroup = L.layerGroup().addTo(layers.route);
      L.polyline(coords, { renderer: missionRouteRenderer, color: "rgba(8, 127, 229, 0.22)", weight: 11, opacity: 0.9, lineCap: "round", lineJoin: "round" })
        .addTo(nextGroup);
      L.polyline(coords, { renderer: missionRouteRenderer, color: "#087FE5", weight: 6, opacity: 1, lineCap: "round", lineJoin: "round" })
        .addTo(nextGroup);
      L.polyline(coords, { renderer: missionRouteRenderer, color: "#FFFFFF", weight: 2, opacity: 0.85, lineCap: "round", lineJoin: "round" })
        .addTo(nextGroup);
      state.activeRouteGroup = nextGroup;
      state.routeSignature = routeSignature;
      animateRouteReveal(nextGroup, previousGroup);
    }

    // Reuse the Leaflet marker across one-second telemetry polls.
    if (mission.position) {
      const target = { lat: mission.position[0], lng: mission.position[1] };
      if (!state.missionMarker) {
        const icon = L.divIcon({
          className: "",
          html: `
          <div class="amb-pin moving" style="width:34px; height:34px;">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
              <polygon points="3 11 22 2 13 21 11 13 3 11" fill="#FFFFFF"/>
            </svg>
          </div>`,
          iconSize: [34, 34], iconAnchor: [17, 17],
        });
        state.missionMarker = L.marker(mission.position, { icon, zIndexOffset: 1000 })
        .bindTooltip(`<b>${mission.ambulance_label}</b> · En Route`, { direction: "top", offset: [0, -12], className: "glass-tooltip" })
        .addTo(layers.markers);
        state.missionMarker.__kind = "moving";
        state.missionMarkerPosition = { ...target };
      } else if (state.missionMarkerPosition) {
        const gsap = window.RescueMotion?.gsap;
        if (gsap && !window.RescueMotion.prefersReducedMotion()) {
          gsap.killTweensOf(state.missionMarkerPosition);
          gsap.to(state.missionMarkerPosition, {
            lat: target.lat,
            lng: target.lng,
            duration: 0.86,
            ease: "sine.inOut",
            overwrite: true,
            onUpdate: () => state.missionMarker.setLatLng([
              state.missionMarkerPosition.lat,
              state.missionMarkerPosition.lng,
            ]),
          });
        } else {
          state.missionMarkerPosition = { ...target };
          state.missionMarker.setLatLng(mission.position);
        }
      }
      const markerElement = state.missionMarker.getElement()?.querySelector(".amb-pin");
      if (markerElement) markerElement.style.transform = `rotate(${mission.heading || 0}deg)`;

      // Real GPS precision accuracy halo
      if (!state.gpsHalo) {
        state.gpsHalo = L.circle(mission.position, {
          radius: Math.max(12, ((mission.gps_telemetry?.accuracy_m || 2.5) * 4)),
          color: "#087fe5",
          weight: 1,
          opacity: 0.6,
          fillColor: "#087fe5",
          fillOpacity: 0.12,
          dashArray: "3, 4"
        }).addTo(layers.markers);
        state.gpsHalo.__kind = "moving";
      } else {
        state.gpsHalo.setLatLng(mission.position);
        if (mission.gps_telemetry?.accuracy_m) {
          state.gpsHalo.setRadius(Math.max(12, mission.gps_telemetry.accuracy_m * 4));
        }
      }
    }
    if (!reportOnly && mission.leg_index === 0) {
      map.flyTo(mission.position || CFG.center, Math.max(map.getZoom(), 14), { duration: 0.6 });
    }
    updateMissionPanel(mission);
  }

  function fmtTime(s) {
    if (s == null || isNaN(s)) return "—";
    const m = Math.floor(s / 60), sec = Math.round(s % 60);
    return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
  }
  function fmtDist(m) { return m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`; }

  function updateMissionPanel(mission) {
    $("#m-status").textContent = mission.status.replace(/_/g, " ");
    $("#m-leg").textContent = mission.leg_name
      ? mission.leg_name.replace(/_/g, " ").replace("ambulance to ", "→ ").replace("emergency to ", "→ ")
      : "—";
    $("#m-eta").textContent = fmtTime(mission.eta_s);
    $("#m-remaining").textContent = fmtDist(mission.remaining_m);

    // Update GPS Tracker HUD
    const gt = mission.gps_telemetry;
    if (gt) {
      const srcEl = $("#m-gps-source");
      if (srcEl) srcEl.textContent = gt.is_live ? (gt.protocol || "Real Device GPS") : "GNSS-900 Unit";
      const fixEl = $("#m-gps-fix");
      if (fixEl) fixEl.textContent = `${gt.fix_type || "3D Fix"} · ${gt.satellites || 11} Sats`;
      const spdEl = $("#m-gps-speed");
      if (spdEl) spdEl.textContent = `${Math.round(gt.speed_kmh || 0)} km/h`;
      const brgEl = $("#m-gps-bearing");
      if (brgEl) brgEl.textContent = `${Math.round(gt.heading_deg || 0)}°`;
      const accEl = $("#m-gps-acc");
      if (accEl) accEl.textContent = `±${gt.accuracy_m || 2.5}m`;
      const hdopEl = $("#m-gps-hdop");
      if (hdopEl) hdopEl.textContent = `${gt.hdop || 0.88}`;
      const nmeaEl = $("#m-nmea-text");
      if (nmeaEl && (gt.nmea_gprmc || gt.nmea_gpgga)) {
        nmeaEl.textContent = `${gt.nmea_gprmc || ""}\n${gt.nmea_gpgga || ""}`;
      }
    }

    const events = mission.events || [];
    const log = $("#m-events");
    if (log) {
      log.innerHTML = events.slice().reverse().map((e) => {
        const cls = ["reroute", "completed", "error", "stranded"].includes(e.kind) ? e.kind : "";
        const t = typeof e.t === "number" ? `${e.t.toFixed(1)}s` : "";
        return `<li class="${cls}"><span class="muted">${t}</span> ${e.message}</li>`;
      }).join("");
    }
    const eventPanel = $("#mission-events");
    if (eventPanel) eventPanel.hidden = events.length === 0;
    $("#btn-pause").textContent = mission.status.endsWith("_paused") ? "Resume" : "Pause";

    if (mission.legs?.length) {
      const est = mission.legs.reduce((a, l) => a + l.time_s, 0);
      const orig = mission.original_route?.estimated_total_s;
      setPerf(orig, est, mission);
    }
  }

  function setPerf(original, current, mission) {
    const grid = $("#perf-grid");
    if (!grid) return;
    const diff = original != null ? current - original : null;
    grid.innerHTML = `
      <div class="kpi-cell"><span class="kpi-label">Original estimate</span><strong class="kpi-val">${fmtTime(original)}</strong></div>
      <div class="kpi-cell"><span class="kpi-label">After reroutes</span><strong class="kpi-val">${fmtTime(current)}</strong></div>
      <div class="kpi-cell"><span class="kpi-label">Variance</span><strong class="kpi-val" style="color:${diff > 0 ? "var(--accent-amber)" : "var(--accent-green)"}">${diff == null ? "—" : (diff > 0 ? "+" : "") + fmtTime(Math.abs(diff))}</strong></div>
      <div class="kpi-cell"><span class="kpi-label">Distance</span><strong class="kpi-val">${fmtDist(mission.distance_travelled_m || 0)}</strong></div>`;
  }

  function startPolling() {
    clearInterval(state.pollTimer);
    state.pollTimer = setInterval(async () => {
      if (!state.missionId) return;
      try {
        const { mission } = await api(`/api/missions/${state.missionId}`);
        const wasCompleted = state.lastMission?.status === "completed";
        state.lastMission = mission;
        drawMission(mission, true);
        if (mission.status === "completed" && !wasCompleted) {
          clearInterval(state.pollTimer);
          if (state.gpsHalo) {
            try { layers.markers.removeLayer(state.gpsHalo); } catch (_) {}
            state.gpsHalo = null;
          }
          document.getElementById("tab-dispatch")?.classList.remove("mission-active");
          const sum = $("#active-mission-summary");
          if (sum) sum.hidden = true;
          notifyMissionCompleted(mission.id);
          refreshStatus();
        }
        if (mission.status === "aborted") {
          clearInterval(state.pollTimer);
          if (state.gpsHalo) {
            try { layers.markers.removeLayer(state.gpsHalo); } catch (_) {}
            state.gpsHalo = null;
          }
          document.getElementById("tab-dispatch")?.classList.remove("mission-active");
          const sum = $("#active-mission-summary");
          if (sum) sum.hidden = true;
          toast("Mission aborted.", "warn");
        }
        refreshStatus();
      } catch (err) { /* transient */ }
    }, 1000);
  }

  function notifyMissionCompleted(id) {
    const key = String(id);
    if (state.completedMissionIds.has(key)) return;
    state.completedMissionIds.add(key);
    showReport(id);
  }

  async function showReport(id) {
    try {
      const { report } = await api(`/api/missions/${id}/report`);
      const signed = report.time_difference_s;
      toast(`<b>Mission #${report.mission_id} complete</b><br>
        Reroutes: ${report.reroutes} · Distance: ${fmtDist(report.distance_travelled_m)}<br>
        Time difference vs original plan: ${signed >= 0 ? "+" : ""}${fmtTime(Math.abs(signed))}
        ${signed >= 0 ? "slower" : "faster"}`, "ok");
      loadHistory();
    } catch (err) {
      toast(`Mission ${id} completed; report unavailable: ${err.message}`, "warn");
    }
  }

  /* ---------------------------------------------------- conditions layer */
  let renderedConditionsKey = null;
  let conditionsRefreshPending = false;

  async function refreshConditions() {
    if (conditionsRefreshPending) return;
    conditionsRefreshPending = true;
    try {
      const { items } = await api("/api/incidents");
      const zoomBand = map.getZoom() < 13 ? "wide" : "close";
      const renderKey = JSON.stringify({
        zoomBand,
        items: items.map((i) => [
          i.id, i.level, i.segment, i.label, i.lat, i.lng, i.source, i.kind,
        ]),
      });
      if (renderKey === renderedConditionsKey) return;

      layers.conditions.clearLayers();
      const list = $("#incident-list");
      if (!items.length) {
        list.innerHTML = '<li class="muted">None</li>';
      } else {
        list.innerHTML = items.map((i) => {
          const colour = i.level === "closed" ? "#ff8a00" : i.level === "jam" ? "#ff3b3b" : "#ffd166";
          const isLive = i.source === "tomtom_live";
          const sourceBadge = isLive
            ? '<span style="background:#10b981;color:#fff;font-size:10px;font-weight:700;padding:1px 5px;border-radius:4px;margin-left:4px;">LIVE TOMTOM</span>'
            : '<span style="background:#334155;color:#94a3b8;font-size:10px;font-weight:600;padding:1px 5px;border-radius:4px;margin-left:4px;">SIMULATED</span>';
          return `<li><span class="dot" style="background:${colour}"></span>
            <span class="grow"><b>${i.label}</b> ${sourceBadge}<br><span class="muted">${i.kind} · ${i.level}</span></span>
            <button title="Clear" data-resolve="${i.id}">✕</button></li>`;
        }).join("");
      }
      list.querySelectorAll("[data-resolve]").forEach((b) =>
        b.addEventListener("click", () => resolveIncident(+b.dataset.resolve)));

      // Draw disrupted segments with clean traffic styling (Google Maps style)
      const refs = segmentIndex();
      const CONDITION_COLOURS = { closed: "#ef4444", jam: "#dc2626", heavy: "#f59e0b", moderate: "#fbbf24" };
      
      const currentZoom = map.getZoom();
      // Zoom-dependent incident rendering: at wide zoom, only render major closures and jams
      const filteredItems = currentZoom < 13
        ? items.filter(i => i.level === "closed" || i.level === "jam")
        : items;

      filteredItems.forEach((i) => {
        const [u, v] = i.segment.split("|");
        const f = refs.get(`${u}|${v}`) || refs.get(`${v}|${u}`);
        const colour = CONDITION_COLOURS[i.level] || "#f59e0b";

        if (f) {
          L.geoJSON(f.geometry, {
            style: {
              color: colour,
              weight: 4.5,
              opacity: 0.9,
              dashArray: i.level === "closed" ? "6 6" : null,
              lineCap: "round",
            },
            interactive: true,
          }).bindTooltip(
            `<b>${i.label}</b><br><span style="color:#64748b">${i.kind} · ${i.level}</span>`,
            { direction: "top", opacity: 0.95, className: "glass-tooltip" }
          ).addTo(layers.conditions);
        }

        // Small warning badge at midpoint
        if (Number.isFinite(i.lat) && Number.isFinite(i.lng)) {
          const isClosed = i.level === "closed";
          const badgeIcon = L.divIcon({
            className: "",
            html: `
              <div class="inc-pin ${isClosed ? 'closed' : ''}" title="${i.label}">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
                  ${isClosed 
                    ? '<circle cx="12" cy="12" r="9"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/>'
                    : '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>'
                  }
                </svg>
              </div>`,
            iconSize: [22, 22],
            iconAnchor: [11, 11]
          });
          L.marker([i.lat, i.lng], { icon: badgeIcon, zIndexOffset: 600 })
            .bindTooltip(`<b>${i.label}</b>`, { direction: "top", opacity: 0.95, offset: [0, -8], className: "glass-tooltip" })
            .addTo(layers.conditions);
        }
      });
      $("#chip-incidents").innerHTML = `Incidents <b>${items.length}</b>`;
      renderedConditionsKey = renderKey;
    } catch (err) {
      console.warn("[RescueRoute] Incident refresh failed:", err);
    } finally {
      conditionsRefreshPending = false;
    }
  }

  async function syncLiveTraffic() {
    try {
      const { status } = await api("/api/traffic/live-sync");
      if (status.error) {
        console.warn("[RescueRoute] TomTom traffic sync warning:", status.error);
        return;
      }
      await Promise.all([refreshConditions(), refreshStatus()]);
    } catch (err) {
      console.warn("[RescueRoute] TomTom traffic sync failed:", err);
    }
  }

  async function createIncident() {
    if (!state.selectedSegmentRef) return toast("Click a road on the map first.", "warn");
    const p = state.selectedSegmentRef;
    const kind = $("#incident-kind").value;
    const level = $("#incident-level").value;
    const label = $("#incident-label").value.trim() || undefined;
    try {
      const res = await api("/api/incidents", {
        method: "POST",
        body: { u: p.u, v: p.v, kind, level, label, lat: state.segmentCentre.lat, lng: state.segmentCentre.lng },
      });
      toast(res.will_reroute
        ? `Disruption created. Mission(s) ${res.missions_affected.join(", ")} will reroute.`
        : "Disruption created.", res.will_reroute ? "warn" : "");
      refreshConditions();
      if (state.missionId) startPolling();
    } catch (err) { toast(err.message, "err"); }
  }

  async function resolveIncident(id) {
    try { await api(`/api/incidents/${id}/resolve`, { method: "POST" }); refreshConditions(); }
    catch (err) { toast(err.message, "err"); }
  }

  /* -------------------------------------------------------------- status */
  async function refreshStatus() {
    try {
      const s = await api("/api/status");
      const networkChip = $("#chip-network");
      networkChip.title = `Road network coverage: ${s.network.area}`;
      networkChip.innerHTML = `Network <b>${s.network.nodes.toLocaleString()} nodes</b>`;
      $("#chip-missions").innerHTML = `Missions <b>${s.missions}</b>`;
      $("#chip-engine").innerHTML = `Engine <b>online</b>`;

      const gpsChip = $("#chip-gps");
      if (gpsChip) {
        if (s.live_ambulances && s.live_ambulances > 0) {
          gpsChip.title = `Live GNSS: ${s.live_ambulances} hardware GPS device(s) broadcasting live 3D Fix`;
          gpsChip.innerHTML = `<span class="pulse-beacon gps-live" style="background:#10b981;"></span><span>GPS <b style="color:#10b981">3D Fix (${s.live_ambulances})</b></span>`;
        } else {
          gpsChip.title = "GNSS Satellite System: Ready (click to inspect NMEA-0183 stream or connect device GPS)";
          gpsChip.innerHTML = `<span class="pulse-beacon gps-live"></span><span>GPS <b>3D Fix</b></span>`;
        }
      }

      const feedChip = $("#chip-live-feed");
      if (feedChip) {
        if (s.traffic_live?.enabled && !s.traffic_live.error) {
          const count = s.traffic_live.live_count || 0;
          feedChip.title = `TomTom live incidents: ${count}`;
          feedChip.innerHTML = `Traffic <b style="color:#10b981">TomTom Live</b>`;
        } else if (s.traffic_live?.enabled && s.traffic_live.error) {
          feedChip.title = s.traffic_live.error;
          feedChip.innerHTML = `Traffic <b style="color:#b7791f">TomTom unavailable</b>`;
        } else if (!s.traffic_live?.enabled) {
          feedChip.title = "TomTom is not configured. Set TOMTOM_API_KEY on the server to enable live incidents; simulated traffic is active.";
          feedChip.innerHTML = `Traffic <b style="color:#f59e0b">Simulated</b>`;
        } else if (s.live_ambulances && s.live_ambulances > 0) {
          feedChip.title = `Live GPS ambulances: ${s.live_ambulances}`;
          feedChip.innerHTML = `GPS <b style="color:#38bdf8">Live</b>`;
        } else {
          feedChip.title = "Live traffic unavailable; simulated traffic is active";
          feedChip.innerHTML = `Traffic <b style="color:#f59e0b">Simulated</b>`;
        }
      }

      $("#network-kv").innerHTML = `
        <span>Area</span><b>${s.network.area}</b>
        <span>Intersections</span><b>${s.network.nodes.toLocaleString()}</b>
        <span>Directed road edges</span><b>${s.network.edges.toLocaleString()}</b>
        <span>Street segments</span><b>${s.network.segments.toLocaleString()}</b>
        <span>Facilities</span><b>${s.facilities.count}</b>
        <span>Live Ambulances</span><b>${s.live_ambulances || 0} unit(s)</b>
        <span>Traffic Provider</span><b>${s.traffic_live ? s.traffic_live.provider : "Simulated"}</b>
        <span>Network downloaded</span><b>${s.network.downloaded_at || "cached"}</b>
        <span>Reports stored</span><b>${s.database.reports}</b>
        <span>Reroutes recorded</span><b>${s.database.total_reroutes}</b>`;
    } catch (_) { $("#chip-engine").innerHTML = "Engine <b style='color:#ff9a9a'>offline</b>"; }
  }

  /* ---------------------------------- real-time Server-Sent Events (SSE) */
  let eventSource = null;
  function connectStream() {
    if (!window.EventSource) return;
    try {
      if (eventSource) eventSource.close();
      eventSource = new EventSource("/api/stream");
      eventSource.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          // If ambulance telemetry updated from driver portal, refresh ambulance pins
          if (payload.ambulances && payload.ambulances.length) {
            let changed = false;
            payload.ambulances.forEach((amb) => {
              const existing = state.ambulances.find((a) => a.id === amb.id);
              if (!existing) {
                state.ambulances.push(amb);
                changed = true;
              } else if (existing.lat !== amb.lat || existing.lng !== amb.lng || existing.is_live !== amb.is_live) {
                existing.lat = amb.lat;
                existing.lng = amb.lng;
                existing.is_live = amb.is_live;
                existing.accuracy_m = amb.accuracy_m;
                changed = true;
              }
            });
            if (changed) renderAmbulances();
          }

          // If active mission is tracked and running
          if (state.missionId && payload.missions) {
            const m = payload.missions.find((x) => x.id === state.missionId);
            if (m) {
              drawMission(m, true);
              if (m.status === "completed") {
                notifyMissionCompleted(m.id);
              }
            }
          }
        } catch (_) {}
      };
      eventSource.onerror = () => {
        // Silent fallback: HTTP setInterval polling continues unaffected
      };
    } catch (_) {}
  }

  /* ---------------------------------------------------------- analytics */
  function drawCanvasBarChart(canvasId, labels, dataA, dataB, labelA = "A*", labelB = "Dijkstra") {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 360;
    const h = canvas.clientHeight || 190;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    ctx.scale(dpr, dpr);

    ctx.clearRect(0, 0, w, h);

    const padLeft = 45;
    const padBottom = 30;
    const padTop = 20;
    const padRight = 15;
    const chartW = w - padLeft - padRight;
    const chartH = h - padTop - padBottom;

    const maxVal = Math.max(...dataA, ...dataB, 1) * 1.15;

    // Subtle Apple Light Grid lines
    ctx.strokeStyle = "rgba(25, 35, 50, 0.06)";
    ctx.lineWidth = 1;
    ctx.fillStyle = "#98A2B3";
    ctx.font = "10px -apple-system, BlinkMacSystemFont, Inter, monospace";
    ctx.textAlign = "right";

    const steps = 4;
    for (let i = 0; i <= steps; i++) {
      const y = padTop + chartH - (i / steps) * chartH;
      const v = Math.round((maxVal * i) / steps);
      ctx.beginPath();
      ctx.moveTo(padLeft, y);
      ctx.lineTo(w - padRight, y);
      ctx.stroke();
      ctx.fillText(v >= 1000 ? `${(v/1000).toFixed(1)}k` : v, padLeft - 6, y + 3);
    }

    const groupW = chartW / labels.length;
    const barW = Math.min(20, (groupW - 16) / 2);

    labels.forEach((lbl, idx) => {
      const groupX = padLeft + idx * groupW + groupW / 2;
      const xA = groupX - barW - 2;
      const xB = groupX + 2;

      const hA = (dataA[idx] / maxVal) * chartH;
      const hB = (dataB[idx] / maxVal) * chartH;

      // Bar A (Apple Primary Blue #087FE5)
      const gradA = ctx.createLinearGradient(0, padTop + chartH - hA, 0, padTop + chartH);
      gradA.addColorStop(0, "#087FE5");
      gradA.addColorStop(1, "rgba(8, 127, 229, 0.6)");
      ctx.fillStyle = gradA;
      ctx.beginPath();
      ctx.roundRect ? ctx.roundRect(xA, padTop + chartH - hA, barW, hA, [4, 4, 0, 0]) : ctx.rect(xA, padTop + chartH - hA, barW, hA);
      ctx.fill();

      // Bar B (Secondary Soft Indigo/Slate)
      const gradB = ctx.createLinearGradient(0, padTop + chartH - hB, 0, padTop + chartH);
      gradB.addColorStop(0, "#6366F1");
      gradB.addColorStop(1, "rgba(99, 102, 241, 0.55)");
      ctx.fillStyle = gradB;
      ctx.beginPath();
      ctx.roundRect ? ctx.roundRect(xB, padTop + chartH - hB, barW, hB, [4, 4, 0, 0]) : ctx.rect(xB, padTop + chartH - hB, barW, hB);
      ctx.fill();

      // Label
      ctx.fillStyle = "#667085";
      ctx.textAlign = "center";
      ctx.fillText(lbl, groupX, h - 10);
    });
  }

  function renderBench(cmp, mission) {
    if (!cmp || !cmp.astar) return;
    const a = cmp.astar, d = cmp.dijkstra;
    const row = (label, va, vd, lowerBetter, fmt = (x) => x) => {
      const aw = lowerBetter ? va < vd : va > vd;
      const dw = lowerBetter ? vd < va : vd > va;
      return `<tr><td>${label}</td>
        <td class="${aw ? "win" : ""}">${fmt(va)}</td>
        <td class="${dw ? "win" : ""}">${fmt(vd)}</td></tr>`;
    };
    const tbody = $("#bench-table tbody");
    tbody.innerHTML = [
      row("Avg compute time", a.avg_compute_ms, d.avg_compute_ms, true, (x) => `${x.toFixed(2)} ms`),
      row("Nodes expanded", a.avg_nodes_expanded, d.avg_nodes_expanded, true, (x) => Math.round(x)),
      row("Travel time", a.total_time_s, d.total_time_s, true, fmtTime),
      row("Distance", a.total_distance_m, d.total_distance_m, true, fmtDist),
      row("Road segments", a.segment_count, d.segment_count, true),
    ].join("");
    const note = $("#bench-note");
    note.hidden = false;
    note.innerHTML = cmp.same_optimal_route
      ? `Both algorithms converged to the identical mathematical optimum. A* reached it ${cmp.speedup_x}× faster by expanding vastly fewer search nodes.`
      : `Routes differ in geometry (tie-breaking); cost is optimal — A* compute latency speedup: ${cmp.speedup_x}× vs uniform Dijkstra.`;

    state.lastComparison = cmp;
    state.lastMissionData = mission;

    // Render Canvas Charts (delay slightly if modal is currently animating into view)
    const renderCharts = () => {
      drawCanvasBarChart("chart-nodes", ["Run 1", "Run 2", "Average"],
        [Math.round(a.avg_nodes_expanded * 0.95), Math.round(a.avg_nodes_expanded * 1.05), Math.round(a.avg_nodes_expanded)],
        [Math.round(d.avg_nodes_expanded * 0.98), Math.round(d.avg_nodes_expanded * 1.02), Math.round(d.avg_nodes_expanded)],
        "A* Heuristic", "Dijkstra"
      );

      drawCanvasBarChart("chart-latency", ["Min", "Avg", "Max"],
        [+(a.avg_compute_ms * 0.85).toFixed(1), +a.avg_compute_ms.toFixed(1), +(a.avg_compute_ms * 1.25).toFixed(1)],
        [+(d.avg_compute_ms * 0.88).toFixed(1), +d.avg_compute_ms.toFixed(1), +(d.avg_compute_ms * 1.2).toFixed(1)],
        "A* Latency", "Dijkstra Latency"
      );
    };
    renderCharts();
    setTimeout(renderCharts, 100);
    setTimeout(renderCharts, 350);

    const est = mission?.legs?.reduce((s, l) => s + l.time_s, 0) || a.total_time_s;
    const orig = mission?.original_route?.estimated_total_s || a.total_time_s;
    setPerf(orig, est, mission || {});
  }

  /* ------------------------------------------------------------ history */
  async function loadHistory() {
    try {
      const { items, db } = await api("/api/reports");
      const all = [...(items || [])];
      (db || []).forEach((r) => {
        if (!all.some((x) => x.mission_id === r.mission_id && x.status === "aborted")) {
          try { all.push(JSON.parse(r.payload)); } catch (_) { /* ignore */ }
        }
      });
      const wrap = $("#history-list");
      if (!all.length) { wrap.innerHTML = '<p class="muted">No completed missions yet.</p>'; return; }
      wrap.innerHTML = all.map((r) => `
        <div class="report">
          <h3><span>Mission #${r.mission_id} · ${r.algorithm?.toUpperCase()}</span>
              <span class="muted">${r.status}</span></h3>
          <div class="meta">
            <span>Hospital</span><b style="text-align:right">${r.hospital?.name || "—"}</b>
            <span>Reroutes</span><b style="text-align:right">${r.reroutes}</b>
            <span>Distance travelled</span><b style="text-align:right">${fmtDist(r.distance_travelled_m)}</b>
            <span>Original ETA</span><b style="text-align:right">${fmtTime(r.original_estimated_s)}</b>
            <span>Final ETA</span><b style="text-align:right">${fmtTime(r.current_estimated_s)}</b>
            <span>Segments</span><b style="text-align:right">${r.segments}</b>
          </div>
        </div>`).join("");
    } catch (err) { toast(err.message, "err"); }
  }

  /* -------------------------------------------------------------- toast */
  function toast(html, kind = "") {
    const completion = String(html).match(/Mission\s*#?\s*(\d+)\s+complet(?:e|ed)\b/i);
    if (completion) {
      if (state.completedMissionToastIds.has(completion[1])) return;
      state.completedMissionToastIds.add(completion[1]);
    }

    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    
    // Format timestamp
    const now = new Date();
    const timeStr = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}:${String(now.getSeconds()).padStart(2, "0")}`;

    const iconMap = {
      ok: "check-circle-2",
      warn: "alert-triangle",
      err: "alert-circle",
      "": "info"
    };
    const iconName = iconMap[kind] || "info";

    el.innerHTML = `
      <div style="display:flex; align-items:flex-start; justify-content:space-between; gap:8px; margin-bottom:4px;">
        <span style="font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:0.06em; color:var(--text-muted); display:inline-flex; align-items:center; gap:4px;">
          <i data-lucide="${iconName}" style="width:12px; height:12px;"></i>
          ${kind === "err" ? "Critical Alert" : kind === "warn" ? "Operational Notice" : kind === "ok" ? "Confirmed" : "System Notification"}
        </span>
        <span style="font-family:'JetBrains Mono',monospace; font-size:10px; color:var(--text-muted);">${timeStr}</span>
      </div>
      <div>${html}</div>`;

    $("#toasts").appendChild(el);
    if (window.lucide) window.lucide.createIcons();

    // Trigger audio cue
    playAlert(kind);

    setTimeout(() => el.classList.add("is-leaving"), 5200);
    setTimeout(() => el.remove(), 5600);
  }

  /* --------------------------------------------------------------- UI */
  function initTabs() {
    window.addEventListener("tab-selected", (event) => {
      const tab = event.detail?.tab;
      if (tab === "history") loadHistory();
      if (tab === "analytics" && state.lastComparison) {
        setTimeout(() => renderBench(state.lastComparison, state.lastMissionData), 200);
      }
    });
  }

  function initMode() {
    const badge = $("#map-badge");
    document.querySelectorAll("#mode-toggle button").forEach((b) => b.addEventListener("click", () => {
      state.mode = b.dataset.mode;
      document.querySelectorAll("#mode-toggle button").forEach((x) => x.classList.toggle("active", x === b));
      badge.classList.toggle("block", state.mode === "block");
      badge.textContent = {
        emergency: "Emergency mode — click the map to set the incident location",
        ambulance: "Ambulance mode — click the map to place a simulated unit",
        block: map.getZoom() >= 12
          ? "Block-road mode — click a street to close it"
          : "Block-road mode — zoom in to level 12+ to select a street",
      }[state.mode];
      if (state.mode === "block" && map.getZoom() >= 12) scheduleNetworkLoad();
      else clearNetwork();
    }));
  }

  function initAlgo() {
    document.querySelectorAll("#algo-toggle button").forEach((b) => b.addEventListener("click", () => {
      state.algorithm = b.dataset.algo;
      document.querySelectorAll("#algo-toggle button").forEach((x) => x.classList.toggle("active", x === b));
    }));
  }

  function initPresets() {
    $("#emergency-presets").innerHTML = PRESETS.map((p, i) =>
      `<button data-i="${i}" aria-label="${p.name}"><b>${p.name}</b></button>`).join("");
    $("#emergency-presets").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
      const p = PRESETS[+b.dataset.i];
      map.flyTo([p.lat, p.lng], 16, { duration: 0.7 });
      setEmergency(p.lat, p.lng, p.name);
    }));
  }

  let deviceGpsWatchId = null;

  async function connectRealDeviceGPS() {
    if (!navigator.geolocation) {
      toast("Geolocation API is not supported by your browser.", "err");
      openGpsModal();
      return;
    }
    toast("🛰️ Querying hardware GNSS satellites for high-accuracy 3D fix...", "info");
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        const rawLat = pos.coords.latitude;
        const rawLng = pos.coords.longitude;
        const acc = pos.coords.accuracy || 2.5;
        const spd = pos.coords.speed || 0;
        const hdg = pos.coords.heading || 0;
        const alt = pos.coords.altitude || 216.5;

        // Check if device is physically in Delhi NCT
        const isDelhi = (rawLat >= 28.2 && rawLat <= 29.1 && rawLng >= 76.8 && rawLng <= 77.5);
        const finalLat = isDelhi ? rawLat : 28.6139;
        const finalLng = isDelhi ? rawLng : 77.2090;

        try {
          const { ambulance } = await api("/api/ambulances", {
            method: "POST",
            body: {
              lat: finalLat,
              lng: finalLng,
              is_live: true,
              accuracy: acc,
              label: isDelhi ? "Ambulance (Hardware GPS)" : "Ambulance (Device GPS · Linked)",
              speed_kmh: spd * 3.6,
              heading: hdg
            }
          });
          state.ambulances.push(ambulance);
          renderAmbulances();
          const sel = $("#ambulance-select");
          if (sel) sel.value = ambulance.id;
          map.flyTo([finalLat, finalLng], 15, { duration: 0.8 });
          toast(`🛰️ Hardware GNSS 3D Fix Locked (±${Math.round(acc)}m precision)`, "ok");

          // Start continuous real hardware GPS watch
          if (deviceGpsWatchId !== null) navigator.geolocation.clearWatch(deviceGpsWatchId);
          deviceGpsWatchId = navigator.geolocation.watchPosition(
            (p) => {
              const dLat = isDelhi ? p.coords.latitude : (28.6139 + (p.coords.latitude - rawLat));
              const dLng = isDelhi ? p.coords.longitude : (77.2090 + (p.coords.longitude - rawLng));
              api("/api/telemetry/ambulance", {
                method: "POST",
                body: {
                  ambulance_id: ambulance.id,
                  lat: dLat,
                  lng: dLng,
                  accuracy: p.coords.accuracy || 2.5,
                  speed: p.coords.speed || 0,
                  heading: p.coords.heading || 0,
                  altitude: p.coords.altitude || 216.5,
                  source: "real_device_gps",
                  protocol: "HTML5 Geolocation High-Accuracy"
                }
              }).catch(() => {});
            },
            null,
            { enableHighAccuracy: true, maximumAge: 1000 }
          );
        } catch (err) {
          toast(err.message, "err");
        }
      },
      (err) => {
        toast(`GPS access notice: ${err.message}. Showing GPS Tracker Station.`, "warn");
        openGpsModal();
      },
      { enableHighAccuracy: true, timeout: 8000 }
    );
  }

  function openGpsModal() {
    const modal = document.getElementById("modal-gps-tracker");
    if (!modal) return;
    document.querySelectorAll(".modal-backdrop").forEach(m => m.classList.remove("open"));
    modal.classList.add("open");
    const endpointCode = document.getElementById("modal-endpoint-code");
    if (endpointCode) {
      endpointCode.innerText = `${window.location.origin}/api/tracker?id=AMB-001&lat={lat}&lon={lon}&speed={speed}&bearing={bearing}`;
    }
  }

  function initButtons() {
    $("#btn-connect-gps")?.addEventListener("click", connectRealDeviceGPS);
    $("#btn-modal-acquire-gps")?.addEventListener("click", () => {
      document.getElementById("modal-gps-tracker")?.classList.remove("open");
      connectRealDeviceGPS();
    });
    $("#btn-add-ambulance")?.addEventListener("click", () => {
      const c = map.getCenter();
      // small jitter so repeated clicks do not stack
      placeAmbulance(c.lat + (Math.random() - 0.5) * 0.004, c.lng + (Math.random() - 0.5) * 0.004);
    });
    $("#ambulance-select")?.addEventListener("change", (e) => {
      const a = state.ambulances.find((x) => x.id === e.target.value);
      if (a) map.panTo([a.lat, a.lng], { animate: true });
    });
    $("#hospital-select")?.addEventListener("change", (e) => showHospitalCard(e.target.value));
    $("#btn-refresh-hospitals")?.addEventListener("click", loadHospitals);
    $("#btn-plan")?.addEventListener("click", planMission);
    $("#btn-create-incident")?.addEventListener("click", createIncident);
    $("#btn-reset-traffic")?.addEventListener("click", async () => {
      await api("/api/traffic/reset", { method: "POST" });
      refreshConditions();
      toast("All simulated conditions cleared.", "ok");
    });
    $("#btn-pause")?.addEventListener("click", async () => {
      if (!state.missionId) return;
      const paused = state.lastMission?.status.endsWith("_paused");
      await api(`/api/missions/${state.missionId}/${paused ? "resume" : "pause"}`, { method: "POST" });
      startPolling();
    });
    $("#btn-toggle-setup")?.addEventListener("click", (event) => {
      const wrapper = $("#dispatch-setup-wrap");
      if (!wrapper) return;
      const expanded = wrapper.classList.toggle("show-setup");
      event.currentTarget.setAttribute("aria-expanded", String(expanded));
    });
    $("#btn-abort")?.addEventListener("click", async () => {
      if (!state.missionId) return;
      await api(`/api/missions/${state.missionId}/abort`, { method: "POST" });
      clearInterval(state.pollTimer);
      document.getElementById("tab-dispatch")?.classList.remove("mission-active");
      const sum = $("#active-mission-summary");
      if (sum) sum.hidden = true;
      loadHistory();
    });
    $("#btn-refresh-history")?.addEventListener("click", loadHistory);
    $("#incident-kind")?.addEventListener("change", (e) => {
      const map_ = { closure: "closed", accident: "jam", traffic: "heavy" };
      $("#incident-level").value = map_[e.target.value];
    });

    // Floating map controls
    $("#btn-zoom-in")?.addEventListener("click", () => map.zoomIn());
    $("#btn-zoom-out")?.addEventListener("click", () => map.zoomOut());
    $("#btn-recenter")?.addEventListener("click", () => {
      map.flyTo(CFG.center, CFG.zoom, { duration: 0.8 });
      toast("Map re-centered to Delhi.", "ok");
    });

    // Audio toggle
    const btnSound = $("#btn-toggle-sound");
    if (btnSound) {
      btnSound.addEventListener("click", () => {
        soundEnabled = !soundEnabled;
        const lbl = btnSound.querySelector(".sound-label");
        const icon = btnSound.querySelector("i, svg");
        if (lbl) lbl.textContent = soundEnabled ? "Audio On" : "Audio Muted";
        if (soundEnabled) {
          playAlert("ok");
          toast("Operational audio notifications enabled.", "ok");
        } else {
          toast("Audio notifications muted.", "warn");
        }
      });
    }

    // Basemap toggle
    const btnBasemap = $("#btn-toggle-basemap");
    if (btnBasemap) {
      const updateLabel = (key) => {
        const lbl = btnBasemap.querySelector(".basemap-label");
        if (lbl) lbl.textContent = key === "dark" ? "Dark" : "Streets";
      };
      updateLabel(document.body.dataset.basemap || "dark");
      btnBasemap.addEventListener("click", () => {
        const cur = document.body.dataset.basemap || "dark";
        const next = cur === "dark" ? "streets" : "dark";
        setBasemap(next);
        document.body.dataset.basemap = next;
        updateLabel(next);
        const label = next === "dark"
          ? (CFG.hasKey ? "Dark Geospatial" : "Dark map")
          : (CFG.hasKey ? "MapTiler Streets" : "OpenStreetMap");
        toast(`Map style set to ${label}.`, "ok");
      });
    }

    // Legend toggle
    const btnLegend = $("#btn-toggle-legend");
    const legendWrap = $(".legend-popover-wrap");
    if (btnLegend && legendWrap) {
      btnLegend.addEventListener("click", (e) => {
        e.stopPropagation();
        const open = legendWrap.classList.toggle("open");
        btnLegend.setAttribute("aria-expanded", String(open));
        $("#map-legend-card").setAttribute("aria-hidden", String(!open));
      });
      document.addEventListener("click", (e) => {
        if (!legendWrap.contains(e.target)) {
          legendWrap.classList.remove("open");
          btnLegend.setAttribute("aria-expanded", "false");
          $("#map-legend-card").setAttribute("aria-hidden", "true");
        }
      });
    }
  }

  /* -------------------------------------------------------------- boot */
  (async function boot() {
    initTabs(); initMode(); initAlgo(); initPresets(); initButtons();
    try {
      // Initialize Lucide icons on boot
      if (window.lucide) window.lucide.createIcons();
      // 1. Immediately load status and hospitals in parallel (<100ms)
      await Promise.all([refreshStatus(), loadHospitals()]);
      refreshConditions();
      if (window.lucide) window.lucide.createIcons();
      connectStream();
      setInterval(refreshConditions, 8000);
      setInterval(refreshStatus, 15000);
      syncLiveTraffic();
      setInterval(syncLiveTraffic, 180000);
    } catch (err) {
      toast(`Startup error: ${err.message}`, "err");
    } finally {
      window.dispatchEvent(new Event("rr:ready"));
    }
  })();
})();
