"use strict";

const ROUTES = new Set(["/", "/ride", "/pools", "/driver", "/account"]);

const state = {
  route: "/",
  token: sessionStorage.getItem("poolsmart-token") || "",
  user: null,
  localPreview: false,
  demoMode: false,
  demoPlaces: [],
  demoCampus: null,
  config: null,
  map: null,
  selectedPlaces: { pickup: null, drop: null },
  selectedPoolId: null,
  selectedRideId: null,
  activeTripId: null,
  activeRideId: null,
  activeDriverTripId: null,
  activeDemoTrip: null,
  lastRideStatuses: new Map(),
  openPools: [],
  rides: [],
  savedPlaceIds: JSON.parse(localStorage.getItem("poolsmart-saved-places") || '["demo-campus","demo-hostel","demo-mall","demo-station","demo-airport"]'),
  tripSocket: null,
  driverSocket: null,
  driverWatch: null,
  driverTimer: null,
  lastDriverPosition: null,
  driverOnline: false,
  offerTimer: null,
  routes: new Map(),
  hubs: [],
  driverMarker: null,
};

const copy = {
  en: {
    navHome: "HOME",
    navRide: "RIDE",
    navPools: "OPEN POOLS",
    navDriver: "DRIVER",
    navAccount: "ACCOUNT",
    heroCta: "REQUEST A RIDE",
    heroLineOne: "One road.",
    heroLineTwo: "More than one way home.",
    heroSubtitle: "Request a verified cab, share the route, and keep your pickup limits in view.",
    accountHeading: "Manage your account and preferences.",
    rideHeading: "Choose your pickup and destination.",
    poolHeading: "Rides forming now.",
    driverHeading: "Drive with the campus fleet.",
  },
  hi: {
    navHome: "मुख्य पृष्ठ",
    navRide: "सवारी",
    navPools: "खुले समूह",
    navDriver: "चालक",
    navAccount: "खाता",
    heroCta: "सवारी माँगें",
    heroLineOne: "एक रास्ता।",
    heroLineTwo: "घर पहुँचने के कई रास्ते।",
    heroSubtitle: "सत्यापित कैब माँगें, रास्ता साझा करें और पिकअप की सीमाएँ देखें।",
    accountHeading: "खाता और प्राथमिकताएँ प्रबंधित करें।",
    rideHeading: "पिकअप और गंतव्य चुनें।",
    poolHeading: "अभी बन रहे साझा सफ़र।",
    driverHeading: "कैंपस बेड़े के साथ ड्राइव करें।",
  },
  te: {
    navHome: "హోమ్",
    navRide: "ప్రయాణం",
    navPools: "ఓపెన్ పూల్స్",
    navDriver: "డ్రైవర్",
    navAccount: "ఖాతా",
    heroCta: "ప్రయాణాన్ని కోరండి",
    heroLineOne: "ఒకే దారి.",
    heroLineTwo: "ఇంటికి చేరే మార్గాలు మరెన్నో.",
    heroSubtitle: "ధృవీకరించిన క్యాబ్‌ను కోరండి, మార్గాన్ని పంచుకోండి, పికప్ పరిమితులను గమనించండి.",
    accountHeading: "మీ ఖాతా మరియు ప్రాధాన్యతలను నిర్వహించండి.",
    rideHeading: "పికప్ మరియు గమ్యస్థానాన్ని ఎంచుకోండి.",
    poolHeading: "ఇప్పుడు ఏర్పడుతున్న ప్రయాణాలు.",
    driverHeading: "క్యాంపస్ వాహన బృందంలో డ్రైవ్ చేయండి.",
  },
};

const $ = (id) => document.getElementById(id);

const make = (tag, text, className) => {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
};

function setHidden(id, hidden) {
  const node = $(id);
  if (node) node.hidden = Boolean(hidden);
}

function message(id, text = "", error = false) {
  const output = $(id);
  if (!output) return;
  output.textContent = text;
  output.classList.toggle("error", Boolean(error));
}

const activityLog = [];

function renderActivityDrawer() {
  const list = $("activity-list");
  const countBadge = $("nav-notif-count");
  if (countBadge) {
    const unread = activityLog.filter((item) => !item.read).length;
    countBadge.textContent = String(unread);
    countBadge.hidden = unread === 0;
  }
  if (!list) return;
  list.replaceChildren();
  if (!activityLog.length) {
    list.append(make("p", "No recent alerts yet.", "empty-note"));
    return;
  }
  activityLog.slice(0, 20).forEach((entry) => {
    const row = make("div", undefined, "activity-entry");
    row.append(make("strong", entry.title), make("span", entry.detail), make("time", entry.time));
    list.append(row);
  });
}

function notify(title, detail, isError = false) {
  activityLog.unshift({ title, detail, isError, read: false, time: new Date().toLocaleTimeString() });
  if (activityLog.length > 30) activityLog.length = 30;
  renderActivityDrawer();
  const stack = $("toast-stack");
  if (!stack) return;
  const item = make("div", undefined, `toast-item${isError ? " error" : ""}`);
  item.append(make("strong", title), make("span", detail));
  stack.append(item);
  window.setTimeout(() => item.remove(), 4500);
}

function openTripModal(kicker, title, bodyText, actionNodes = []) {
  $("modal-kicker").textContent = kicker;
  $("modal-title").textContent = title;
  $("modal-body").textContent = bodyText;
  const actions = $("modal-actions");
  actions.replaceChildren(...actionNodes);
  setHidden("trip-modal", false);
}

function closeTripModal() {
  setHidden("trip-modal", true);
}

async function api(url, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body !== undefined && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
  const body = options.body && typeof options.body === "object" && !(options.body instanceof FormData)
    ? JSON.stringify(options.body)
    : options.body;
  const response = await fetch(url, { ...options, headers, body });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    const detail = payload && typeof payload === "object" ? payload.detail || payload.error : "";
    throw new Error(detail || `Request failed (${response.status}).`);
  }
  return payload;
}

function routeKey(path) {
  const normalized = path === "/" ? "/" : path.replace(/\/+$/, "");
  if (!ROUTES.has(normalized)) return "home";
  return normalized === "/" ? "home" : normalized.slice(1);
}

function navigateTo(path, replace = false) {
  const normalized = ROUTES.has(path) ? path : "/";
  const targetPage = routeKey(normalized);
  state.route = normalized;
  if (location.pathname !== normalized) {
    if (replace) history.replaceState({ path: normalized }, "", normalized);
    else history.pushState({ path: normalized }, "", normalized);
  }
  document.querySelectorAll(".page-view").forEach((section) => {
    section.hidden = section.dataset.page !== targetPage;
  });
  document.querySelectorAll(".topbar nav a[data-route]").forEach((link) => {
    link.classList.toggle("active", link.dataset.route === targetPage);
  });
  updateAuthGates();
  window.scrollTo({ top: 0, behavior: "instant" });
  if (targetPage === "ride" && state.demoMode) renderBookingRouteSvg();
  if (targetPage === "pools" && state.token) loadPools().catch(() => {});
  if (targetPage === "driver" && state.token) loadDriver().catch(() => {});
  if (targetPage === "account" && state.token && state.user?.role === "admin") loadAdmin().catch(() => {});
}

function updateAuthGates() {
  const signedIn = Boolean(state.token && state.user);
  setHidden("ride-auth-gate", signedIn || state.localPreview);
  setHidden("ride-form", !signedIn || state.localPreview);
  setHidden("driver-auth-gate", signedIn || state.localPreview);
  if (!signedIn) {
    setHidden("driver-form", true);
    setHidden("driver-tools", true);
  }
}

function localDateTimeToIso(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) throw new Error("Enter a valid local date and time.");
  return date.toISOString();
}

function displayTime(value) {
  if (!value) return "Not scheduled";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Time unavailable" : date.toLocaleString();
}

function haversineKm(lat1, lon1, lat2, lon2) {
  const toRad = (deg) => (deg * Math.PI) / 180;
  const r = 6371.0088;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a = Math.sin(dLat / 2) ** 2
    + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return r * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function applyLanguage(language) {
  const selected = copy[language] ? language : "en";
  const strings = copy[selected];
  document.documentElement.lang = selected === "en" ? "en-IN" : selected === "te" ? "te-IN" : "hi-IN";
  document.querySelectorAll("[data-i18n]").forEach((node) => {
    const key = node.dataset.i18n;
    node.textContent = strings[key] || copy.en[key] || node.textContent;
  });
  if (state.localPreview) applyLocalPreviewCopy();
  if (state.demoMode) applyDemoCopy();
}

function updateModeIndicator() {
  const pill = $("mode-indicator");
  if (!pill) return;
  if (state.localPreview) {
    pill.textContent = "PREVIEW";
    pill.hidden = false;
    return;
  }
  if (!state.demoMode) {
    pill.hidden = true;
    return;
  }
  pill.hidden = false;
  pill.textContent = state.user
    ? `DEMO · ${state.user.name.split(" ")[0].toUpperCase()} (${state.user.role.toUpperCase()})`
    : "LOCAL DEMO";
}

function updateFareImpactCard() {
  const card = $("fare-impact-card");
  if (!card) return;
  const pickup = state.selectedPlaces.pickup;
  const drop = state.selectedPlaces.drop;
  if (
    !pickup || !drop || pickup.place_id === drop.place_id
    || !Number.isFinite(pickup.latitude) || !Number.isFinite(pickup.longitude)
    || !Number.isFinite(drop.latitude) || !Number.isFinite(drop.longitude)
  ) {
    card.hidden = true;
    return;
  }
  const roadFactor = state.demoMode ? 1.25 : 1.35;
  const speedKmh = state.demoMode ? 24 : 25;
  const km = haversineKm(pickup.latitude, pickup.longitude, drop.latitude, drop.longitude) * roadFactor;
  const mins = Math.max(1, (km / speedKmh) * 60);
  const hour = new Date().getHours();
  const surge = hour >= 21 || hour < 6 ? 1.2 : 1.0;
  const soloFare = (25 + km * 12 + mins * 1) * surge;
  const detourFactor = Number($("ride-detour")?.value || 1.3);
  const maxWait = Number($("ride-wait")?.value || 15);
  const poolSize = Number($("pool-size-slider")?.value || 2);
  if ($("pool-size-label")) $("pool-size-label").textContent = `${poolSize} RIDERS`;
  const splitRatio = poolSize === 2 ? 0.62 : poolSize === 3 ? 0.46 : 0.36;
  const pooledFare = soloFare * splitRatio;
  const savingsPct = Math.round(((soloFare - pooledFare) / soloFare) * 100);
  const detourMins = Math.max(1, Math.round(mins * (detourFactor - 1)));
  const co2Saved = (km * 0.12 * (poolSize - 1)).toFixed(2);
  $("impact-solo-fare").textContent = `INR ${soloFare.toFixed(0)}`;
  $("impact-pool-fare").textContent = `INR ${pooledFare.toFixed(0)}`;
  $("impact-savings").textContent = `${savingsPct}%`;
  $("impact-wait").textContent = `~${Math.min(6, maxWait)} min`;
  $("impact-detour").textContent = `+${detourMins} min`;
  $("impact-co2").textContent = `${co2Saved} kg`;
  const trustPill = $("trust-score-pill");
  if (trustPill && state.user) {
    let score = 50;
    if (state.user.phone) score += 15;
    if ((state.user.emergency_contacts || []).length) score += 20;
    if (state.user.gender_verified) score += 15;
    trustPill.textContent = `TRUST SCORE ${score}/100`;
    trustPill.className = `status-pill ${score >= 85 ? "status-confirmed" : "status-offered"}`;
  }
  card.hidden = false;
}

function renderBookingRouteSvg(activeTrip = state.activeDemoTrip) {
  const svg = $("ride-route-svg");
  const badge = $("route-preview-badge");
  if (!svg) return;
  const svgNs = "http://www.w3.org/2000/svg";
  const el = (tag, attrs = {}, text = null) => {
    const node = document.createElementNS(svgNs, tag);
    Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, String(v)));
    if (text !== null) node.textContent = text;
    return node;
  };
  svg.replaceChildren();
  const pickup = state.selectedPlaces.pickup;
  const drop = state.selectedPlaces.drop;
  if (activeTrip && activeTrip.stops?.length) {
    const stops = activeTrip.stops.filter((s) => Number.isFinite(s.latitude) && Number.isFinite(s.longitude));
    if (stops.length >= 2) {
      const lats = stops.map((s) => s.latitude);
      const lons = stops.map((s) => s.longitude);
      const minLat = Math.min(...lats);
      const maxLat = Math.max(...lats);
      const minLon = Math.min(...lons);
      const maxLon = Math.max(...lons);
      const project = (lat, lon) => ({
        x: 64 + (maxLon === minLon ? 0.5 : (lon - minLon) / (maxLon - minLon)) * 432,
        y: 44 + (maxLat === minLat ? 0.5 : (maxLat - lat) / (maxLat - minLat)) * 192,
      });
      const points = stops.map((s) => project(s.latitude, s.longitude));
      svg.append(el("polyline", {
        class: "demo-map-active-route",
        points: points.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" "),
      }));
      stops.forEach((s, index) => {
        const pt = points[index];
        const g = el("g", { class: `demo-map-node${s.completed ? "" : " selected"}`, transform: `translate(${pt.x.toFixed(1)} ${pt.y.toFixed(1)})` });
        g.append(el("circle", { r: 6 }));
        g.append(el("text", { y: index % 2 === 0 ? -12 : 18 }, `${index + 1}. ${s.place.replace(/^Demo\s+/i, "").toUpperCase()}`));
        svg.append(g);
      });
      if (activeTrip.driver_location && Number.isFinite(activeTrip.driver_location.latitude)) {
        const cab = project(activeTrip.driver_location.latitude, activeTrip.driver_location.longitude);
        const cabG = el("g", { class: "demo-map-cab", transform: `translate(${cab.x.toFixed(1)} ${cab.y.toFixed(1)})` });
        cabG.append(el("circle", { class: "cab-pulse", r: 12 }), el("circle", { r: 7 }), el("text", { y: -14 }, "CAB"));
        svg.append(cabG);
      }
      const km = (Number(activeTrip.distance_m || 0) / 1000).toFixed(1);
      const mins = Math.max(1, Math.round(Number(activeTrip.duration_s || 0) / 60));
      if (badge) badge.textContent = `${km} km · ~${mins} min · ${activeTrip.state.toUpperCase()}`;
      return;
    }
  }
  if (!pickup || !drop) {
    svg.append(el("text", { x: 280, y: 145, fill: "#aaa89e", "text-anchor": "middle", "font-size": 12 }, "Choose pickup and destination to preview live route"));
    if (badge) badge.textContent = "Select pickup & drop-off";
    return;
  }
  const roadFactor = state.demoMode ? 1.25 : 1.35;
  const speedKmh = state.demoMode ? 24 : 25;
  const km = Number.isFinite(pickup.latitude) && Number.isFinite(drop.latitude)
    ? haversineKm(pickup.latitude, pickup.longitude, drop.latitude, drop.longitude) * roadFactor
    : 4.5;
  const mins = Math.max(1, Math.ceil((km / speedKmh) * 60));
  if (badge) badge.textContent = `${km.toFixed(1)} km · ~${mins} min ETA`;
  svg.append(el("path", {
    class: "demo-map-active-route",
    d: "M 92 200 C 190 95, 360 185, 468 82",
  }));
  const startG = el("g", { class: "demo-map-node campus selected", transform: "translate(92 200)" });
  startG.append(el("circle", { r: 7 }), el("text", { y: 22 }, pickup.name.replace(/^Demo\s+/i, "").toUpperCase()));
  const endG = el("g", { class: "demo-map-node selected", transform: "translate(468 82)" });
  endG.append(el("circle", { r: 7 }), el("text", { y: -14 }, drop.name.replace(/^Demo\s+/i, "").toUpperCase()));
  svg.append(startG, endG);
}

