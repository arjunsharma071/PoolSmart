import hashlib
import logging
import math
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.models import MapsQuota, Place, TravelCache, utc_now


logger = logging.getLogger("poolsmart.maps")
PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
ROUTE_MATRIX_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
COMPUTE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

HUB_SEARCHES = [
    ("campus", "SRM University-AP, Neerukonda, Andhra Pradesh, India"),
    ("campus_hub", "SRM University-AP main gate, Neerukonda, Andhra Pradesh"),
    ("campus_hub", "SRM University-AP hostel, Neerukonda, Andhra Pradesh"),
    ("campus_hub", "SRM University-AP academic block, Neerukonda, Andhra Pradesh"),
    ("campus_hub", "SRM University-AP library, Neerukonda, Andhra Pradesh"),
    ("campus_hub", "SRM University-AP canteen, Neerukonda, Andhra Pradesh"),
    ("destination", "Vijayawada Railway Station, Andhra Pradesh, India"),
    ("destination", "Pandit Nehru Bus Station, Vijayawada, Andhra Pradesh"),
    ("destination", "Vijayawada Airport, Gannavaram, Andhra Pradesh"),
    ("destination", "Guntur Junction, Andhra Pradesh, India"),
    ("destination", "Mangalagiri, Andhra Pradesh, India"),
    ("destination", "Tadepalli, Andhra Pradesh, India"),
    ("destination", "Amaravati, Andhra Pradesh, India"),
]


class MapsProviderError(RuntimeError):
    def __init__(self, message, status=None, quota=False):
        super().__init__(message)
        self.status = status
        self.quota = quota


