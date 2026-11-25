"use strict";

const state = {
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
  tripSocket: null,
  driverSocket: null,
  driverWatch: null,
  driverTimer: null,
  lastDriverPosition: null,
  driverOnline: false,
  poolTimer: null,
  offerTimer: null,
  activeDemoTrip: null,
  routes: new Map(),
  hubs: [],
  driverMarker: null,
};

const copy = {
  en: {
    navRide: "REQUEST A RIDE",
    navPools: "OPEN POOLS",
    navDriver: "DRIVER",
    navAccount: "ACCOUNT",
    heroLineOne: "One road.",
    heroLineTwo: "More than one way home.",
    heroSubtitle: "Request a verified cab, share the route, and keep your pickup limits in view.",
    accountHeading: "Sign in with your university email.",
    rideHeading: "Choose your pickup and destination.",
    poolHeading: "Rides forming now.",
    driverHeading: "Drive with the campus fleet.",
  },
  hi: {
    navRide: "सवारी माँगें",
    navPools: "खुले समूह",
    navDriver: "चालक",
    navAccount: "खाता",
    heroLineOne: "एक रास्ता।",
    heroLineTwo: "घर पहुँचने के कई रास्ते।",
    heroSubtitle: "सत्यापित कैब माँगें, रास्ता साझा करें और पिकअप की सीमाएँ देखें।",
    accountHeading: "विश्वविद्यालय ईमेल से साइन इन करें।",
    rideHeading: "पिकअप और गंतव्य चुनें।",
    poolHeading: "अभी बन रहे साझा सफ़र।",
    driverHeading: "कैंपस बेड़े के साथ ड्राइव करें।",
  },
  te: {
    navRide: "ప్రయాణాన్ని కోరండి",
    navPools: "తెరిచి ఉన్న పూల్స్",
    navDriver: "డ్రైవర్",
    navAccount: "ఖాతా",
    heroLineOne: "ఒకే దారి.",
    heroLineTwo: "ఇంటికి చేరే మార్గాలు మరెన్నో.",
    heroSubtitle: "ధృవీకరించిన క్యాబ్‌ను కోరండి, మార్గాన్ని పంచుకోండి, పికప్ పరిమితులను గమనించండి.",
    accountHeading: "మీ విశ్వవిద్యాలయ ఈమెయిల్‌తో సైన్ ఇన్ చేయండి.",
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

function message(id, text = "", error = false) {
  const output = $(id);
  if (!output) return;
  output.textContent = text;
  output.classList.toggle("error", Boolean(error));
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

function setHidden(id, hidden) {
  const node = $(id);
  if (node) node.hidden = Boolean(hidden);
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

function haversineKm(lat1, lon1, lat2, lon2) {
  const toRad = (deg) => (deg * Math.PI) / 180;
  const r = 6371.0088;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a = Math.sin(dLat / 2) ** 2
    + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return r * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function updateFarePreview() {
  const preview = $("fare-preview");
  if (!preview) return;
  const pickup = state.selectedPlaces.pickup;
  const drop = state.selectedPlaces.drop;
  if (
    !pickup || !drop || pickup.place_id === drop.place_id
    || !Number.isFinite(pickup.latitude) || !Number.isFinite(pickup.longitude)
    || !Number.isFinite(drop.latitude) || !Number.isFinite(drop.longitude)
  ) {
    preview.textContent = "";
    preview.hidden = true;
    return;
  }
  const roadFactor = state.demoMode ? 1.25 : 1.35;
  const speedKmh = state.demoMode ? 24 : 25;
  const km = haversineKm(pickup.latitude, pickup.longitude, drop.latitude, drop.longitude) * roadFactor;
  const mins = Math.max(0.5, (km / speedKmh) * 60);
  const hour = new Date().getHours();
  const surge = hour >= 21 || hour < 6 ? 1.2 : 1.0;
  const soloFare = (25 + km * 12 + mins * 1) * surge;
  preview.textContent = `Estimated solo route: ${km.toFixed(1)} km · ~${Math.ceil(mins)} min · ~INR ${soloFare.toFixed(0)} (pooling shares the fare).`;
  preview.hidden = false;
}

function renderDemoSvgMap(activeTrip = state.activeDemoTrip) {
  const svg = $("demo-map-svg");
  if (!svg || !state.demoPlaces.length) return;
  state.activeDemoTrip = activeTrip || null;
  const lats = state.demoPlaces.map((p) => p.latitude);
  const lons = state.demoPlaces.map((p) => p.longitude);
  const minLat = Math.min(...lats);
  const maxLat = Math.max(...lats);
  const minLon = Math.min(...lons);
  const maxLon = Math.max(...lons);
  const project = (lat, lon) => {
    const nx = maxLon === minLon ? 0.5 : (lon - minLon) / (maxLon - minLon);
    const ny = maxLat === minLat ? 0.5 : (maxLat - lat) / (maxLat - minLat);
    const mobile = window.innerWidth <= 600;
    return {
      x: (mobile ? 310 : 410) + nx * (mobile ? 380 : 500),
      y: (mobile ? 115 : 100) + ny * (mobile ? 430 : 460),
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
  const campus = state.demoCampus || state.demoPlaces[0];
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
    group.append(el("circle", { r: isSelected ? 7.5 : isCampus ? 6.5 : 4.8 }));
    const shortLabel = place.name.replace(/^Demo\s+/i, "").toUpperCase();
    group.append(el("text", { y: isCampus ? 20 : -11 }, shortLabel));
    group.addEventListener("click", () => {
      if (!state.token) return;
      if (!state.selectedPlaces.pickup || state.selectedPlaces.pickup.place_id === place.place_id) {
        setSelectedPlace("pickup", place);
      } else {
        setSelectedPlace("drop", place);
      }
    });
    svg.append(group);
  });
  if (
    activeTrip?.driver_location
    && Number.isFinite(activeTrip.driver_location.latitude)
    && Number.isFinite(activeTrip.driver_location.longitude)
  ) {
    const cab = project(activeTrip.driver_location.latitude, activeTrip.driver_location.longitude);
    const cabNode = el("g", {
      class: "demo-map-cab",
      transform: `translate(${cab.x.toFixed(1)} ${cab.y.toFixed(1)})`,
    });
    cabNode.append(el("circle", { r: 7 }));
    cabNode.append(el("text", { y: -13 }, "CAB"));
    svg.append(cabNode);
  }
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
  if (demoInput) {
    demoInput.value = enriched ? enriched.name : (demoInput.value && enriched === null ? demoInput.value : "");
  }
  $(`${kind}-selected`).textContent = enriched
    ? `${enriched.name}${enriched.address ? ` · ${enriched.address}` : ""}`
    : "Choose a verified place.";
  updateFarePreview();
  if (state.demoMode) {
    renderDemoSvgMap();
  }
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
  autocomplete.placeholder = kind === "pickup" ? "Search pickup place" : "Search destination";
  autocomplete.setAttribute("aria-label", kind === "pickup" ? "Search pickup place" : "Search destination");
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
  input.placeholder = kind === "pickup" ? "Search demo pickup places" : "Search demo destinations";
  input.setAttribute("aria-label", input.placeholder);
  const results = make("div", undefined, "demo-place-results");
  results.hidden = true;
  let searchSequence = 0;
  let activeIndex = -1;
  const kindLabel = (placeKind) => (
    placeKind === "campus" ? "CAMPUS" : placeKind === "campus_hub" ? "HUB" : "DEST"
  );
  const appendOption = (place) => {
    const option = make("button", undefined, "demo-place-option");
    option.type = "button";
    option.append(make("span", place.name));
    option.append(make("span", kindLabel(place.kind), "place-kind-tag"));
    option.addEventListener("click", () => {
      setSelectedPlace(kind, place);
      input.value = place.name;
      results.hidden = true;
      activeIndex = -1;
    });
    results.append(option);
  };
  const syncActive = () => {
    const items = results.querySelectorAll("button.demo-place-option");
    items.forEach((btn, idx) => btn.classList.toggle("active", idx === activeIndex));
    if (activeIndex >= 0 && items[activeIndex]) {
      items[activeIndex].scrollIntoView({ block: "nearest" });
    }
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
      if (!localMatches.length) {
        results.append(make("span", "No matching demo places."));
      }
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
      if (!response.places.length) {
        results.append(make("span", "No matching demo places."));
      }
      results.hidden = false;
    } catch (error) {
      results.replaceChildren(make("span", error.message));
      results.hidden = false;
    }
  };
  input.addEventListener("input", () => {
    setSelectedPlace(kind, null);
    window.clearTimeout(input.searchTimer);
    input.searchTimer = window.setTimeout(renderResults, 120);
  });
  input.addEventListener("focus", () => {
    renderResults();
  });
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
}

async function loadDemoMap() {
  state.config = await api("/api/map/config");
  const response = await api("/api/places/hubs");
  state.demoPlaces = response.places;
  state.hubs = response.places;
  state.demoCampus = response.places.find((place) => place.kind === "campus");
  if (!state.demoCampus) throw new Error("Demo campus place data is missing. Reset the demo database.");
  $("local-preview-map").hidden = false;
  $("map").setAttribute("aria-label", "Fictional local demonstration map; no live roads or GPS");
  $("map-status").textContent = "LOCAL DEMO · simulated route network";
  $("map-legend-copy").textContent = "FICTIONAL DEMO PLACES";
  $("demo-map-caption").textContent = "INTERACTIVE DEMO NETWORK · CLICK NODES OR SEARCH BELOW";
  renderDemoSvgMap(null);
  installDemoPlacePicker("pickup");
  installDemoPlacePicker("drop");
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
  state.tripSocket.addEventListener("error", () => {
    message("ride-message", "Live location connection was interrupted. Ride details remain available.", true);
  });
}

async function showTrip(tripId, rideId) {
  const trip = await api(`/api/trips/${encodeURIComponent(tripId)}`);
  state.activeTripId = tripId;
  state.activeRideId = rideId;
  $("trip-summary").textContent =
    `${trip.driver.first_name} · ${trip.driver.vehicle_model} · ${trip.driver.vehicle_number}`;
  const meta = $("trip-meta");
  if (meta) {
    meta.replaceChildren();
    const km = (Number(trip.distance_m || 0) / 1000).toFixed(1);
    const mins = Math.max(1, Math.round(Number(trip.duration_s || 0) / 60));
    const fare = trip.fare_inr !== null && trip.fare_inr !== undefined ? `INR ${Number(trip.fare_inr).toFixed(2)}` : "Pending";
    const coRiders = (trip.riders || []).map((r) => `${r.first_name} (${r.drop_area})`).join(", ");
    meta.append(
      make("span", `Route: ${km} km · ~${mins} min`),
      make("span", `Your share: ${fare}`),
      make("span", trip.riders?.length > 1 ? `Pooled riders: ${coRiders}` : "Solo cab trip"),
    );
  }
  setHidden("trip-live", false);
  setHidden("active-trip-actions", !["accepted", "started"].includes(trip.state));
  setHidden("trip-estimate", !trip.estimated);
  if (state.demoMode) {
    $("trip-estimate").textContent = "Simulated route and times · no live road routing or GPS data.";
    renderDemoSvgMap(trip);
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
  const contact = await api(`/api/trips/${encodeURIComponent(tripId)}/driver-contact`);
  if (contact.phone) {
    $("driver-call").href = `tel:${contact.phone}`;
    $("driver-call").textContent = `CALL ${contact.name}`;
    setHidden("driver-call", false);
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

async function renderRides(rides) {
  const target = $("ride-list");
  target.replaceChildren();
  const active = rides.find((ride) => ["confirmed", "in_progress"].includes(ride.status) && ride.trip_id);
  if (!rides.length) {
    target.append(make("p", "No ride requests yet.", "empty-note"));
    setHidden("payment-panel", true);
    setHidden("active-trip-actions", true);
    setHidden("trip-live", true);
    return;
  }
  const table = make("table", undefined, "data-table");
  const head = make("thead");
  const headerRow = make("tr");
  ["STATUS", "PICKUP", "DESTINATION", "DEPARTURE", "FARE · INR", "ACTIONS"].forEach((label) => {
    headerRow.append(make("th", label));
  });
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
      ride.fare_inr === null ? "Pending" : Number(ride.fare_inr).toFixed(2),
    ].forEach((value) => row.append(make("td", value)));
    const actions = make("td");
    if (ride.trip_id && ["confirmed", "in_progress"].includes(ride.status)) {
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
      const cancel = make("button", "CANCEL", "text-button");
      cancel.type = "button";
      cancel.addEventListener("click", async () => {
        try {
          await api(`/api/rides/${encodeURIComponent(ride.id)}`, { method: "DELETE" });
          await loadRides();
          await loadPools();
          message("ride-message", "Ride request cancelled.");
        } catch (error) {
          message("ride-message", error.message, true);
        }
      });
      actions.append(cancel);
    }
    row.append(actions);
    body.append(row);
  });
  table.append(head, body);
  const scroll = make("div", undefined, "table-scroll");
  scroll.append(table);
  target.append(scroll);
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

async function loadPools() {
  if (!state.token) return;
  try {
    const { pools } = await api("/api/pools/open");
    const list = $("pool-list");
    list.replaceChildren();
    if (!pools.length) {
      list.append(make("p", "No compatible pending departures are visible right now.", "empty-note"));
      return;
    }
    pools.forEach((pool) => {
      const item = make("div", undefined, "pool-entry");
      item.append(make("strong", pool.destination));
      item.append(make("span", pool.destination_area));
      item.append(make("span", `${pool.rider_count} rider${pool.rider_count === 1 ? "" : "s"} · ${pool.seats_left} seat${pool.seats_left === 1 ? "" : "s"} shown free`));
      item.append(make("span", pool.fare_notice));
      const select = make("button", "SET DESTINATION", "outline-button");
      select.type = "button";
      select.addEventListener("click", () => {
        state.selectedPoolId = pool.pool_id;
        setSelectedPlace("drop", {
          place_id: pool.drop_place_id,
          name: pool.destination,
          address: pool.destination_area,
        }, true);
        message("ride-message", "Destination selected. Matching still checks seats, timing, route limits, and safety preferences.");
        location.hash = "#ride";
      });
      item.append(select);
      list.append(item);
    });
  } catch (error) {
    $("pool-list").replaceChildren(make("p", error.message, "empty-note"));
  }
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
  $("account-name").textContent = `${profile.name} · ${profile.role}`;
  $("profile-phone").value = profile.phone || "";
  $("profile-gender").value = profile.gender || "unspecified";
  $("gender-verification-note").textContent = profile.gender_verified
    ? "Gender verification is on file. You may request compatible pools."
    : "Gender-matched rides require administrator verification against university records. No identity document is stored here.";
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
  setHidden("auth-panel", false);
  document.querySelectorAll("#auth-panel > form").forEach((form) => { form.hidden = true; });
  setHidden("signed-in", false);
  setHidden("profile-panel", false);
  setHidden("ride-form", false);
  setHidden("admin", profile.role !== "admin");
  setHidden("driver-form", profile.role === "admin");
  if (profile.role === "admin") await loadAdmin();
  const results = await Promise.allSettled([loadRides(), loadPools(), loadDriver()]);
  results.forEach((result) => {
    if (result.status === "rejected") message("auth-message", result.reason.message, true);
  });
}

function clearAuth() {
  state.token = "";
  state.user = null;
  sessionStorage.removeItem("poolsmart-token");
  closeTripSocket();
  clearLiveMapData();
  closeDriverSocket();
  clearDriverWatch();
  state.driverOnline = false;
  state.lastDriverPosition = null;
  state.selectedPoolId = null;
  state.selectedRideId = null;
  state.activeTripId = null;
  state.activeRideId = null;
  state.activeDriverTripId = null;
  state.selectedPlaces = { pickup: null, drop: null };
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
  $("pickup-selected").textContent = "Choose a verified place.";
  $("drop-selected").textContent = "Choose a verified place.";
  $("driver-active-trip").replaceChildren();
  $("driver-offers").replaceChildren();
  $("driver-name").value = "";
  $("driver-phone").value = "";
  $("admin-metrics").replaceChildren();
  $("admin-drivers").replaceChildren();
  $("admin-gender-verifications").replaceChildren();
  $("admin-payments").replaceChildren();
  $("algorithm-lab").replaceChildren();
  $("payment-qr").hidden = true;
  setHidden("download-receipt", true);
  setHidden("payment-panel", true);
  setHidden("signed-in", true);
  setHidden("profile-panel", true);
  setHidden("driver-tools", true);
  setHidden("ride-form", true);
  setHidden("driver-form", true);
  setHidden("admin", true);
  setHidden("trip-live", true);
  setHidden("active-trip-actions", true);
  if (state.demoMode) {
    $("auth-panel").querySelectorAll("form").forEach((form) => { form.hidden = true; });
    setHidden("demo-login-form", false);
  } else {
    $("auth-panel").querySelectorAll("form").forEach((form) => { form.hidden = false; });
    setHidden("otp-verify-form", true);
  }
  if (state.demoMode) renderDemoSvgMap(null);
  updateFarePreview();
  $("ride-list").replaceChildren(make("p", "Sign in to see your ride requests.", "empty-note"));
  $("pool-list").replaceChildren(make("p", "Sign in to view current departures.", "empty-note"));
  if (state.offerTimer) window.clearInterval(state.offerTimer);
  state.offerTimer = null;
  setHidden("driver-summary", true);
  $("admin-approved-drivers")?.replaceChildren();
  $("admin-incidents")?.replaceChildren();
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
    if (hasActiveTrip) {
      target.append(make("p", "Active trip in progress.", "empty-note"));
    } else {
      target.append(make("p", "No offers at the moment. Keep GPS sharing on to receive requests.", "empty-note"));
    }
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
  if (!state.token || state.user?.role === "admin") {
    setHidden("driver-tools", true);
    return;
  }
  try {
    const response = await api("/api/driver/offers");
    if (response.application_status === "not_applied") {
      setHidden("driver-tools", true);
      setHidden("driver-form", false);
      setHidden("driver-summary", true);
      return;
    }
    const mine = await api("/api/drivers/status");
    state.driverOnline = mine.online;
    if (state.demoMode && state.demoCampus) {
      state.lastDriverPosition = {
        latitude: state.demoCampus.latitude,
        longitude: state.demoCampus.longitude,
        speed_m_s: 0,
      };
    }
    setHidden("driver-tools", false);
    setHidden("driver-form", mine.approval_status !== "rejected");
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
  target.append(make("h3", `Active trip · ${trip.state.replaceAll("_", " ")}`));
  target.append(make("p", `${km} km · ~${mins} min · Net driver earnings: INR ${Number(trip.vehicle_earnings_inr || 0).toFixed(2)}`, "selection-note"));
  trip.passengers.forEach((rider) => {
    const passenger = make("div", undefined, "offer-row");
    const paymentStatus = rider.payment_state
      ? `${rider.payment_method?.toUpperCase()} · ${rider.payment_state.toUpperCase()}`
      : "No payment method selected";
    passenger.append(make("p", `${rider.first_name} · ${rider.drop_area} · INR ${Number(rider.fare_inr || 0).toFixed(2)} · ${paymentStatus}`));
    if (rider.ride_status === "completed" && (!rider.payment_state || rider.payment_state === "due")) {
      const collected = make("button", "MARK CASH COLLECTED", "text-button");
      collected.type = "button";
      collected.addEventListener("click", async () => {
        try {
          await api(`/api/driver/trips/${encodeURIComponent(trip.id)}/rides/${encodeURIComponent(rider.ride_id)}/cash-collected`, {
            method: "POST",
          });
          await loadDriverTrip();
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
          await api(`/api/driver/trips/${encodeURIComponent(trip.id)}/stops/${stop.id}/complete`, {
            method: "POST",
            body: { stop_id: stop.id },
          });
          await loadDriverTrip();
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
  state.driverSocket.addEventListener("error", () => {
    message("driver-state", "Secure GPS connection failed. Check your connection and try again.", true);
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
    $("driver-online").textContent = "GO ONLINE";
    $("driver-state").textContent = state.demoMode
      ? "Offline; no location is being shared."
      : "GPS sharing is off.";
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
    if (!approvedDrivers.drivers.length) {
      approvedList.append(make("p", "No approved drivers in fleet.", "empty-note"));
    }
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
    if (!verifications.users.length) {
      genderList.append(make("p", "No gender verification requests are waiting.", "empty-note"));
    }
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
    if (!payments.payments.length) {
      paymentList.append(make("p", "No UPI payments await confirmation.", "empty-note"));
    }
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
    if (!incidents.incidents.length) {
      incidentList.append(make("p", "No SOS or safety incidents recorded.", "empty-note"));
    }
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
  ].forEach((name) => {
    header.append(make("th", name));
  });
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
  if (state.demoMode) {
    const status = payment.state.replaceAll("_", " ").toUpperCase();
    const next = payment.method === "upi"
      ? "The demo administrator can confirm this payment."
      : payment.method === "cash"
        ? "The demo driver can mark cash as collected."
        : "Demo payment is marked paid.";
    message("payment-message", `SIMULATED ${payment.method?.toUpperCase() || $("payment-method").value.toUpperCase()} · ${status}. ${next}`);
    await loadRides();
    return;
  }
  if (payment.qr_data_url) {
    $("payment-qr").src = payment.qr_data_url;
    $("payment-qr").hidden = false;
    message("payment-message", `Scan the UPI code for INR ${Number(payment.amount_inr || 0).toFixed(2)}. The provider must confirm payment before it is marked paid.`);
    return;
  }
  if (payment.order_id) {
    await openRazorpay(payment);
    message("payment-message", "Checkout opened. The server marks payment paid only after a valid provider webhook.");
    return;
  }
  message("payment-message", `Payment state: ${payment.state}. Cash payment is confirmed by the driver.`);
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
    handler: () => message("payment-message", "Payment submitted. Waiting for signed confirmation from Razorpay."),
    modal: { ondismiss: () => message("payment-message", "Checkout closed. No payment confirmation was received.") },
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

function bindEvents() {
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
      const response = await api("/api/auth/demo-login", {
        method: "POST",
        body: {
          email: $("demo-account").value,
          password: $("demo-password").value,
        },
      });
      state.token = response.access_token;
      sessionStorage.setItem("poolsmart-token", state.token);
      await loadProfile();
      message("auth-message", `Signed in as ${response.user.name} · LOCAL DEMO.`);
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
  $("profile-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/api/me", { method: "PATCH", body: readProfileForm() });
      message("auth-message", "Profile and emergency contact saved.");
      await loadProfile();
    } catch (error) {
      message("auth-message", error.message, true);
    }
  });
  $("ride-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      if (!state.token) throw new Error("Sign in before requesting a ride.");
      if (!state.selectedPlaces.pickup || !state.selectedPlaces.drop) {
        throw new Error(state.demoMode
          ? "Choose both places from the fictional demo search results."
          : "Choose both pickup and destination from the Google Places results.");
      }
      const body = {
        pickup_place_id: state.selectedPlaces.pickup.place_id,
        drop_place_id: state.selectedPlaces.drop.place_id,
        seats: Number($("ride-seats").value),
        luggage_count: Number($("ride-luggage").value),
        max_wait_minutes: Number($("ride-wait").value),
        max_detour: Number($("ride-detour").value),
        budget_inr: $("ride-budget").value ? Number($("ride-budget").value) : null,
        scheduled_at: $("train-mode").checked ? null : localDateTimeToIso($("ride-time").value),
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
      const statusText = ride.status === "offered"
        ? "Request matched and offered to an available driver. Waiting for driver acceptance."
        : ride.status === "pending"
          ? "Request submitted (pending). Matching checks compatible pools and online drivers."
          : `Request ${ride.status}.`;
      message("ride-message", statusText);
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
  $("refresh-pools").addEventListener("click", loadPools);
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
      try {
        await navigator.clipboard.writeText(response.url);
        message("ride-message", `Time-limited trip link copied: ${response.url}`);
      } catch (_clipError) {
        message("ride-message", `Shareable trip link: ${response.url}`);
      }
    } catch (error) {
      message("ride-message", `Could not share trip: ${error.message}`, true);
    }
  });
  $("trip-sos").addEventListener("click", async () => {
    const prompt = state.demoMode
      ? "Record a simulated SOS for this demo trip? No emergency service will be contacted."
      : "Send your current location to the configured emergency response channel?";
    if (!window.confirm(prompt)) return;
    try {
      const position = state.demoMode && state.demoCampus
        ? { latitude: state.demoCampus.latitude, longitude: state.demoCampus.longitude }
        : await requestBrowserPosition();
      await api(`/api/rides/${encodeURIComponent(state.activeRideId)}/sos`, {
        method: "POST",
        body: {
          ...position,
          details: state.demoMode
            ? "Simulated demo SOS; no emergency service was contacted."
            : "Rider requested emergency assistance.",
        },
      });
      message("ride-message", state.demoMode
        ? "Simulated demo SOS recorded. No emergency service was contacted."
        : "SOS alert recorded and delivered. Call local emergency services if you need immediate help.");
    } catch (error) {
      message("ride-message", error.message, true);
    }
  });
  $("enable-notifications").addEventListener("click", async () => {
    try {
      if (state.demoMode) {
        message("auth-message", "Simulated in-page demo alerts are active. Browser and external push services are disabled.");
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
    message("auth-message", language === "en"
      ? "Language set to English."
      : "Navigation and section headings are translated. Service forms and notices remain in English.", language !== "en");
  });
}

function renderLocalPreview() {
  state.localPreview = true;
  state.token = "";
  sessionStorage.removeItem("poolsmart-token");
  $("local-preview-map").hidden = false;
  $("map-status").textContent = "LOCAL PREVIEW · live services are off";
  $("map").setAttribute("aria-label", "Illustrative local preview route diagram, not a live map");
  $("local-preview-banner").hidden = false;
  applyLocalPreviewCopy();
  $("auth-panel").hidden = true;
  message("auth-message", "Read-only preview. Sign-in, bookings, maps, payments and driver tools need service credentials.");
  setHidden("ride-form", true);
  setHidden("payment-panel", true);
  setHidden("active-trip-actions", true);
  setHidden("trip-live", true);
  setHidden("driver-form", true);
  setHidden("driver-tools", true);
  setHidden("refresh-pools", true);
  $("ride-list").replaceChildren(make("p", "Ride booking is disabled in local preview.", "empty-note"));
  $("pool-list").replaceChildren(make("p", "Live departures are disabled in local preview.", "empty-note"));
  message("ride-message", "Live route matching is disabled in local preview.");
  message("driver-message", "Driver service is disabled in local preview.");
}

function applyLocalPreviewCopy() {
  $("hero-subtitle").textContent = "Explore a read-only illustration. Live campus mapping and ride services are not configured.";
  $("map-legend-copy").textContent = "ILLUSTRATIVE NETWORK";
  $("account-heading").textContent = "Local preview; sign-in is off.";
  $("account-intro").textContent = "University email, OTP delivery and account services require live service configuration.";
  $("ride-intro").textContent = "Live place verification and route matching are disabled in this preview.";
  $("pool-intro").textContent = "No live departures are loaded in this read-only preview.";
  $("driver-intro").textContent = "Driver applications and location sharing are disabled in this preview.";
}

function applyDemoCopy() {
  $("hero-eyebrow").textContent = "LOCAL DEMONSTRATION / FICTIONAL ROUTE NETWORK";
  $("hero-subtitle").textContent = "Try ride requests, cab offers and payments with fictional places and simulated services. Nothing contacts a live provider.";
  $("account-heading").textContent = "Choose a local demo account.";
  $("account-intro").textContent = "Use one of the sample rider, driver or administrator accounts. Demo changes are stored only in the separate local demo database.";
  $("ride-intro").textContent = "Search fictional demo places. Routes, travel times and cab locations are simulated and are not real-world directions.";
  $("pool-intro").textContent = "View demo ride requests by destination area. Riders and departures in this mode are sample data.";
  $("driver-intro").textContent = "Submit a sample driver application or use the approved demo driver. GPS, offers and trips are simulated locally.";
  $("footer-service-area").textContent = "LOCAL DEMONSTRATION · FICTIONAL PLACES";
  $("privacy-copy").textContent = "Local demo mode stores sample accounts and activity in a separate demo database on this computer. The route network, driver locations, weather, payment results, alerts and SOS responses are simulations. No live location is collected, and no Maps, email, push, payment or emergency-response provider is contacted. Use the live service only after configuring and verifying its required integrations.";
  $("admin").querySelector(".section-head > p:last-child").textContent = "Sample driver reviews, simulated payment confirmations and algorithm activity from local demo requests.";
  $("admin").querySelectorAll(".selection-note")[0].textContent = "This review changes only the local demo account. No real identity verification is performed.";
  $("admin").querySelectorAll(".selection-note")[1].textContent = "Confirm only the sample payment shown in this local demo database.";
  const methods = $("payment-method").options;
  methods[0].textContent = "UPI · simulated admin confirmation";
  methods[1].textContent = "Razorpay · simulated result";
  methods[2].textContent = "Cash · simulated driver collection";
}

async function renderDemoMode() {
  state.demoMode = true;
  $("mode-indicator").hidden = false;
  $("demo-mode-banner").hidden = false;
  $("local-preview-banner").hidden = true;
  $("auth-panel").hidden = false;
  $("auth-panel").querySelectorAll("form").forEach((form) => { form.hidden = true; });
  setHidden("demo-login-form", false);
  setHidden("refresh-pools", false);
  setHidden("admin", true);
  applyDemoCopy();
  await loadDemoMap();
  message("auth-message", "LOCAL DEMO: choose a sample account. The displayed password is for local demonstration only.");
}

async function start() {
  bindEvents();
  const language = localStorage.getItem("poolsmart-language");
  if (language && $("language").querySelector(`option[value="${CSS.escape(language)}"]`)) {
    $("language").value = language;
  }
  applyLanguage($("language").value);
  $("train-mode").dispatchEvent(new Event("change"));
  if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost")) {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      message("auth-message", "Offline assets could not be registered on this device.", true);
    });
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
    if (!state.token) return;
    if (state.demoMode && state.driverOnline && state.lastDriverPosition) {
      try {
        await api("/api/drivers/online", {
          method: "POST",
          body: { ...state.lastDriverPosition, online: true },
        });
      } catch (error) {
        message("driver-state", error.message, true);
      }
    }
    loadRides().catch((error) => message("ride-message", error.message, true));
    loadPools().catch((error) => message("ride-message", error.message, true));
    if (state.user?.role !== "admin") {
      loadDriver().catch((error) => message("driver-message", error.message, true));
    }
  }, 8000);
}

start();