function renderDemoSvgMap(activeTrip = state.activeDemoTrip) {
  const svg = $("demo-map-svg");
  if (!svg || !state.demoPlaces.length) return;
  state.activeDemoTrip = activeTrip || null;
  const campus = state.demoCampus || state.demoPlaces[0];
  const mobile = window.innerWidth <= 600;
  const centerX = mobile ? 500 : 755;
  const centerY = mobile ? 360 : 375;
  const radiusX = mobile ? 185 : 145;
  const radiusY = mobile ? 170 : 170;
  const rawOffsets = state.demoPlaces.map((p) => ({
    dLat: p.latitude - campus.latitude,
    dLon: p.longitude - campus.longitude,
  }));
  const maxSpan = Math.max(0.001, ...rawOffsets.map((o) => Math.hypot(o.dLat, o.dLon)));
  const project = (lat, lon) => {
    const dLat = lat - campus.latitude;
    const dLon = lon - campus.longitude;
    const dist = Math.hypot(dLat, dLon);
    if (dist === 0) return { x: centerX, y: centerY };
    const scaled = Math.pow(dist / maxSpan, 0.42);
    return {
      x: centerX + (dLon / dist) * scaled * radiusX,
      y: centerY + (-dLat / dist) * scaled * radiusY,
    };
  };
  const svgNs = "http://www.w3.org/2000/svg";
  const el = (tag, attrs = {}, text = null) => {
    const node = document.createElementNS(svgNs, tag);
    Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, String(v)));
    if (text !== null) node.textContent = text;
    return node;
  };
  svg.replaceChildren();
  const campusPt = project(campus.latitude, campus.longitude);
  state.demoPlaces.forEach((place) => {
    if (place.place_id === campus.place_id) return;
    const pt = project(place.latitude, place.longitude);
    svg.append(el("line", {
      class: "demo-map-edge",
      x1: campusPt.x.toFixed(1),
      y1: campusPt.y.toFixed(1),
      x2: pt.x.toFixed(1),
      y2: pt.y.toFixed(1),
    }));
  });
  const selectedIds = new Set(
    [state.selectedPlaces.pickup?.place_id, state.selectedPlaces.drop?.place_id].filter(Boolean)
  );
  if (activeTrip && activeTrip.stops?.length) {
    const pts = activeTrip.stops
      .filter((s) => Number.isFinite(s.latitude) && Number.isFinite(s.longitude))
      .map((s) => {
        const p = project(s.latitude, s.longitude);
        return `${p.x.toFixed(1)},${p.y.toFixed(1)}`;
      });
    if (pts.length >= 2) {
      svg.append(el("polyline", { class: "demo-map-active-route", points: pts.join(" ") }));
    }
  } else if (
    state.selectedPlaces.pickup && state.selectedPlaces.drop
    && Number.isFinite(state.selectedPlaces.pickup.latitude)
    && Number.isFinite(state.selectedPlaces.drop.latitude)
  ) {
    const p1 = project(state.selectedPlaces.pickup.latitude, state.selectedPlaces.pickup.longitude);
    const p2 = project(state.selectedPlaces.drop.latitude, state.selectedPlaces.drop.longitude);
    svg.append(el("line", {
      class: "demo-map-active-route",
      x1: p1.x.toFixed(1),
      y1: p1.y.toFixed(1),
      x2: p2.x.toFixed(1),
      y2: p2.y.toFixed(1),
    }));
  }
  state.demoPlaces.forEach((place) => {
    const pt = project(place.latitude, place.longitude);
    const isCampus = place.kind === "campus";
    const isSelected = selectedIds.has(place.place_id);
    const group = el("g", {
      class: `demo-map-node${isCampus ? " campus" : ""}${isSelected ? " selected" : ""}`,
      transform: `translate(${pt.x.toFixed(1)} ${pt.y.toFixed(1)})`,
    });
    group.append(el("title", {}, `${place.name} — click to book`));
    group.append(el("circle", { class: "demo-map-hit", r: 18 }));
    group.append(el("circle", { r: isSelected ? 7.5 : isCampus ? 6.5 : 4.5 }));
    const shortLabel = place.name.replace(/^Demo\s+/i, "").toUpperCase();
    if (place.kind !== "campus_hub" || isSelected) {
      group.append(el("text", { y: isCampus ? 20 : pt.y > centerY ? 18 : -11 }, shortLabel));
    }
    group.addEventListener("click", () => {
      if (isCampus) {
        setSelectedPlace("pickup", place);
      } else {
        if (!state.selectedPlaces.pickup && state.demoCampus) {
          setSelectedPlace("pickup", state.demoCampus);
        }
        setSelectedPlace("drop", place);
      }
      navigateTo("/ride");
      notify("Route pre-filled", `Selected ${place.name}. Review fare and submit on /ride.`);
    });
    svg.append(group);
  });
  if (activeTrip?.driver_location && Number.isFinite(activeTrip.driver_location.latitude)) {
    const cab = project(activeTrip.driver_location.latitude, activeTrip.driver_location.longitude);
    const cabNode = el("g", { class: "demo-map-cab", transform: `translate(${cab.x.toFixed(1)} ${cab.y.toFixed(1)})` });
    cabNode.append(el("circle", { class: "cab-pulse", r: 12 }), el("circle", { r: 7 }), el("text", { y: -14 }, "CAB"));
    svg.append(cabNode);
  }
}

function renderPlaceChips(kind) {
  const container = $(`${kind}-chips`);
  if (!container || !state.demoPlaces.length) return;
  container.replaceChildren();
  const subset = kind === "pickup"
    ? state.demoPlaces.filter((p) => p.kind !== "destination").concat(state.demoPlaces.filter((p) => p.kind === "destination").slice(0, 3))
    : state.demoPlaces.filter((p) => p.kind === "destination").concat(state.demoPlaces.filter((p) => p.kind === "campus_hub").slice(0, 2));
  subset.forEach((place) => {
    const active = state.selectedPlaces[kind]?.place_id === place.place_id;
    const chip = make("button", place.name.replace(/^Demo\s+/i, ""), `place-chip${active ? " active" : ""}`);
    chip.type = "button";
    chip.addEventListener("click", () => setSelectedPlace(kind, place));
    container.append(chip);
  });
}

function setSelectedPlace(kind, place, keepPool = false) {
  let enriched = place;
  if (place && (!Number.isFinite(place.latitude) || !Number.isFinite(place.longitude))) {
    const known = state.hubs.find((item) => item.place_id === place.place_id);
    if (known) {
      enriched = { ...known, ...place, latitude: known.latitude, longitude: known.longitude };
    }
  }
  state.selectedPlaces[kind] = enriched;
  if (kind === "drop" && !keepPool) {
    state.selectedPoolId = null;
  }
  const demoInput = $(`${kind}-picker`)?.querySelector("input");
  if (demoInput && enriched !== undefined) {
    demoInput.value = enriched ? enriched.name : "";
  }
  $(`${kind}-selected`).textContent = enriched
    ? `${enriched.name}${enriched.address ? ` · ${enriched.address}` : ""}`
    : `Choose a verified ${kind === "pickup" ? "pickup place" : "destination"}.`;
  renderPlaceChips(kind);
  updateFareImpactCard();
  renderBookingRouteSvg();
  if (state.demoMode) renderDemoSvgMap();
  if (state.map && enriched && Number.isFinite(enriched.latitude) && Number.isFinite(enriched.longitude)) {
    state.map.panTo({ lat: enriched.latitude, lng: enriched.longitude });
    state.map.setZoom(Math.max(state.map.getZoom() || 0, 13));
  }
}

function installPlacesWidget(kind, center) {
  const host = $(`${kind}-picker`);
  host.replaceChildren();
  const autocomplete = new google.maps.places.PlaceAutocompleteElement();
  autocomplete.includedRegionCodes = ["in"];
  autocomplete.locationBias = {
    center: { lat: center.latitude, lng: center.longitude },
    radius: Math.min((state.config.service_area_radius_km || 85) * 1000, 50000),
  };
  autocomplete.placeholder = kind === "pickup" ? "Search pickup location..." : "Search destination location...";
  autocomplete.setAttribute("aria-label", autocomplete.placeholder);
  autocomplete.addEventListener("gmp-select", async (event) => {
    const selection = event.placePrediction;
    if (!selection) return;
    try {
      const place = selection.toPlace();
      await place.fetchFields({ fields: ["id", "displayName", "formattedAddress", "location"] });
      const location = place.location && place.location.toJSON();
      setSelectedPlace(kind, {
        place_id: place.id,
        name: place.displayName || place.formattedAddress || "Selected place",
        address: place.formattedAddress || "",
        latitude: location ? location.lat : null,
        longitude: location ? location.lng : null,
      });
    } catch (error) {
      message("ride-message", `Could not read that place from Google Maps: ${error.message}`, true);
    }
  });
  host.append(autocomplete);
}