class GoogleMaps:
    def __init__(self, settings=None, client=None):
        self.settings = settings or get_settings()
        self.client = client or httpx.AsyncClient(timeout=20.0)
        self._owns_client = client is None

    async def close(self):
        if self._owns_client:
            await self.client.aclose()

    def _key(self):
        return self.settings.google_maps_server_key.get_secret_value()

    def _consume_quota(self, session, elements, requests=1):
        today = utc_now().date().isoformat()
        usage = session.get(MapsQuota, today) or MapsQuota(day=today, elements=0, requests=0)
        if usage.elements + elements > self.settings.maps_daily_element_limit:
            raise MapsProviderError("The configured daily Google Maps quota limit was reached.", 429, True)
        usage.elements += elements
        usage.requests += requests
        session.merge(usage)
        session.commit()

    async def _post(self, url, body, field_mask):
        response = await self.client.post(
            url,
            json=body,
            headers={
                "X-Goog-Api-Key": self._key(),
                "X-Goog-FieldMask": field_mask,
            },
        )
        if response.is_error:
            detail = response.text[:300]
            quota = response.status_code == 429 or "RESOURCE_EXHAUSTED" in detail
            raise MapsProviderError(
                "Google Maps request failed (%s): %s" % (response.status_code, detail),
                response.status_code,
                quota,
            )
        return response.json()

    async def search_text(self, text, bias=None, session=None):
        if session is not None:
            self._consume_quota(session, 1)
        body = {"textQuery": text}
        if bias:
            body["locationBias"] = {
                "circle": {
                    "center": {"latitude": bias[0], "longitude": bias[1]},
                    "radius": 85000.0,
                }
            }
        data = await self._post(
            PLACES_URL,
            body,
            "places.id,places.displayName,places.formattedAddress,places.location",
        )
        places = data.get("places", [])
        if not places:
            raise MapsProviderError("Places API found no result for a configured place query.")
        return places

    async def details(self, place_id, session=None):
        if session is not None:
            self._consume_quota(session, 1)
        response = await self.client.get(
            "https://places.googleapis.com/v1/places/" + place_id,
            headers={
                "X-Goog-Api-Key": self._key(),
                "X-Goog-FieldMask": "id,displayName,formattedAddress,location",
            },
        )
        if response.is_error:
            detail = response.text[:300]
            quota = response.status_code == 429 or "RESOURCE_EXHAUSTED" in detail
            raise MapsProviderError("Place lookup failed: " + detail, response.status_code, quota)
        return response.json()

    async def resolve_hubs(self, session):
        resolved = []
        for kind, query in HUB_SEARCHES:
            record = session.scalar(select(Place).where(Place.search_query == query))
            if record is None:
                campus = session.scalar(select(Place).where(Place.kind == "campus"))
                bias = (campus.latitude, campus.longitude) if campus else None
                places = await self.search_text(query, bias, session)
                place = places[0]
                location = place.get("location") or {}
                if "latitude" not in location or "longitude" not in location or not place.get("id"):
                    raise MapsProviderError("Places API result did not contain a verified place ID and location.")
                name = (place.get("displayName") or {}).get("text", query)
                address = place.get("formattedAddress", "")
                if kind == "campus" and (
                    "srm" not in name.lower()
                    or not any(token in address.lower() for token in ("andhra pradesh", "mangalagiri", "neerukonda"))
                ):
                    raise MapsProviderError("Places API did not verify the SRM University-AP campus address.")
                record = Place(
                    place_id=place["id"],
                    search_query=query,
                    name=name,
                    address=address,
                    kind=kind,
                    latitude=location["latitude"],
                    longitude=location["longitude"],
                )
                session.add(record)
                session.flush()
            resolved.append(record)
        session.commit()
        return resolved

    async def resolve_place_id(self, session, place_id):
        stored = session.get(Place, place_id)
        if stored:
            return stored
        data = await self.details(place_id, session)
        location = data.get("location") or {}
        if not data.get("id") or not location:
            raise MapsProviderError("Place details did not return a verified location.")
        record = Place(
            place_id=data["id"],
            name=(data.get("displayName") or {}).get("text", ""),
            address=data.get("formattedAddress", ""),
            kind="rider_place",
            latitude=location["latitude"],
            longitude=location["longitude"],
        )
        session.add(record)
        session.commit()
        return record

    @staticmethod
    def waypoint(place):
        if isinstance(place, dict) and "place_id" in place:
            return {"placeId": place["place_id"]}
        if isinstance(place, dict) and "latitude" in place:
            return {"location": {"latLng": {
                "latitude": place["latitude"],
                "longitude": place["longitude"],
            }}}
        raise ValueError("A route waypoint needs a verified place ID or GPS location.")

    @staticmethod
    def _cache_key(origin, destination, band):
        token = "%s|%s|%s" % (origin, destination, band)
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _waypoint_key(waypoint):
        if "placeId" in waypoint:
            return waypoint["placeId"]
        point = waypoint["location"]["latLng"]
        return "%.5f,%.5f" % (point["latitude"], point["longitude"])

    async def route_matrix(self, session, origins, destinations, departure_time=None, coordinates=None):
        now = departure_time or utc_now()
        band = int(now.timestamp() // 900)
        results = {}
        missing = []
        for row, origin in enumerate(origins):
            for column, destination in enumerate(destinations):
                key = self._cache_key(self._waypoint_key(origin), self._waypoint_key(destination), band)
                cached = session.get(TravelCache, key)
                if cached and (
                    cached.expires_at.replace(tzinfo=timezone.utc)
                    if cached.expires_at.tzinfo is None else cached.expires_at.astimezone(timezone.utc)
                ) > utc_now():
                    results[(row, column)] = {
                        "minutes": cached.duration_s / 60,
                        "meters": cached.distance_m,
                        "estimated": cached.estimated,
                    }
                else:
                    missing.append((row, column, key))
        if not missing:
            return results

        missing_pairs = {(row, column) for row, column, _ in missing}
        tiles = []
        billable_count = 0
        for row_start in range(0, len(origins), 25):
            for col_start in range(0, len(destinations), 25):
                row_indexes = range(row_start, min(row_start + 25, len(origins)))
                col_indexes = range(col_start, min(col_start + 25, len(destinations)))
                if any((row, column) in missing_pairs for row in row_indexes for column in col_indexes):
                    row_indexes = list(row_indexes)
                    col_indexes = list(col_indexes)
                    tiles.append((row_indexes, col_indexes))
                    billable_count += len(row_indexes) * len(col_indexes)
        today = utc_now().date().isoformat()
        usage = session.get(MapsQuota, today) or MapsQuota(day=today, elements=0, requests=0)
        if usage.elements + billable_count > self.settings.maps_daily_element_limit:
            logger.warning("Configured daily route element limit reached; returning labeled estimates.")
            return self._estimated_pairs(missing, results, origins, destinations, coordinates, session, band)

        try:
            for row_indexes, col_indexes in tiles:
                origin_batch = [origins[index] for index in row_indexes]
                destination_batch = [destinations[index] for index in col_indexes]
                body = {
                    "origins": [{"waypoint": item} for item in origin_batch],
                    "destinations": [{"waypoint": item} for item in destination_batch],
                    "travelMode": "DRIVE",
                    "routingPreference": "TRAFFIC_AWARE_OPTIMAL",
                    "departureTime": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                }
                elements = await self._post(
                    ROUTE_MATRIX_URL,
                    body,
                    "originIndex,destinationIndex,duration,distanceMeters,status,condition",
                )
                for item in elements:
                    if item.get("condition") != "ROUTE_EXISTS":
                        continue
                    row = row_indexes[item["originIndex"]]
                    column = col_indexes[item["destinationIndex"]]
                    key = self._cache_key(
                        self._waypoint_key(origins[row]),
                        self._waypoint_key(destinations[column]),
                        band,
                    )
                    seconds = self._duration_seconds(item.get("duration", "0s"))
                    meters = int(item.get("distanceMeters", 0))
                    cached = TravelCache(
                        cache_key=key,
                        duration_s=seconds,
                        distance_m=meters,
                        estimated=False,
                        expires_at=datetime.fromtimestamp((band + 1) * 900, timezone.utc),
                    )
                    session.merge(cached)
                    results[(row, column)] = {
                        "minutes": seconds / 60,
                        "meters": meters,
                        "estimated": False,
                    }
            self._consume_quota(session, billable_count, len(tiles))
        except MapsProviderError as error:
            if not error.quota:
                raise
            logger.warning("Google Maps quota exhausted; using labeled route estimates.")
            session.rollback()
            return self._estimated_pairs(missing, results, origins, destinations, coordinates, session, band)
        return results

    @staticmethod
    def _duration_seconds(duration):
        value = duration.removesuffix("s")
        return math.ceil(float(value))

    def _estimated_pairs(self, missing, results, origins, destinations, coordinates, session, band):
        if coordinates is None:
            raise MapsProviderError("Route estimates require verified coordinates for every place.")
        for row, column, key in missing:
            origin_key = self._waypoint_key(origins[row])
            destination_key = self._waypoint_key(destinations[column])
            start = coordinates.get(origin_key)
            end = coordinates.get(destination_key)
            if not start or not end:
                raise MapsProviderError("Route estimate requires verified coordinates for each point.")
            meters = int(haversine_km(start[0], start[1], end[0], end[1]) * 1.35 * 1000)
            minutes = meters / 1000 / 25 * 60
            results[(row, column)] = {"minutes": minutes, "meters": meters, "estimated": True}
            session.merge(TravelCache(
                cache_key=key,
                duration_s=round(minutes * 60),
                distance_m=meters,
                estimated=True,
                expires_at=datetime.fromtimestamp((band + 1) * 900, timezone.utc),
            ))
        session.commit()
        return results

    async def compute_route(self, origin, destination, intermediates, departure_time=None, session=None):
        if session is not None:
            self._consume_quota(session, 1)
        body = {
            "origin": {"waypoint": origin},
            "destination": {"waypoint": destination},
            "intermediates": [{"waypoint": point} for point in intermediates],
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_AWARE_OPTIMAL",
            "computeAlternativeRoutes": False,
        }
        if departure_time:
            body["departureTime"] = departure_time.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        data = await self._post(
            COMPUTE_ROUTES_URL,
            body,
            "routes.duration,routes.distanceMeters,routes.legs.duration,routes.legs.distanceMeters,routes.polyline.encodedPolyline",
        )
        routes = data.get("routes", [])
        if not routes:
            raise MapsProviderError("Routes API did not return a drivable route.")
        route = routes[0]
        legs = route.get("legs", [])
        if len(legs) != len(intermediates) + 1:
            raise MapsProviderError("Routes API did not return duration and distance for each stop leg.")
        return {
            "duration_s": self._duration_seconds(route["duration"]),
            "distance_m": int(route.get("distanceMeters", 0)),
            "polyline": route.get("polyline", {}).get("encodedPolyline", ""),
            "legs": [
                {
                    "minutes": self._duration_seconds(leg.get("duration", "0s")) / 60,
                    "meters": int(leg.get("distanceMeters", 0)),
                    "estimated": False,
                }
                for leg in legs
            ],
        }

    async def reverse_geocode(self, latitude, longitude, session=None):
        if session is not None:
            self._consume_quota(session, 1)
        response = await self.client.get(
            GEOCODE_URL,
            params={
                "latlng": "%s,%s" % (latitude, longitude),
                "key": self._key(),
            },
        )
        if response.is_error:
            raise MapsProviderError("Reverse geocoding failed.", response.status_code)
        data = response.json()
        if data.get("status") != "OK" or not data.get("results"):
            raise MapsProviderError("Reverse geocoding did not find an address.")
        return data["results"][0]["formatted_address"]


def haversine_km(lat1, lon1, lat2, lon2):
    """Geodesic estimate: haversine calculation in O(1)."""
    radius = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    value = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def within_service_area(latitude, longitude, campus_latitude, campus_longitude, radius_km):
    return haversine_km(latitude, longitude, campus_latitude, campus_longitude) <= radius_km