function installDemoPlacePicker(kind) {
  const host = $(`${kind}-picker`);
  host.replaceChildren();
  host.classList.add("demo-place-picker");
  const input = document.createElement("input");
  input.type = "search";
  input.autocomplete = "off";
  input.placeholder = kind === "pickup" ? "Search demo pickup location..." : "Search demo destination location...";
  input.setAttribute("aria-label", input.placeholder);
  if (state.selectedPlaces[kind]) input.value = state.selectedPlaces[kind].name;
  const results = make("div", undefined, "demo-place-results");
  results.hidden = true;
  let searchSequence = 0;
  let activeIndex = -1;
  const kindLabel = (placeKind) => (placeKind === "campus" ? "CAMPUS" : placeKind === "campus_hub" ? "HUB" : "DEST");
  const appendOption = (place) => {
    const option = make("button", undefined, "demo-place-option");
    option.type = "button";
    option.append(make("span", place.name), make("span", kindLabel(place.kind), "place-kind-tag"));
    option.addEventListener("click", () => {
      setSelectedPlace(kind, place);
      results.hidden = true;
      activeIndex = -1;
    });
    results.append(option);
  };
  const syncActive = () => {
    const items = results.querySelectorAll("button.demo-place-option");
    items.forEach((btn, idx) => btn.classList.toggle("active", idx === activeIndex));
    if (activeIndex >= 0 && items[activeIndex]) items[activeIndex].scrollIntoView({ block: "nearest" });
  };
  const renderResults = async () => {
    const sequence = ++searchSequence;
    const query = input.value.trim();
    activeIndex = -1;
    if (query.length < 3) {
      const lowered = query.toLowerCase();
      const localMatches = state.demoPlaces.filter((place) => !lowered || place.name.toLowerCase().includes(lowered));
      results.replaceChildren();
      localMatches.forEach(appendOption);
      if (!localMatches.length) results.append(make("span", "No matching demo places."));
      results.hidden = false;
      return;
    }
    try {
      const response = await api(`/api/places/suggest?q=${encodeURIComponent(query)}`);
      if (sequence !== searchSequence) return;
      results.replaceChildren();
      response.places.forEach((place) => {
        const known = state.demoPlaces.find((p) => p.place_id === place.place_id);
        appendOption({ ...place, kind: known?.kind || "destination" });
      });
      if (!response.places.length) results.append(make("span", "No matching demo places."));
      results.hidden = false;
    } catch (error) {
      results.replaceChildren(make("span", error.message));
      results.hidden = false;
    }
  };
  input.addEventListener("input", () => {
    state.selectedPlaces[kind] = null;
    if (kind === "drop") state.selectedPoolId = null;
    updateFareImpactCard();
    renderBookingRouteSvg();
    window.clearTimeout(input.searchTimer);
    input.searchTimer = window.setTimeout(renderResults, 120);
  });
  input.addEventListener("focus", renderResults);
  input.addEventListener("keydown", (event) => {
    const items = results.querySelectorAll("button.demo-place-option");
    if (event.key === "Escape") {
      results.hidden = true;
      activeIndex = -1;
      return;
    }
    if (!items.length || results.hidden) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      activeIndex = (activeIndex + 1) % items.length;
      syncActive();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      activeIndex = activeIndex <= 0 ? items.length - 1 : activeIndex - 1;
      syncActive();
    } else if (event.key === "Enter" && activeIndex >= 0) {
      event.preventDefault();
      items[activeIndex].click();
    }
  });
  document.addEventListener("click", (event) => {
    if (!host.contains(event.target)) results.hidden = true;
  });
  host.append(input, results);
  renderPlaceChips(kind);
}

function populatePoolPickupSelect() {
  const select = $("pool-join-pickup");
  if (!select || !state.hubs.length) return;
  const previous = select.value;
  select.replaceChildren();
  state.hubs.forEach((place) => {
    const option = make("option", place.name);
    option.value = place.place_id;
    select.append(option);
  });
  if (previous && state.hubs.some((p) => p.place_id === previous)) {
    select.value = previous;
  } else if (state.demoCampus) {
    select.value = state.demoCampus.place_id;
  }
}

async function refreshNetworkStats() {
  try {
    const stats = await api("/api/network/stats");
    if ($("stat-places")) $("stat-places").textContent = stats.places_count;
    if ($("stat-drivers")) $("stat-drivers").textContent = stats.active_drivers;
    if ($("stat-pools")) $("stat-pools").textContent = stats.open_requests_count;
    if ($("stat-saved")) $("stat-saved").textContent = stats.distance_saved_km;
  } catch (_error) {
  }
}

async function loadDemoMap() {
  state.config = await api("/api/map/config");
  const response = await api("/api/places/hubs");
  state.demoPlaces = response.places;
  state.hubs = response.places;
  state.demoCampus = response.places.find((place) => place.kind === "campus");
  if (!state.demoCampus) throw new Error("Demo campus place data is missing. Reset the demo database.");
  $("local-preview-map").hidden = false;
  $("map").setAttribute("aria-label", "Interactive local demonstration network");
  $("map-status").textContent = "LOCAL DEMO · interactive network";
  $("map-legend-copy").textContent = "FICTIONAL DEMO PLACES";
  $("demo-map-caption").textContent = "CLICK ANY STOP TO PRE-FILL /RIDE";
  renderDemoSvgMap(null);
  installDemoPlacePicker("pickup");
  installDemoPlacePicker("drop");
  populatePoolPickupSelect();
  renderSavedPlaces();
}

async function loadGoogleMaps() {
  const config = await api("/api/map/config");
  state.config = config;
  window.initPoolSmartMap = async () => {
    try {
      await google.maps.importLibrary("maps");
      const { AdvancedMarkerElement } = await google.maps.importLibrary("marker");
      const response = await api("/api/places/hubs");
      state.hubs = response.places;
      const campus = state.hubs.find((place) => place.kind === "campus");
      if (!campus) throw new Error("Verified campus location was not returned.");
      state.map = new google.maps.Map($("map"), {
        center: { lat: campus.latitude, lng: campus.longitude },
        zoom: 9,
        mapId: config.map_id,
        colorScheme: "DARK",
        clickableIcons: false,
        fullscreenControl: false,
        streetViewControl: false,
        mapTypeControl: false,
        keyboardShortcuts: true,
        gestureHandling: "greedy",
      });
      const info = new google.maps.InfoWindow();
      state.hubs.forEach((place) => {
        const marker = new AdvancedMarkerElement({
          map: state.map,
          position: { lat: place.latitude, lng: place.longitude },
          title: `${place.name} — verified map place`,
        });
        marker.addListener("click", () => {
          const content = make("div");
          content.append(make("strong", place.name));
          content.append(make("p", place.address));
          info.setContent(content);
          info.open({ map: state.map, anchor: marker });
        });
      });
      installPlacesWidget("pickup", campus);
      installPlacesWidget("drop", campus);
      populatePoolPickupSelect();
      $("map-status").textContent = "Live Google Maps · verified places";
      if (config.route_estimate_factor) $("map-estimate-label").hidden = false;
    } catch (error) {
      $("map-status").textContent = `Live map unavailable: ${error.message}`;
      message("ride-message", `The verified campus map could not be loaded: ${error.message}`, true);
    }
  };
  const script = document.createElement("script");
  script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(config.browser_key)}&callback=initPoolSmartMap&loading=async&v=weekly`;
  script.async = true;
  script.onerror = () => {
    $("map-status").textContent = "Live Google Maps failed to load.";
  };
  document.head.append(script);
}

function closeTripSocket() {
  if (state.tripSocket) state.tripSocket.close();
  state.tripSocket = null;
}

function clearLiveMapData() {
  state.routes.forEach((route) => route.setMap(null));
  state.routes.clear();
  if (state.driverMarker) state.driverMarker.setMap(null);
  state.driverMarker = null;
}

function closeDriverSocket() {
  if (state.driverSocket) state.driverSocket.close();
  state.driverSocket = null;
  if (state.driverTimer) window.clearInterval(state.driverTimer);
  state.driverTimer = null;
}

function clearDriverWatch() {
  if (state.driverWatch !== null && navigator.geolocation) {
    navigator.geolocation.clearWatch(state.driverWatch);
  }
  state.driverWatch = null;
  if (state.driverTimer) window.clearInterval(state.driverTimer);
  state.driverTimer = null;
}

function openTripSocket(tripId) {
  if (state.tripSocket && state.activeTripId === tripId && state.tripSocket.readyState <= WebSocket.OPEN) {
    return;
  }
  closeTripSocket();
  if (state.demoMode || !state.token || !tripId || !("WebSocket" in window)) return;
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  state.tripSocket = new WebSocket(
    `${scheme}//${location.host}/ws/trip/${encodeURIComponent(tripId)}?token=${encodeURIComponent(state.token)}`
  );
  state.tripSocket.addEventListener("open", () => {
    state.tripSocket.send("location");
    const socket = state.tripSocket;
    const ping = window.setInterval(() => {
      if (socket.readyState === WebSocket.OPEN) socket.send("location");
      else window.clearInterval(ping);
    }, 5000);
    socket.addEventListener("close", () => window.clearInterval(ping), { once: true });
  });
  state.tripSocket.addEventListener("message", (event) => {
    try {
      const position = JSON.parse(event.data);
      if (state.map && Number.isFinite(position.latitude) && Number.isFinite(position.longitude)) {
        const point = { lat: position.latitude, lng: position.longitude };
        if (!state.driverMarker) {
          state.driverMarker = new google.maps.Marker({ map: state.map, position: point, title: "Assigned cab" });
        } else {
          state.driverMarker.setPosition(point);
        }
      }
    } catch (_error) {
      message("ride-message", "The live location update could not be read.", true);
    }
  });
}

function updateTripStepper(tripState) {
  const stages = ["matching", "assigned", "enroute", "completed"];
  const activeIndex = tripState === "offered"
    ? 0
    : tripState === "accepted"
      ? 1
      : tripState === "started"
        ? 2
        : 3;
  document.querySelectorAll("#trip-stepper li").forEach((node) => {
    const idx = stages.indexOf(node.dataset.stage);
    node.classList.toggle("done", idx < activeIndex || (tripState === "completed" && idx === 3));
    node.classList.toggle("active", idx === activeIndex && tripState !== "completed");
  });
}

function renderSplitFareCard(trip) {
  const card = $("split-fare-breakdown");
  if (!card) return;
  if (!trip.riders || !trip.riders.length) {
    card.hidden = true;
    return;
  }
  card.replaceChildren();
  card.append(make("p", "TRANSPARENT FARE SPLIT", "eyebrow"));
  trip.riders.forEach((rider) => {
    const row = make("div", undefined, "split-fare-row");
    const solo = Number(rider.solo_fare_inr || 0);
    const shared = Number(rider.fare_inr || 0);
    const saved = Math.max(0, solo - shared);
    row.append(
      make("span", `${rider.first_name} (${rider.pickup_area} -> ${rider.drop_area})`),
      make("span", `Solo INR ${solo.toFixed(2)} -> Split INR ${shared.toFixed(2)}${saved > 0 ? ` (Saved INR ${saved.toFixed(2)})` : ""}`),
    );
    card.append(row);
  });
  card.hidden = false;
}

async function showTrip(tripId, rideId) {
  const trip = await api(`/api/trips/${encodeURIComponent(tripId)}`);
  state.activeTripId = tripId;
  state.activeRideId = rideId;
  $("trip-summary").textContent =
    `${trip.driver.first_name} · ${trip.driver.vehicle_model} · ${trip.driver.vehicle_number}`;
  updateTripStepper(trip.state);
  renderSplitFareCard(trip);
  const meta = $("trip-meta");
  if (meta) {
    meta.replaceChildren();
    const km = (Number(trip.distance_m || 0) / 1000).toFixed(1);
    const mins = Math.max(1, Math.round(Number(trip.duration_s || 0) / 60));
    const fare = trip.fare_inr !== null && trip.fare_inr !== undefined ? `INR ${Number(trip.fare_inr).toFixed(2)}` : "Pending";
    meta.append(
      make("span", `Route: ${km} km · ~${mins} min`),
      make("span", `Your share: ${fare}`),
      make("span", trip.riders?.length > 1 ? `Shared cab (${trip.riders.length} riders)` : "Solo cab trip"),
    );
  }
  setHidden("trip-live", false);
  setHidden("active-trip-actions", !["offered", "accepted", "started"].includes(trip.state));
  setHidden("simulate-step", !(state.demoMode && ["offered", "accepted", "started"].includes(trip.state)));
  setHidden("trip-estimate", !trip.estimated);
  if (state.demoMode) {
    $("trip-estimate").textContent = "Simulated route and times · no live road routing or GPS data.";
    renderDemoSvgMap(trip);
    renderBookingRouteSvg(trip);
  }
  setHidden("map-estimate-label", !trip.estimated);
  $("trip-stops").replaceChildren();
  trip.stops.forEach((stop) => {
    const item = make("div", undefined, "stop-item");
    item.append(make("strong", `${stop.sequence + 1} · ${stop.action.toUpperCase()}${stop.completed ? " · DONE" : ""}`));
    item.append(make("span", `${stop.place} · ${displayTime(stop.eta_at)}`));
    $("trip-stops").append(item);
  });
  $("trip-start-code").textContent = "";
  if (trip.state === "accepted") {
    const code = await api(`/api/trips/${encodeURIComponent(tripId)}/start-code`);
    $("trip-start-code").textContent = `START CODE ${code.code}`;
  }
  if (["accepted", "started"].includes(trip.state)) {
    const contact = await api(`/api/trips/${encodeURIComponent(tripId)}/driver-contact`);
    if (contact.phone) {
      $("driver-call").href = `tel:${contact.phone}`;
      $("driver-call").textContent = `CALL ${contact.name}`;
      setHidden("driver-call", false);
    } else {
      setHidden("driver-call", true);
    }
  } else {
    setHidden("driver-call", true);
  }
  if (trip.polyline && state.map && window.google?.maps) {
    try {
      await google.maps.importLibrary("geometry");
      const path = google.maps.geometry.encoding.decodePath(trip.polyline);
      if (!state.routes.has(tripId)) {
        const route = new google.maps.Polyline({
          path: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? path : path.slice(0, 1),
          geodesic: true,
          strokeColor: "#c1a477",
          strokeOpacity: 0.9,
          strokeWeight: 3,
          map: state.map,
        });
        state.routes.set(tripId, route);
        if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches && path.length > 1) {
          let pointIndex = 1;
          const drawNext = () => {
            pointIndex += 1;
            route.setPath(path.slice(0, pointIndex));
            if (pointIndex < path.length) window.setTimeout(drawNext, 24);
          };
          window.setTimeout(drawNext, 24);
        }
      }
      const bounds = new google.maps.LatLngBounds();
      path.forEach((point) => bounds.extend(point));
      state.map.fitBounds(bounds, 60);
    } catch (_error) {
      $("map-status").textContent = "Trip details loaded; route drawing is unavailable.";
    }
  }
  if (["accepted", "started"].includes(trip.state)) openTripSocket(tripId);
  else closeTripSocket();
  return trip;
}

function buildRideTable(rides, includeActions = true) {
  const table = make("table", undefined, "data-table");
  const head = make("thead");
  const headerRow = make("tr");
  const headers = ["STATUS", "PICKUP", "DESTINATION", "DEPARTURE", "FARE · INR"];
  if (includeActions) headers.push("ACTIONS");
  headers.forEach((label) => headerRow.append(make("th", label)));
  head.append(headerRow);
  const body = make("tbody");
  rides.forEach((ride) => {
    const row = make("tr");
    const statusCell = make("td");
    statusCell.append(make("span", ride.status.replaceAll("_", " ").toUpperCase(), `status-pill status-${ride.status}`));
    row.append(statusCell);
    [
      ride.pickup.name,
      ride.drop.name,
      displayTime(ride.promised_pickup_at || ride.scheduled_at),
      ride.fare_inr === null
        ? "Pending"
        : `${Number(ride.fare_inr).toFixed(2)}${ride.savings_percent ? ` (-${ride.savings_percent}%)` : ""}`,
    ].forEach((value) => row.append(make("td", value)));
    if (includeActions) {
      const actions = make("td");
      if (ride.trip_id && ["offered", "confirmed", "in_progress"].includes(ride.status)) {
        const track = make("button", "TRACK", "text-button");
        track.type = "button";
        track.addEventListener("click", async () => {
          try {
            await showTrip(ride.trip_id, ride.id);
            $("trip-live").scrollIntoView({ behavior: "smooth", block: "nearest" });
          } catch (error) {
            message("ride-message", error.message, true);
          }
        });
        actions.append(track);
      }
      if (ride.status === "completed") {
        const label = ride.payment?.state === "paid" ? "VIEW RECEIPT" : "PAY / RECEIPT";
        const pay = make("button", label, "text-button");
        pay.type = "button";
        pay.addEventListener("click", () => {
          state.selectedRideId = ride.id;
          $("payment-qr").hidden = true;
          setHidden("payment-panel", false);
          setHidden("demo-payment-note", !state.demoMode);
          $("download-receipt").dataset.rideId = ride.id;
          $("download-receipt").href = `/api/rides/${encodeURIComponent(ride.id)}/receipt`;
          setHidden("download-receipt", false);
          const status = ride.payment
            ? `${ride.payment.method.toUpperCase()} · ${ride.payment.state.replaceAll("_", " ").toUpperCase()}`
            : "No payment recorded";
          message("payment-message", `Selected fare: INR ${Number(ride.fare_inr || 0).toFixed(2)} · ${status}.`);
          setHidden("pay-ride", Boolean(ride.payment && ride.payment.state !== "failed"));
          $("payment-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
        });
        actions.append(pay);
      }
      if (["pending", "offered"].includes(ride.status)) {
        const cancel = make("button", "CANCEL", "text-button danger-text");
        cancel.type = "button";
        cancel.addEventListener("click", async () => {
          try {
            await api(`/api/rides/${encodeURIComponent(ride.id)}`, { method: "DELETE" });
            await loadRides();
            await loadPools();
            notify("Ride cancelled", "Your ride request was cancelled.");
          } catch (error) {
            message("ride-message", error.message, true);
          }
        });
        actions.append(cancel);
      }
      row.append(actions);
    }
    body.append(row);
  });
  table.append(head, body);
  const scroll = make("div", undefined, "table-scroll");
  scroll.append(table);
  return scroll;
}

async function renderRides(rides) {
  state.rides = rides;
  rides.forEach((ride) => {
    const prev = state.lastRideStatuses.get(ride.id);
    if (prev && prev !== ride.status) {
      notify("Ride status updated", `${ride.pickup.name} -> ${ride.drop.name} is now ${ride.status.replaceAll("_", " ").toUpperCase()}.`);
    }
    state.lastRideStatuses.set(ride.id, ride.status);
  });

  const target = $("ride-list");
  target.replaceChildren();
  const historyTarget = $("account-trip-history");
  if (historyTarget) {
    historyTarget.replaceChildren();
    if (!rides.length) historyTarget.append(make("p", "No trip history yet.", "empty-note"));
    else historyTarget.append(buildRideTable(rides, false));
  }

  const active = rides.find((ride) => ["offered", "confirmed", "in_progress"].includes(ride.status) && ride.trip_id);
  if (!rides.length) {
    target.append(make("p", "No ride requests yet.", "empty-note"));
    setHidden("payment-panel", true);
    setHidden("active-trip-actions", true);
    setHidden("trip-live", true);
    return;
  }
  target.append(buildRideTable(rides, true));

  if (state.selectedRideId) {
    const selectedRide = rides.find((ride) => ride.id === state.selectedRideId);
    if (selectedRide && selectedRide.status === "completed") {
      const status = selectedRide.payment
        ? `${selectedRide.payment.method.toUpperCase()} · ${selectedRide.payment.state.replaceAll("_", " ").toUpperCase()}`
        : "No payment recorded";
      message("payment-message", `Selected fare: INR ${Number(selectedRide.fare_inr || 0).toFixed(2)} · ${status}.`);
      setHidden("pay-ride", Boolean(selectedRide.payment && selectedRide.payment.state !== "failed"));
    }
  }
  if (active) {
    try {
      await showTrip(active.trip_id, active.id);
    } catch (error) {
      message("ride-message", `Trip details unavailable: ${error.message}`, true);
    }
  } else {
    closeTripSocket();
    clearLiveMapData();
    state.activeTripId = null;
    state.activeRideId = null;
    if (state.demoMode) renderDemoSvgMap(null);
    renderBookingRouteSvg(null);
    setHidden("active-trip-actions", true);
    setHidden("trip-live", true);
  }
  const completed = rides.some((ride) => ride.status === "completed");
  setHidden("payment-panel", !completed && !state.selectedRideId);
}

async function loadRides() {
  if (!state.token) return;
  const response = await api("/api/rides");
  await renderRides(response.rides);
}

function renderFilteredPools() {
  const list = $("pool-list");
  if (!list) return;
  list.replaceChildren();
  const query = ($("pool-filter-query")?.value || "").trim().toLowerCase();
  const safetyFilter = $("pool-filter-safety")?.value || "all";
  const minSeats = Number($("pool-filter-seats")?.value || 1);

  const filtered = state.openPools.filter((pool) => {
    const textMatch = !query
      || pool.destination.toLowerCase().includes(query)
      || (pool.pickup_names || []).some((name) => name.toLowerCase().includes(query));
    const safetyMatch = safetyFilter === "all" || pool.safety_badge === safetyFilter;
    const seatMatch = pool.seats_left >= minSeats;
    return textMatch && safetyMatch && seatMatch;
  });

  if (!filtered.length) {
    list.append(make("p", state.openPools.length ? "No pools match your current filters." : "No compatible pending departures are visible right now.", "empty-note"));
    return;
  }

  filtered.forEach((pool) => {
    const card = make("article", undefined, "pool-card");
    const top = make("div", undefined, "pool-card-top");
    top.append(make("h3", pool.destination));
    const badgeLabel = pool.safety_badge === "women_only"
      ? "WOMEN-ONLY"
      : pool.safety_badge === "same_gender"
        ? "SAME-GENDER"
        : "OPEN POOL";
    const badgeClass = pool.safety_badge === "open" ? "status-pill status-pending" : "status-pill status-offered";
    top.append(make("span", badgeLabel, badgeClass));

    const meta = make("div", undefined, "pool-meta-list");
    meta.append(
      make("span", `Pickups: ${(pool.pickup_names || []).join(", ") || "Campus"}`),
      make("span", `${pool.rider_count} rider${pool.rider_count === 1 ? "" : "s"} joined · ${pool.seats_left} seat${pool.seats_left === 1 ? "" : "s"} free`),
      make("span", `Max detour allowance: +${pool.estimated_detour_percent ?? 30}% · ${pool.fare_notice}`),
    );

    const fareRow = make("div", undefined, "pool-fare-row");
    const soloTxt = pool.estimated_solo_fare_inr ? `Solo INR ${Number(pool.estimated_solo_fare_inr).toFixed(0)}` : "";
    const poolTxt = pool.estimated_pooled_fare_inr ? `Est. INR ${Number(pool.estimated_pooled_fare_inr).toFixed(0)}` : "Live split";
    fareRow.append(make("span", soloTxt), make("strong", poolTxt));

    const joinBtn = make("button", "JOIN POOL →", "gold-button");
    joinBtn.type = "button";
    joinBtn.addEventListener("click", async () => {
      try {
        const pickupId = $("pool-join-pickup")?.value || state.demoCampus?.place_id || state.hubs[0]?.place_id;
        if (!pickupId) throw new Error("Choose a pickup place first.");
        if (pickupId === pool.drop_place_id) {
          throw new Error("Pickup and pool destination cannot be the same place.");
        }
        const ride = await api(`/api/pools/${encodeURIComponent(pool.pool_id)}/join`, {
          method: "POST",
          body: {
            pickup_place_id: pickupId,
            drop_place_id: pool.drop_place_id,
            seats: 1,
            luggage_count: 0,
            max_wait_minutes: 15,
            max_detour: 1.3,
            women_only: pool.safety_badge === "women_only",
            same_gender: pool.safety_badge === "same_gender",
          },
        });
        notify("Joined pool", `Request ${ride.status.toUpperCase()} for ${pool.destination}.`);
        await loadRides();
        await loadPools();
        navigateTo("/ride");
      } catch (error) {
        message("pools-message", error.message, true);
        notify("Could not join pool", error.message, true);
      }
    });

    card.append(top, meta, fareRow, joinBtn);
    list.append(card);
  });
}

async function loadPools() {
  const badge = $("nav-pool-badge");
  if (!state.token) {
    if (badge) badge.hidden = true;
    return;
  }
  try {
    const { pools } = await api("/api/pools/open");
    state.openPools = pools;
    if (badge) {
      badge.textContent = String(pools.length);
      badge.hidden = pools.length === 0;
    }
    renderFilteredPools();
  } catch (error) {
    $("pool-list").replaceChildren(make("p", error.message, "empty-note"));
  }
}

function renderSavedPlaces() {
  const host = $("saved-places-list");
  if (!host || !state.hubs.length) return;
  host.replaceChildren();
  const places = state.savedPlaceIds
    .map((id) => state.hubs.find((p) => p.place_id === id))
    .filter(Boolean);
  places.forEach((place) => {
    const chip = make("button", `${place.name} →`, "place-chip");
    chip.type = "button";
    chip.addEventListener("click", () => {
      if (place.kind === "campus") {
        setSelectedPlace("pickup", place);
      } else {
        if (!state.selectedPlaces.pickup && state.demoCampus) setSelectedPlace("pickup", state.demoCampus);
        setSelectedPlace("drop", place);
      }
      navigateTo("/ride");
    });
    host.append(chip);
  });
}

function readProfileForm() {
  const name = $("emergency-name").value.trim();
  const email = $("emergency-email").value.trim();
  const phone = $("emergency-phone").value.trim();
  const emergencyContacts = name && (email || phone) ? [{ name, email, phone }] : [];
  return {
    phone: $("profile-phone").value.trim(),
    gender: $("profile-gender").value,
    women_only: $("profile-women-only").checked,
    same_gender: $("profile-same-gender").checked,
    accessibility_need: $("profile-accessibility").value.trim(),
    emergency_contacts: emergencyContacts,
  };
}

async function loadProfile() {
  state.selectedRideId = null;
  state.activeTripId = null;
  state.activeRideId = null;
  state.activeDriverTripId = null;
  state.selectedPoolId = null;
  $("payment-qr").hidden = true;
  setHidden("download-receipt", true);
  message("ride-message");
  message("driver-message");
  message("payment-message");

  const profile = await api("/api/me");
  state.user = profile;
  updateModeIndicator();
  updateAuthGates();

  $("account-name").textContent = `${profile.name} · ${profile.role.toUpperCase()}`;
  if ($("wallet-balance")) {
    $("wallet-balance").textContent = `INR ${Number(profile.wallet_balance_inr || 0).toFixed(2)}`;
  }
  $("profile-phone").value = profile.phone || "";
  $("profile-gender").value = profile.gender || "unspecified";
  $("gender-verification-note").textContent = profile.gender_verified
    ? "Gender verification is on file. You may request compatible pools."
    : "Gender-matched rides require administrator verification against university records.";
  $("profile-women-only").checked = Boolean(profile.women_only);
  $("profile-same-gender").checked = Boolean(profile.same_gender);
  $("women-only").checked = Boolean(profile.women_only && profile.gender_verified && profile.gender === "female");
  $("same-gender").checked = Boolean(profile.same_gender && profile.gender_verified && profile.gender !== "unspecified");
  $("profile-accessibility").value = profile.accessibility_need || "";
  if (!$("driver-name").value.trim()) $("driver-name").value = profile.name || "";
  if (!$("driver-phone").value.trim()) $("driver-phone").value = profile.phone || "";
  const contact = (profile.emergency_contacts || [])[0] || {};
  $("emergency-name").value = contact.name || "";
  $("emergency-email").value = contact.email || "";
  $("emergency-phone").value = contact.phone || "";

  document.querySelectorAll("#demo-role-grid .role-card").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.demoEmail === profile.email);
  });

  setHidden("auth-panel", false);
  document.querySelectorAll("#auth-panel > form").forEach((form) => { form.hidden = true; });
  setHidden("signed-in", false);
  setHidden("wallet-card", !state.demoMode);
  setHidden("saved-places-card", !state.hubs.length);
  setHidden("profile-panel", false);
  setHidden("account-history-wrap", false);
  setHidden("admin", profile.role !== "admin");
  setHidden("driver-form", profile.role === "admin");

  renderSavedPlaces();
  if (profile.role === "admin") await loadAdmin();
  const results = await Promise.allSettled([loadRides(), loadPools(), loadDriver()]);
  results.forEach((result) => {
    if (result.status === "rejected") message("auth-message", result.reason.message, true);
  });
}

function clearAuth() {
  state.token = "";
  state.user = null;
  state.rides = [];
  state.openPools = [];
  state.lastRideStatuses.clear();
  sessionStorage.removeItem("poolsmart-token");
  closeTripSocket();
  clearLiveMapData();
  closeDriverSocket();
  clearDriverWatch();
  if (state.offerTimer) window.clearInterval(state.offerTimer);
  state.offerTimer = null;
  state.driverOnline = false;
  state.lastDriverPosition = null;
  state.selectedPoolId = null;
  state.selectedRideId = null;
  state.activeTripId = null;
  state.activeRideId = null;
  state.activeDriverTripId = null;
  state.selectedPlaces = { pickup: null, drop: null };
  updateModeIndicator();
  updateAuthGates();
  if ($("nav-pool-badge")) $("nav-pool-badge").hidden = true;
  if ($("nav-driver-dot")) {
    $("nav-driver-dot").hidden = true;
    $("nav-driver-dot").classList.remove("online");
  }
  if (state.demoMode && state.demoPlaces.length) {
    installDemoPlacePicker("pickup");
    installDemoPlacePicker("drop");
  } else if (state.hubs.length) {
    const campus = state.hubs.find((place) => place.kind === "campus");
    if (campus) {
      installPlacesWidget("pickup", campus);
      installPlacesWidget("drop", campus);
    }
  }
  $("pickup-selected").textContent = "Choose a verified pickup place.";
  $("drop-selected").textContent = "Choose a verified destination.";
  $("driver-active-trip").replaceChildren();
  $("driver-offers").replaceChildren();
  $("driver-earnings-grid").replaceChildren();
  $("driver-name").value = "";
  $("driver-phone").value = "";
  $("admin-metrics").replaceChildren();
  $("admin-drivers").replaceChildren();
  $("admin-approved-drivers")?.replaceChildren();
  $("admin-gender-verifications").replaceChildren();
  $("admin-payments").replaceChildren();
  $("admin-incidents")?.replaceChildren();
  $("algorithm-lab").replaceChildren();
  $("payment-qr").hidden = true;
  setHidden("download-receipt", true);
  setHidden("payment-panel", true);
  setHidden("signed-in", true);
  setHidden("wallet-card", true);
  setHidden("saved-places-card", true);
  setHidden("profile-panel", true);
  setHidden("account-history-wrap", true);
  setHidden("driver-summary", true);
  setHidden("driver-tools", true);
  setHidden("driver-form", true);
  setHidden("admin", true);
  setHidden("trip-live", true);
  setHidden("active-trip-actions", true);
  document.querySelectorAll("#demo-role-grid .role-card").forEach((btn) => btn.classList.remove("active"));
  if (state.demoMode) {
    $("auth-panel").querySelectorAll("form").forEach((form) => { form.hidden = true; });
    setHidden("demo-login-form", false);
    renderDemoSvgMap(null);
  } else {
    $("auth-panel").querySelectorAll("form").forEach((form) => { form.hidden = false; });
    setHidden("otp-verify-form", true);
  }
  updateFareImpactCard();
  renderBookingRouteSvg(null);
  $("ride-list").replaceChildren(make("p", "Sign in to see your ride requests.", "empty-note"));
  $("pool-list").replaceChildren(make("p", "Sign in to view current departures.", "empty-note"));
  message("ride-message");
  message("payment-message");
  message("driver-message");
  message("auth-message", "Signed out.");
}

function renderOffers(offers, hasActiveTrip = false) {
  if (state.offerTimer) {
    window.clearInterval(state.offerTimer);
    state.offerTimer = null;
  }
  const target = $("driver-offers");
  target.replaceChildren();
  if (!offers.length) {
    target.append(make("p", hasActiveTrip ? "Active trip in progress." : "No offers at the moment. Keep GPS sharing on to receive requests.", "empty-note"));
    return;
  }
  const countdownNodes = [];
  offers.forEach((offer) => {
    const row = make("div", undefined, "offer-row");
    const details = make("div");
    details.append(make("p", `INR ${Number(offer.vehicle_earnings_inr).toFixed(2)} · ${offer.rider_count} rider${offer.rider_count === 1 ? "" : "s"}`));
    const countdown = make("span", "");
    countdownNodes.push({ node: countdown, expiresAt: new Date(offer.expires_at).getTime() });
    details.append(
      make("span", `${offer.stops.map((stop) => `${stop.action}: ${stop.place}`).join(" / ")}${offer.estimated ? " · estimated timing" : ""}`),
      make("br"),
      countdown,
    );
    row.append(details);
    const accept = make("button", "ACCEPT", "outline-button");
    const decline = make("button", "DECLINE", "text-button");
    accept.type = decline.type = "button";
    accept.addEventListener("click", () => respondToOffer(offer.offer_id, true));
    decline.addEventListener("click", () => respondToOffer(offer.offer_id, false));
    row.append(accept, decline);
    target.append(row);
  });
  const tick = () => {
    let expiredAny = false;
    const now = Date.now();
    countdownNodes.forEach(({ node, expiresAt }) => {
      const secs = Math.max(0, Math.ceil((expiresAt - now) / 1000));
      node.textContent = secs > 0 ? `Offer expires in ${secs}s` : "Offer expired — refreshing…";
      if (secs === 0) expiredAny = true;
    });
    if (expiredAny) {
      window.clearInterval(state.offerTimer);
      state.offerTimer = null;
      loadDriver().catch(() => {});
    }
  };
  tick();
  state.offerTimer = window.setInterval(tick, 1000);
}

async function loadDriver() {
  const dot = $("nav-driver-dot");
  if (!state.token || state.user?.role === "admin") {
    setHidden("driver-tools", true);
    if (dot) dot.hidden = true;
    return;
  }
  try {
    const response = await api("/api/driver/offers");
    if (response.application_status === "not_applied") {
      setHidden("driver-tools", true);
      setHidden("driver-form", false);
      setHidden("driver-summary", true);
      if (dot) dot.hidden = true;
      return;
    }
    const mine = await api("/api/drivers/status");
    state.driverOnline = mine.online;
    if (dot) {
      dot.hidden = false;
      dot.classList.toggle("online", Boolean(mine.online));
    }
    if (state.demoMode && state.demoCampus) {
      state.lastDriverPosition = {
        latitude: state.demoCampus.latitude,
        longitude: state.demoCampus.longitude,
        speed_m_s: 0,
      };
    }
    setHidden("driver-tools", false);
    setHidden("driver-form", mine.approval_status !== "rejected");
    const earningsGrid = $("driver-earnings-grid");
    if (earningsGrid) {
      earningsGrid.replaceChildren();
      [
        ["SETTLED EARNINGS", `INR ${Number(mine.earnings_total_inr || 0).toFixed(2)}`],
        ["PENDING CASH", `INR ${Number(mine.pending_cash_inr || 0).toFixed(2)}`],
        ["COMPLETED TRIPS", mine.completed_trips_count ?? 0],
        ["SEAT CAPACITY", mine.seat_capacity ?? 4],
      ].forEach(([label, value]) => {
        const card = make("div", undefined, "admin-metric");
        card.append(make("span", label), make("strong", value));
        earningsGrid.append(card);
      });
    }
    if (mine.vehicle_model) {
      $("driver-summary").textContent = `${mine.vehicle_model} · ${mine.vehicle_number} · ${mine.seat_capacity} seats · ${mine.approval_status.toUpperCase()}`;
      setHidden("driver-summary", false);
    }
    const tripToLoad = mine.active_trip_id || mine.unsettled_trip_id;
    renderOffers(response.offers, Boolean(mine.active_trip_id));
    $("driver-online").disabled = mine.approval_status !== "approved";
    if (mine.approval_status === "rejected") {
      $("driver-online").textContent = "APPLICATION NOT APPROVED";
      $("driver-state").textContent = "Your previous application was not approved. Update and resubmit below.";
    } else if (mine.approval_status !== "approved") {
      $("driver-online").textContent = "APPLICATION UNDER REVIEW";
      $("driver-state").textContent = "An administrator must approve your driver application.";
    } else {
      $("driver-online").textContent = mine.online ? "GO OFFLINE" : "GO ONLINE";
      $("driver-state").textContent = state.demoMode
        ? mine.online ? "Simulated campus location is active; device GPS is not used." : "Offline; local demo cab is waiting at the demo campus."
        : mine.online ? "GPS sharing is active while you are on shift." : "GPS sharing is off.";
    }
    if (tripToLoad) {
      state.activeDriverTripId = tripToLoad;
      await loadDriverTrip();
    } else {
      state.activeDriverTripId = null;
      $("driver-active-trip").replaceChildren();
    }
  } catch (error) {
    if (error.message.includes("driver onboarding")) {
      setHidden("driver-tools", true);
      setHidden("driver-form", false);
      return;
    }
    message("driver-message", error.message, true);
  }
}

async function respondToOffer(offerId, accept) {
  try {
    const response = await api(`/api/driver/offers/${encodeURIComponent(offerId)}`, {
      method: "POST",
      body: { accept },
    });
    if (response.accepted) {
      state.activeDriverTripId = response.trip_id;
      message("driver-message", "Trip accepted. Confirm the rider's start code before moving.");
      notify("Trip accepted", "Start code verification is ready on the Driver Console.");
      await loadDriverTrip();
    } else {
      message("driver-message", "Offer declined.");
    }
    await loadDriver();
  } catch (error) {
    message("driver-message", error.message, true);
  }
}

async function loadDriverTrip() {
  if (!state.activeDriverTripId) return;
  const trip = await api(`/api/driver/trips/${encodeURIComponent(state.activeDriverTripId)}`);
  const target = $("driver-active-trip");
  target.replaceChildren();
  const km = (Number(trip.distance_m || 0) / 1000).toFixed(1);
  const mins = Math.max(1, Math.round(Number(trip.duration_s || 0) / 60));
  target.append(make("h3", `Active trip · ${trip.state.replaceAll("_", " ").toUpperCase()}`));
  target.append(make("p", `${km} km · ~${mins} min · Net driver earnings: INR ${Number(trip.vehicle_earnings_inr || 0).toFixed(2)}`, "selection-note"));
  trip.passengers.forEach((rider) => {
    const passenger = make("div", undefined, "offer-row");
    const paymentStatus = rider.payment_state
      ? `${rider.payment_method?.toUpperCase()} · ${rider.payment_state.toUpperCase()}`
      : "No payment method selected";
    passenger.append(make("p", `${rider.first_name} · ${rider.drop_area} · INR ${Number(rider.fare_inr || 0).toFixed(2)} · ${paymentStatus}`));
    if (rider.ride_status === "completed" && (!rider.payment_state || rider.payment_state === "due")) {
      const collected = make("button", "MARK CASH COLLECTED", "outline-button");
      collected.type = "button";
      collected.addEventListener("click", async () => {
        try {
          await api(`/api/driver/trips/${encodeURIComponent(trip.id)}/rides/${encodeURIComponent(rider.ride_id)}/cash-collected`, {
            method: "POST",
          });
          notify("Cash collected", `Marked INR ${Number(rider.fare_inr || 0).toFixed(2)} collected.`);
          await loadDriver();
        } catch (error) {
          message("driver-message", error.message, true);
        }
      });
      passenger.append(collected);
    }
    target.append(passenger);
  });
  const stops = make("div", undefined, "stop-list");
  trip.stops.forEach((stop) => {
    const node = make("div", undefined, "stop-item");
    node.append(make("strong", `${stop.sequence + 1} · ${stop.action.toUpperCase()}${stop.completed ? " · DONE" : ""}`));
    node.append(make("span", stop.place));
    if (!stop.completed && trip.state === "started") {
      const button = make("button", "COMPLETE STOP", "text-button");
      button.type = "button";
      button.addEventListener("click", async () => {
        try {
          const result = await api(`/api/driver/trips/${encodeURIComponent(trip.id)}/stops/${stop.id}/complete`, {
            method: "POST",
            body: { stop_id: stop.id },
          });
          notify("Stop completed", result.trip_completed ? "All stops complete!" : `Completed ${stop.place}.`);
          await loadDriver();
        } catch (error) {
          message("driver-message", error.message, true);
        }
      });
      node.append(button);
    }
    stops.append(node);
  });
  target.append(stops);
  if (trip.state === "accepted") {
    const form = make("form", undefined, "line-form");
    const label = make("label", "FOUR-DIGIT RIDER START CODE");
    const input = document.createElement("input");
    input.inputMode = "numeric";
    input.pattern = "[0-9]{4}";
    input.maxLength = 4;
    input.required = true;
    label.append(input);
    const button = make("button", "START TRIP", "gold-button");
    button.type = "submit";
    form.append(label, button);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await api(`/api/driver/trips/${encodeURIComponent(trip.id)}/start`, {
          method: "POST",
          body: { code: input.value },
        });
        await loadDriverTrip();
        message("driver-message", "Trip started. Complete each stop in order.");
        notify("Trip started", "En route to pickup and drop-off stops.");
      } catch (error) {
        message("driver-message", error.message, true);
      }
    });
    target.append(form);
  }
  if (trip.state === "completed") {
    const hasPendingCash = trip.passengers.some(
      (rider) => rider.ride_status === "completed" && (!rider.payment_state || rider.payment_state === "due")
    );
    target.append(make("p", hasPendingCash
      ? "All stops complete. Collect cash or wait for rider payment selection."
      : "All stops complete. Trip closed."));
    if (!hasPendingCash) state.activeDriverTripId = null;
  }
}

async function enableDriverGps() {
  if (!navigator.geolocation) throw new Error("This browser does not provide GPS.");
  closeDriverSocket();
  state.driverWatch = navigator.geolocation.watchPosition(
    (position) => {
      state.lastDriverPosition = {
        latitude: position.coords.latitude,
        longitude: position.coords.longitude,
        speed_m_s: position.coords.speed === null ? null : Math.max(0, position.coords.speed),
      };
    },
    (error) => message("driver-state", `GPS error: ${error.message}`, true),
    { enableHighAccuracy: true, maximumAge: 3000, timeout: 15000 },
  );
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  state.driverSocket = new WebSocket(`${scheme}//${location.host}/ws/driver?token=${encodeURIComponent(state.token)}`);
  state.driverSocket.addEventListener("open", () => {
    state.driverTimer = window.setInterval(async () => {
      if (!state.lastDriverPosition) return;
      const packet = { ...state.lastDriverPosition, online: true };
      if (state.driverSocket?.readyState === WebSocket.OPEN) state.driverSocket.send(JSON.stringify(packet));
      try {
        await api("/api/drivers/online", { method: "POST", body: packet });
      } catch (error) {
        message("driver-state", error.message, true);
      }
    }, 10000);
  });
}

async function setDriverAvailability(online) {
  if (!online) {
    clearDriverWatch();
    closeDriverSocket();
    if (state.lastDriverPosition) {
      await api("/api/drivers/online", {
        method: "POST",
        body: { ...state.lastDriverPosition, online: false },
      });
    }
    state.driverOnline = false;
    if ($("nav-driver-dot")) $("nav-driver-dot").classList.remove("online");
    $("driver-online").textContent = "GO ONLINE";
    $("driver-state").textContent = state.demoMode ? "Offline; no location is being shared." : "GPS sharing is off.";
    return;
  }
  if (state.demoMode && state.demoCampus) {
    state.lastDriverPosition = {
      latitude: state.demoCampus.latitude,
      longitude: state.demoCampus.longitude,
      speed_m_s: 0,
    };
  }
  if (!state.lastDriverPosition) {
    await new Promise((resolve, reject) => {
      if (!navigator.geolocation) {
        reject(new Error("This browser does not provide GPS."));
        return;
      }
      navigator.geolocation.getCurrentPosition(
        (position) => {
          state.lastDriverPosition = {
            latitude: position.coords.latitude,
            longitude: position.coords.longitude,
            speed_m_s: position.coords.speed,
          };
          resolve();
        },
        (error) => reject(new Error(`GPS permission is required: ${error.message}`)),
        { enableHighAccuracy: true, timeout: 15000 },
      );
    });
  }
  await api("/api/drivers/online", {
    method: "POST",
    body: { ...state.lastDriverPosition, online: true },
  });
  state.driverOnline = true;
  if ($("nav-driver-dot")) {
    $("nav-driver-dot").hidden = false;
    $("nav-driver-dot").classList.add("online");
  }
  $("driver-online").textContent = "GO OFFLINE";
  $("driver-state").textContent = state.demoMode
    ? "Simulated campus location is active; device GPS is not used."
    : "GPS sharing is active while you are on shift.";
  if (!state.demoMode) await enableDriverGps();
  else await loadDriver();
}

async function loadAdmin() {
  if (state.user?.role !== "admin") return;
  try {
    const [analytics, runs, drivers, approvedDrivers, verifications, payments, incidents] = await Promise.all([
      api("/api/admin/analytics"),
      api("/api/admin/algorithm-lab"),
      api("/api/admin/drivers?status_filter=pending"),
      api("/api/admin/drivers?status_filter=approved"),
      api("/api/admin/gender-verifications"),
      api("/api/admin/payments"),
      api("/api/admin/incidents"),
    ]);
    const metrics = $("admin-metrics");
    metrics.replaceChildren();
    [
      ["ACTIVE DRIVERS", analytics.active_drivers],
      ["PENDING REQUESTS", analytics.rides_by_state.pending],
      ["COMPLETED KM", analytics.completed_distance_km],
      ["POOL DISTANCE SAVED KM", analytics.distance_saved_km ?? 0],
      ["SAFETY EVENTS · 30 DAYS", analytics.safety_events_30_days],
      ["COMPLETED REQUESTS", analytics.rides_by_state.completed],
      ["RIDERS IN COMPLETED POOLS", `${analytics.pool_rate_percent}%`],
    ].forEach(([label, value]) => {
      const metric = make("div", undefined, "admin-metric");
      metric.append(make("span", label), make("strong", value));
      metrics.append(metric);
    });
    const driverList = $("admin-drivers");
    driverList.replaceChildren();
    if (!drivers.drivers.length) driverList.append(make("p", "No driver applications await review.", "empty-note"));
    drivers.drivers.forEach((driver) => {
      const row = make("div", undefined, "offer-row");
      const details = make("div");
      details.append(
        make("p", `${driver.name} · ${driver.vehicle_model} · ${driver.vehicle_number}`),
        make("span", `${driver.email} · ${driver.phone} · licence ${driver.licence_number} · insurance ${displayTime(driver.insurance_expiry)}`),
      );
      const approve = make("button", "APPROVE", "outline-button");
      const reject = make("button", "REJECT", "text-button danger-text");
      approve.type = reject.type = "button";
      approve.addEventListener("click", () => reviewDriver(driver.id, true));
      reject.addEventListener("click", () => reviewDriver(driver.id, false));
      row.append(details, approve, reject);
      driverList.append(row);
    });
    const approvedList = $("admin-approved-drivers");
    approvedList.replaceChildren();
    if (!approvedDrivers.drivers.length) approvedList.append(make("p", "No approved drivers in fleet.", "empty-note"));
    approvedDrivers.drivers.forEach((driver) => {
      const row = make("div", undefined, "offer-row");
      const details = make("div");
      details.append(
        make("p", `${driver.name} · ${driver.vehicle_model} · ${driver.vehicle_number}`),
        make("span", `${driver.email} · ${driver.seat_capacity} seats · ${driver.online ? "ONLINE" : "OFFLINE"}`),
      );
      const suspend = make("button", "SUSPEND", "text-button danger-text");
      suspend.type = "button";
      suspend.addEventListener("click", () => suspendDriver(driver.id));
      row.append(details, suspend);
      approvedList.append(row);
    });
    const genderList = $("admin-gender-verifications");
    genderList.replaceChildren();
    if (!verifications.users.length) genderList.append(make("p", "No gender verification requests are waiting.", "empty-note"));
    verifications.users.forEach((person) => {
      const row = make("div", undefined, "offer-row");
      row.append(make("p", `${person.name} · ${person.gender} · ${person.email}`));
      const verify = make("button", "VERIFY", "outline-button");
      verify.type = "button";
      verify.addEventListener("click", () => reviewGender(person.id, true));
      row.append(verify);
      genderList.append(row);
    });
    const paymentList = $("admin-payments");
    paymentList.replaceChildren();
    if (!payments.payments.length) paymentList.append(make("p", "No UPI payments await confirmation.", "empty-note"));
    payments.payments.forEach((payment) => {
      const row = make("div", undefined, "offer-row");
      row.append(make("p", `${payment.rider_email} · INR ${Number(payment.amount_inr).toFixed(2)} · ride ${payment.ride_id}`));
      const confirm = make("button", "CONFIRM PAYMENT", "outline-button");
      confirm.type = "button";
      confirm.addEventListener("click", () => confirmManualPayment(payment.id));
      row.append(confirm);
      paymentList.append(row);
    });
    const incidentList = $("admin-incidents");
    incidentList.replaceChildren();
    if (!incidents.incidents.length) incidentList.append(make("p", "No SOS or safety incidents recorded.", "empty-note"));
    incidents.incidents.forEach((incident) => {
      const row = make("div", undefined, "offer-row");
      const details = make("div");
      details.append(
        make("p", `${incident.rider_email} · ${displayTime(incident.created_at)}`),
        make("span", `${incident.details || "SOS alert"} · (${Number(incident.latitude).toFixed(4)}, ${Number(incident.longitude).toFixed(4)})`),
      );
      row.append(details);
      incidentList.append(row);
    });
    renderAlgorithmLab(runs.runs);
  } catch (error) {
    message("auth-message", `Operations data unavailable: ${error.message}`, true);
  }
}

async function suspendDriver(driverId) {
  if (!window.confirm("Suspend this driver and revoke online access?")) return;
  try {
    await api(`/api/admin/drivers/${encodeURIComponent(driverId)}/suspend`, { method: "POST" });
    await loadAdmin();
  } catch (error) {
    message("auth-message", error.message, true);
  }
}

async function reviewGender(userId, verified) {
  try {
    await api(`/api/admin/users/${encodeURIComponent(userId)}/gender-verification?verified=${verified}`, { method: "POST" });
    await loadAdmin();
  } catch (error) {
    message("auth-message", error.message, true);
  }
}

async function confirmManualPayment(paymentId) {
  try {
    await api(`/api/admin/payments/${encodeURIComponent(paymentId)}/confirm`, { method: "POST" });
    await loadAdmin();
  } catch (error) {
    message("auth-message", error.message, true);
  }
}

function renderAlgorithmLab(runs) {
  const target = $("algorithm-lab");
  target.replaceChildren();
  if (!runs.length) {
    target.append(make("p", "No matching runs have been recorded yet.", "empty-note"));
    return;
  }
  const table = make("table", undefined, "data-table");
  const head = make("thead");
  const header = make("tr");
  [
    "TIME", "REQ", "SOLO · M", "GREEDY · M", "EXACT · M", "SAVED",
    "DP GAP", "NEAREST · M", "HUNGARIAN · M", "GREEDY · MS", "EXACT · MS",
  ].forEach((name) => header.append(make("th", name)));
  head.append(header);
  const body = make("tbody");
  runs.forEach((run) => {
    const row = make("tr");
    const savedPct = run.solo_cost_m > 0
      ? `${Math.max(0, ((run.solo_cost_m - run.exact_cost_m) / run.solo_cost_m) * 100).toFixed(1)}%`
      : "0%";
    [
      displayTime(run.created_at), run.requests, Number(run.solo_cost_m || 0).toFixed(0),
      Number(run.greedy_cost_m).toFixed(0), Number(run.exact_cost_m).toFixed(0),
      savedPct, `${run.grouping_gap_percent}%`,
      Number(run.nearest_cost_m || 0).toFixed(0), Number(run.hungarian_cost_m || 0).toFixed(0),
      Number(run.greedy_ms).toFixed(2), Number(run.exact_ms).toFixed(2),
    ].forEach((value) => row.append(make("td", value)));
    body.append(row);
  });
  table.append(head, body);
  target.append(table);
}

async function reviewDriver(driverId, approved) {
  try {
    await api(`/api/admin/drivers/${encodeURIComponent(driverId)}/approve?approved=${approved}`, { method: "POST" });
    await loadAdmin();
  } catch (error) {
    message("auth-message", error.message, true);
  }
}

async function installPush() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    throw new Error("Push alerts are not available in this browser.");
  }
  if (!state.config?.vapid_public_key) throw new Error("Push alerts are not configured.");
  const permission = await Notification.requestPermission();
  if (permission !== "granted") throw new Error("Allow notifications in browser settings to receive trip alerts.");
  const registration = await navigator.serviceWorker.register("/sw.js");
  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: decodeBase64Url(state.config.vapid_public_key),
  });
  await api("/api/push/subscribe", { method: "POST", body: subscription.toJSON() });
}

function decodeBase64Url(value) {
  const padded = value + "=".repeat((4 - value.length % 4) % 4);
  const raw = atob(padded.replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (character) => character.charCodeAt(0));
}

async function requestBrowserPosition() {
  if (!navigator.geolocation) throw new Error("This browser does not provide location access.");
  return new Promise((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(
      (position) => resolve({
        latitude: position.coords.latitude,
        longitude: position.coords.longitude,
      }),
      (error) => reject(new Error(`Location permission is required: ${error.message}`)),
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 5000 },
    );
  });
}

async function paySelectedRide() {
  if (!state.selectedRideId) throw new Error("Select a completed ride first.");
  const payment = await api(`/api/rides/${encodeURIComponent(state.selectedRideId)}/payment`, {
    method: "POST",
    body: { method: $("payment-method").value },
  });
  $("payment-qr").hidden = true;
  if (payment.wallet_balance_inr !== undefined && $("wallet-balance")) {
    $("wallet-balance").textContent = `INR ${Number(payment.wallet_balance_inr).toFixed(2)}`;
  }
  if (state.demoMode) {
    const status = payment.state.replaceAll("_", " ").toUpperCase();
    const next = payment.method === "upi"
      ? "The demo administrator can confirm this payment."
      : payment.method === "cash"
        ? "The demo driver can mark cash as collected."
        : "Demo payment settled.";
    message("payment-message", `SIMULATED ${payment.method?.toUpperCase()} · ${status}. ${next}`);
    notify("Payment updated", `${payment.method?.toUpperCase()} · ${status}`);
    await loadRides();
    await loadProfile();
    return;
  }
  if (payment.qr_data_url) {
    $("payment-qr").src = payment.qr_data_url;
    $("payment-qr").hidden = false;
    message("payment-message", `Scan the UPI code for INR ${Number(payment.amount_inr || 0).toFixed(2)}.`);
    return;
  }
  if (payment.order_id) {
    await openRazorpay(payment);
    message("payment-message", "Checkout opened. Waiting for provider webhook.");
    return;
  }
  message("payment-message", `Payment state: ${payment.state}.`);
}

async function openRazorpay(order) {
  if (!window.Razorpay) {
    await new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "https://checkout.razorpay.com/v1/checkout.js";
      script.onload = resolve;
      script.onerror = () => reject(new Error("Razorpay Checkout could not be loaded."));
      document.head.append(script);
    });
  }
  const checkout = new Razorpay({
    key: order.key_id,
    order_id: order.order_id,
    amount: order.amount_paise,
    currency: "INR",
    name: "PoolSmart",
    description: "Completed campus ride",
    handler: () => message("payment-message", "Payment submitted. Waiting for signed confirmation."),
    modal: { ondismiss: () => message("payment-message", "Checkout closed.") },
  });
  checkout.open();
}

async function refreshWeather() {
  try {
    const data = await api("/api/weather");
    $("weather-advice").textContent = data.notice;
  } catch (_error) {
    $("weather-advice").textContent = "Current weather advice is unavailable.";
  }
}

async function signInDemoAccount(email, password = "poolsmart-demo") {
  const response = await api("/api/auth/demo-login", {
    method: "POST",
    body: { email, password },
  });
  state.token = response.access_token;
  sessionStorage.setItem("poolsmart-token", state.token);
  if ($("demo-account")) $("demo-account").value = email;
  await loadProfile();
  message("auth-message", `Signed in as ${response.user.name} · LOCAL DEMO.`);
  notify("Signed in", `${response.user.name} (${response.user.role})`);
}

function bindEvents() {
  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-link]");
    if (!link) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const url = new URL(link.href, location.origin);
    if (url.origin !== location.origin || !ROUTES.has(url.pathname)) return;
    event.preventDefault();
    navigateTo(url.pathname);
  });
  window.addEventListener("popstate", () => navigateTo(location.pathname, true));

  document.querySelectorAll("#demo-role-grid .role-card").forEach((card) => {
    card.addEventListener("click", async () => {
      try {
        if (state.driverOnline) await setDriverAvailability(false);
        await signInDemoAccount(card.dataset.demoEmail);
      } catch (error) {
        message("auth-message", error.message, true);
      }
    });
  });

  $("otp-request-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/api/auth/request-otp", {
        method: "POST",
        body: { email: $("auth-email").value.trim() },
      });
      setHidden("otp-verify-form", false);
      $("auth-name").focus();
      message("auth-message", "Code sent. It expires in ten minutes.");
    } catch (error) {
      message("auth-message", error.message, true);
    }
  });
  $("otp-verify-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const response = await api("/api/auth/verify-otp", {
        method: "POST",
        body: {
          email: $("auth-email").value.trim(),
          code: $("auth-code").value.trim(),
          name: $("auth-name").value.trim(),
          consent: $("consent").checked,
        },
      });
      state.token = response.access_token;
      sessionStorage.setItem("poolsmart-token", state.token);
      await loadProfile();
      message("auth-message", `Signed in as ${response.user.name}.`);
    } catch (error) {
      message("auth-message", error.message, true);
    }
  });
  $("admin-login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const response = await api("/api/auth/admin-login", {
        method: "POST",
        body: { email: $("admin-email").value.trim(), password: $("admin-password").value },
      });
      state.token = response.access_token;
      sessionStorage.setItem("poolsmart-token", state.token);
      await loadProfile();
      message("auth-message", "Administrator signed in.");
    } catch (error) {
      message("auth-message", error.message, true);
    }
  });
  $("demo-login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await signInDemoAccount($("demo-account").value, $("demo-password").value);
    } catch (error) {
      message("auth-message", error.message, true);
    }
  });
  $("sign-out").addEventListener("click", async () => {
    try {
      if (state.driverOnline) await setDriverAvailability(false);
    } catch (error) {
      message("driver-message", `Could not mark driver offline: ${error.message}`, true);
    } finally {
      clearAuth();
    }
  });
  $("wallet-topup")?.addEventListener("click", async () => {
    try {
      const result = await api("/api/me/wallet/topup", { method: "POST", body: { amount_inr: 500 } });
      $("wallet-balance").textContent = `INR ${Number(result.wallet_balance_inr).toFixed(2)}`;
      notify("Wallet topped up", `New balance: INR ${Number(result.wallet_balance_inr).toFixed(2)}`);
    } catch (error) {
      message("auth-message", error.message, true);
    }
  });
  $("profile-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/api/me", { method: "PATCH", body: readProfileForm() });
      message("auth-message", "Profile and emergency contact saved.");
      notify("Profile saved", "Safety and contact settings updated.");
      await loadProfile();
    } catch (error) {
      message("auth-message", error.message, true);
    }
  });

  $("leave-now-btn")?.addEventListener("click", () => {
    $("leave-now-btn").classList.add("active");
    $("schedule-later-btn").classList.remove("active");
    $("ride-time").value = "";
    $("ride-time").hidden = true;
  });
  $("schedule-later-btn")?.addEventListener("click", () => {
    $("schedule-later-btn").classList.add("active");
    $("leave-now-btn").classList.remove("active");
    $("ride-time").hidden = false;
    $("ride-time").focus();
  });
  $("ride-wait")?.addEventListener("input", updateFareImpactCard);
  $("ride-detour")?.addEventListener("input", updateFareImpactCard);
  $("pool-size-slider")?.addEventListener("input", updateFareImpactCard);
  $("nav-notif-bell")?.addEventListener("click", () => {
    const drawer = $("activity-drawer");
    if (!drawer) return;
    drawer.hidden = !drawer.hidden;
    $("nav-notif-bell").setAttribute("aria-expanded", String(!drawer.hidden));
    if (!drawer.hidden) {
      activityLog.forEach((item) => { item.read = true; });
      renderActivityDrawer();
    }
  });
  $("clear-activity")?.addEventListener("click", () => {
    activityLog.length = 0;
    renderActivityDrawer();
  });
  $("modal-close")?.addEventListener("click", closeTripModal);
  $("trip-modal")?.addEventListener("click", (event) => {
    if (event.target === $("trip-modal")) closeTripModal();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("trip-modal")?.hidden) closeTripModal();
  });

  $("ride-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      if (!state.token) throw new Error("Sign in before requesting a ride.");
      if (!state.selectedPlaces.pickup || !state.selectedPlaces.drop) {
        throw new Error(state.demoMode
          ? "Choose both places from the demo search results or chips."
          : "Choose both pickup and destination from Google Places.");
      }
      const body = {
        pickup_place_id: state.selectedPlaces.pickup.place_id,
        drop_place_id: state.selectedPlaces.drop.place_id,
        seats: Number($("ride-seats").value),
        luggage_count: Number($("ride-luggage").value),
        max_wait_minutes: Number($("ride-wait").value),
        max_detour: Number($("ride-detour").value),
        budget_inr: $("ride-budget").value ? Number($("ride-budget").value) : null,
        scheduled_at: $("train-mode").checked || $("ride-time").hidden ? null : localDateTimeToIso($("ride-time").value),
        leave_by: $("train-mode").checked ? localDateTimeToIso($("arrival-time").value) : null,
        women_only: $("women-only").checked,
        same_gender: $("same-gender").checked,
      };
      const url = state.selectedPoolId
        ? `/api/pools/${encodeURIComponent(state.selectedPoolId)}/join`
        : "/api/rides";
      const ride = await api(url, { method: "POST", body });
      state.selectedPoolId = null;
      await loadRides();
      await loadPools();
      await refreshNetworkStats();
      const statusText = ride.status === "offered"
        ? "Matched and offered to an available driver. Waiting for driver acceptance."
        : ride.status === "pending"
          ? "Request submitted (pending). Visible in Open Pools for co-riders."
          : `Request ${ride.status}.`;
      message("ride-message", statusText);
      notify("Ride requested", statusText);
    } catch (error) {
      message("ride-message", error.message, true);
    }
  });
  $("swap-places")?.addEventListener("click", () => {
    const prevPickup = state.selectedPlaces.pickup;
    const prevDrop = state.selectedPlaces.drop;
    setSelectedPlace("pickup", prevDrop);
    setSelectedPlace("drop", prevPickup);
  });
  $("train-mode").addEventListener("change", () => {
    setHidden("arrival-field", !$("train-mode").checked);
    $("ride-time").disabled = $("train-mode").checked;
    $("arrival-time").required = $("train-mode").checked;
  });

  $("pool-filter-query")?.addEventListener("input", renderFilteredPools);
  $("pool-filter-safety")?.addEventListener("change", renderFilteredPools);
  $("pool-filter-seats")?.addEventListener("change", renderFilteredPools);
  $("refresh-pools").addEventListener("click", loadPools);

  $("simulate-step")?.addEventListener("click", async () => {
    if (!state.activeTripId) return;
    try {
      const res = await api(`/api/demo/trips/${encodeURIComponent(state.activeTripId)}/advance`, { method: "POST" });
      await loadRides();
      await loadPools();
      notify("Simulated progression", `Trip is now ${res.state.toUpperCase()} (${res.remaining_stops} stops remaining).`);
    } catch (error) {
      message("ride-message", error.message, true);
    }
  });

  $("driver-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/api/drivers/apply", {
        method: "POST",
        body: {
          name: $("driver-name").value.trim(),
          phone: $("driver-phone").value.trim(),
          licence_number: $("driver-licence").value.trim(),
          vehicle_number: $("vehicle-number").value.trim(),
          vehicle_model: $("vehicle-model").value.trim(),
          seat_capacity: Number($("vehicle-seats").value),
          insurance_expiry: localDateTimeToIso(`${$("insurance-expiry").value}T23:59`),
          photo_url: $("vehicle-photo").value.trim(),
        },
      });
      await loadDriver();
      message("driver-message", "Application submitted for administrator review.");
      notify("Driver application submitted", "Awaiting administrator review.");
    } catch (error) {
      message("driver-message", error.message, true);
    }
  });
  $("driver-online").addEventListener("click", async () => {
    try {
      await setDriverAvailability(!state.driverOnline);
    } catch (error) {
      message("driver-state", error.message, true);
    }
  });
  $("pay-ride").addEventListener("click", async () => {
    try {
      await paySelectedRide();
    } catch (error) {
      message("payment-message", error.message, true);
    }
  });
  $("download-receipt").addEventListener("click", async (event) => {
    event.preventDefault();
    const rideId = $("download-receipt").dataset.rideId || state.selectedRideId;
    if (!rideId || !state.token) return;
    try {
      const response = await fetch(`/api/rides/${encodeURIComponent(rideId)}/receipt`, {
        headers: { Authorization: `Bearer ${state.token}` },
      });
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || `Receipt download failed (${response.status}).`);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = make("a");
      link.href = url;
      link.download = `poolsmart-receipt-${rideId.slice(0, 8)}.pdf`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      message("payment-message", error.message, true);
    }
  });
  $("trip-share").addEventListener("click", async () => {
    try {
      const response = await api(`/api/trips/${encodeURIComponent(state.activeTripId)}/share-link`, { method: "POST" });
      const copyBtn = make("button", "COPY LINK", "gold-button");
      copyBtn.type = "button";
      copyBtn.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(response.url);
          notify("Link copied", "Time-limited share URL copied to clipboard.");
        } catch (_err) {
        }
        closeTripModal();
      });
      const openLink = make("a", "OPEN LIVE TRACKER →", "outline-button");
      openLink.href = response.url;
      openLink.target = "_blank";
      openLink.rel = "noopener noreferrer";
      openTripModal(
        "TRIP SHARE LINK",
        "Share live trip progress",
        `Anyone with this expiring link can view vehicle status and coordinates until the trip ends: ${response.url}`,
        [copyBtn, openLink],
      );
      message("ride-message", `Shareable trip link ready: ${response.url}`);
    } catch (error) {
      message("ride-message", `Could not share trip: ${error.message}`, true);
    }
  });
  $("trip-sos").addEventListener("click", () => {
    const prompt = state.demoMode
      ? "Record a simulated SOS for this demo trip? No emergency service will be contacted."
      : "Send your current location to the configured emergency response channel?";
    const confirmBtn = make("button", state.demoMode ? "RECORD SIMULATED SOS" : "SEND SOS ALERT", "gold-button");
    confirmBtn.type = "button";
    confirmBtn.addEventListener("click", async () => {
      closeTripModal();
      try {
        const position = state.demoMode && state.demoCampus
          ? { latitude: state.demoCampus.latitude, longitude: state.demoCampus.longitude }
          : await requestBrowserPosition();
        await api(`/api/rides/${encodeURIComponent(state.activeRideId)}/sos`, {
          method: "POST",
          body: {
            ...position,
            details: state.demoMode ? "Simulated demo SOS; no emergency service was contacted." : "Rider requested emergency assistance.",
          },
        });
        message("ride-message", state.demoMode ? "Simulated demo SOS recorded." : "SOS alert recorded and delivered.");
        notify("SOS recorded", "Emergency incident logged.", true);
      } catch (error) {
        message("ride-message", error.message, true);
      }
    });
    const cancelBtn = make("button", "CANCEL", "outline-button");
    cancelBtn.type = "button";
    cancelBtn.addEventListener("click", closeTripModal);
    openTripModal("SAFETY ALERT", "Confirm Emergency SOS", prompt, [confirmBtn, cancelBtn]);
  });
  $("enable-notifications").addEventListener("click", async () => {
    try {
      if (state.demoMode) {
        message("auth-message", "Simulated in-page demo alerts are active.");
        return;
      }
      await installPush();
      message("auth-message", "Trip alerts are enabled on this device.");
    } catch (error) {
      message("auth-message", error.message, true);
    }
  });
  $("export-data").addEventListener("click", async () => {
    try {
      const data = await api("/api/me/export");
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const link = make("a");
      link.href = url;
      link.download = "poolsmart-account-export.json";
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      message("auth-message", error.message, true);
    }
  });
  $("delete-account").addEventListener("click", async () => {
    if (!window.confirm("Delete your PoolSmart account? Active trips must be completed first.")) return;
    try {
      await api("/api/me", { method: "DELETE" });
      clearAuth();
      message("auth-message", "Account deleted and personal profile data cleared.");
    } catch (error) {
      message("auth-message", error.message, true);
    }
  });
  $("load-admin").addEventListener("click", loadAdmin);
  $("language").addEventListener("change", () => {
    const language = $("language").value;
    localStorage.setItem("poolsmart-language", language);
    applyLanguage(language);
  });
}

function renderLocalPreview() {
  state.localPreview = true;
  state.token = "";
  sessionStorage.removeItem("poolsmart-token");
  $("local-preview-map").hidden = false;
  $("map-status").textContent = "LOCAL PREVIEW · live services are off";
  applyLocalPreviewCopy();
  $("auth-panel").hidden = true;
  setHidden("demo-switcher-panel", true);
  updateModeIndicator();
  updateAuthGates();
  message("auth-message", "Read-only preview. Sign-in, bookings, maps, payments and driver tools need service credentials.");
}

function applyLocalPreviewCopy() {
  $("hero-subtitle").textContent = "Explore a read-only illustration. Live campus mapping and ride services are not configured.";
  $("map-legend-copy").textContent = "ILLUSTRATIVE NETWORK";
  $("account-heading").textContent = "Local preview; sign-in is off.";
}

function applyDemoCopy() {
  $("hero-eyebrow").textContent = "LOCAL DEMONSTRATION / FICTIONAL ROUTE NETWORK";
  $("hero-subtitle").textContent = "Plan shared campus & city rides, compare solo vs. pooled fares, and test live dispatch with zero external dependencies.";
  $("account-heading").textContent = "Account, demo roles & wallet.";
  $("account-intro").textContent = "Switch roles instantly or manage your demo wallet, saved places, safety preferences, and trip history.";
  $("ride-intro").textContent = "Select verified demo locations below or click any hub on the home map to pre-fill your route.";
  $("pool-intro").textContent = "Filter forming departures by destination, safety badge, and seats free, then join with one click.";
  $("driver-intro").textContent = "Toggle online status, accept pooled ride offers, verify rider start codes, and settle cash earnings.";
  $("footer-service-area").textContent = "LOCAL DEMONSTRATION · FICTIONAL PLACES";
}

async function renderDemoMode() {
  state.demoMode = true;
  $("auth-panel").hidden = false;
  $("auth-panel").querySelectorAll("form").forEach((form) => { form.hidden = true; });
  setHidden("demo-switcher-panel", false);
  setHidden("demo-login-form", false);
  setHidden("refresh-pools", false);
  setHidden("admin", true);
  updateModeIndicator();
  applyDemoCopy();
  await loadDemoMap();
  message("auth-message", "Choose any sample role above to start an end-to-end simulation.");
}

async function start() {
  bindEvents();
  const language = localStorage.getItem("poolsmart-language");
  if (language && $("language").querySelector(`option[value="${CSS.escape(language)}"]`)) {
    $("language").value = language;
  }
  applyLanguage($("language").value);
  $("train-mode").dispatchEvent(new Event("change"));
  navigateTo(location.pathname, true);
  if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost")) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
  try {
    const service = await api("/api/healthz");
    if (service.local_preview) {
      renderLocalPreview();
      return;
    }
    if (service.demo_mode) {
      await renderDemoMode();
      await refreshWeather();
    } else {
      await loadGoogleMaps();
      await refreshWeather();
    }
    await refreshNetworkStats();
  } catch (error) {
    $("map-status").textContent = `Service setup incomplete: ${error.message}`;
    message("auth-message", error.message, true);
  }
  if (state.token) {
    try {
      await loadProfile();
    } catch (_error) {
      clearAuth();
    }
  }
  window.setInterval(async () => {
    refreshNetworkStats();
    if (!state.token) return;
    if (state.demoMode && state.driverOnline && state.lastDriverPosition) {
      try {
        await api("/api/drivers/online", {
          method: "POST",
          body: { ...state.lastDriverPosition, online: true },
        });
      } catch (_error) {
      }
    }
    loadRides().catch(() => {});
    loadPools().catch(() => {});
    if (state.user?.role !== "admin") {
      loadDriver().catch(() => {});
    }
  }, 8000);
}

start();
