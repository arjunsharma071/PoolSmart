import asyncio
import hmac
import io
import logging
import math
import os
import time
import base64
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import urlencode

import httpx
import qrcode
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal, get_db
from app.maps import GoogleMaps, MapsProviderError, haversine_km, within_service_area
from app.matcher import fare_inr as compute_fare_inr, match_pending
from app.models import (
    AdminEmailAllowlist,
    AuditLog,
    Driver,
    DriverOffer,
    MapsQuota,
    MatchingRun,
    OtpChallenge,
    Payment,
    Place,
    PushSubscription,
    RideRequest,
    SafetyIncident,
    Trip,
    TripStop,
    User,
    utc_now,
)
from app.notifications import send_push, send_sos, send_trip_link_email
from app.schemas import (
    AdminLogin,
    DriverPosition,
    DriverRegister,
    FinishStop,
    OtpRequest,
    OtpStart,
    OtpVerify,
    OfferResponse,
    PaymentCreate,
    PlaceResolve,
    ProfileUpdate,
    PushRegister,
    RideCreate,
    SosCreate,
    WalletTopUp,
)
from app.security import (
    create_access_token,
    create_password_hash,
    current_user,
    decrypt_otp,
    is_allowlisted,
    new_otp,
    otp_digest,
    require_roles,
    send_otp_email,
    verify_password,
)


settings = get_settings()
logger = logging.getLogger("poolsmart")
limiter = Limiter(key_func=get_remote_address, default_limits=["120/minute"])
static_dir = Path(__file__).resolve().parent.parent / "static"
KOLKATA = ZoneInfo("Asia/Kolkata")
matching_lock = asyncio.Lock()


async def run_matching(app):
    async with matching_lock:
        session = SessionLocal()
        try:
            return await match_pending(session, app.state.maps)
        except Exception:
            session.rollback()
            logger.exception("Matching cycle failed.")
            raise
        finally:
            session.close()


@asynccontextmanager
async def lifespan(app):
    if os.getenv("POOLSMART_LOCAL_PREVIEW") == "1":
        app.state.local_preview = True
        yield
        return
    if os.getenv("POOLSMART_MODE") == "demo":
        from app.demo import DemoMaps, seed_demo_data

        seed_demo_data()
        app.state.demo_mode = True
        app.state.maps = DemoMaps()
        scheduler = AsyncIOScheduler(timezone="UTC")
        scheduler.add_job(run_matching, "interval", args=[app], seconds=10)
        scheduler.start()
        app.state.scheduler = scheduler
        try:
            yield
        finally:
            scheduler.shutdown(wait=False)
        return
    missing = settings.missing_runtime_settings()
    if missing:
        raise RuntimeError(settings.setup_instructions(missing))
    maps = GoogleMaps(settings)
    session = SessionLocal()
    try:
        await maps.resolve_hubs(session)
    except Exception:
        session.rollback()
        await maps.close()
        logger.exception("Verified Places could not be loaded.")
        raise
    finally:
        session.close()
    app.state.maps = maps
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(run_matching, "interval", args=[app], seconds=settings.match_interval_seconds)
    scheduler.start()
    app.state.scheduler = scheduler
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        await maps.close()


app = FastAPI(title="PoolSmart", version="1.0.0", lifespan=lifespan)
app.state.limiter = limiter
app.state.local_preview = False
app.state.demo_mode = False
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.middleware("http")
async def security_and_request_log(request: Request, call_next):
    started = time.perf_counter()
    if (
        app.state.local_preview
        and request.url.path.startswith("/api/")
        and request.url.path != "/api/healthz"
    ):
        response = JSONResponse(
            status_code=503,
            content={"detail": "This feature is disabled in the read-only local preview."},
        )
    else:
        response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(self), camera=(), microphone=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://maps.googleapis.com https://maps.gstatic.com https://checkout.razorpay.com 'unsafe-inline'; "
        "style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https://*.googleapis.com https://*.gstatic.com blob:; "
        "connect-src 'self' https://*.googleapis.com https://api.razorpay.com wss:; "
        "frame-src https://*.google.com https://*.googleapis.com https://checkout.razorpay.com"
    )
    logger.info(
        "request method=%s path=%s status=%s elapsed_ms=%.2f",
        request.method, request.url.path, response.status_code,
        (time.perf_counter() - started) * 1000,
    )
    return response


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request, error):
    return Response(
        content='{"detail":"Some fields are invalid. Review the values and try again."}',
        media_type="application/json",
        status_code=422,
    )


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request, error):
    return Response(
        content='{"detail":"That record conflicts with an existing account or request."}',
        media_type="application/json",
        status_code=409,
    )


def _campus(session):
    campus = session.scalar(select(Place).where(Place.kind == "campus"))
    if campus is None:
        raise HTTPException(503, "Verified campus location is not available.")
    return campus


def _as_utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _require_area(session, place):
    campus = _campus(session)
    if not within_service_area(
        place.latitude, place.longitude, campus.latitude, campus.longitude,
        settings.service_area_radius_km,
    ):
        raise HTTPException(
            422,
            "That location is outside the service area. Choose a place in the Guntur–Vijayawada–Amaravati region.",
        )


def _require_verified_user(user):
    if user.verified_at is None or user.consent_at is None:
        raise HTTPException(403, "Verify your university email and accept the privacy notice first.")


async def _planned_departure(request, session, body, pickup, drop):
    if body.scheduled_at and body.leave_by:
        raise HTTPException(422, "Choose either a scheduled departure or an arrival deadline, not both.")
    scheduled_at = body.scheduled_at
    if scheduled_at and _as_utc(scheduled_at) < utc_now() + timedelta(minutes=5):
        raise HTTPException(422, "Scheduled pickup must be at least five minutes in the future.")
    if body.leave_by:
        arrival_deadline = _as_utc(body.leave_by)
        if arrival_deadline < utc_now() + timedelta(minutes=10):
            raise HTTPException(422, "Arrival deadline must be at least ten minutes in the future.")
        try:
            solo_route = await request.app.state.maps.compute_route(
                request.app.state.maps.waypoint({"place_id": pickup.place_id}),
                request.app.state.maps.waypoint({"place_id": drop.place_id}),
                [],
                departure_time=arrival_deadline,
                session=session,
            )
        except MapsProviderError as error:
            raise HTTPException(503, "A live route is required to plan an arrival deadline.") from error
        scheduled_at = arrival_deadline - timedelta(seconds=solo_route["duration_s"])
        if scheduled_at < utc_now() + timedelta(minutes=5):
            raise HTTPException(422, "Not enough time remains to schedule a feasible departure.")
    return scheduled_at


def _require_night_contact(user, scheduled_at):
    service_time = scheduled_at.astimezone(KOLKATA) if scheduled_at else utc_now().astimezone(KOLKATA)
    if (service_time.hour >= 21 or service_time.hour < 5) and not any(
        contact.get("email") for contact in (user.emergency_contacts or [])
    ):
        raise HTTPException(
            422,
            "Add an emergency contact email before requesting a trip after 9 PM or before 5 AM.",
        )


def _get_driver(session, user):
    driver = session.scalar(select(Driver).where(Driver.user_id == user.id))
    if driver is None:
        raise HTTPException(403, "Complete driver onboarding before using driver tools.")
    return driver


def _estimated_solo_metrics(pickup, drop, at_time):
    km = haversine_km(pickup.latitude, pickup.longitude, drop.latitude, drop.longitude) * 1.25
    distance_m = round(km * 1000)
    duration_s = round(max(30.0, km / 24 * 3600)) if distance_m else 0
    hour = (_as_utc(at_time) or utc_now()).astimezone(KOLKATA).hour
    return distance_m, duration_s, compute_fare_inr(distance_m, duration_s, hour)


def _ride_record(session, ride):
    pickup = session.get(Place, ride.pickup_place_id)
    drop = session.get(Place, ride.drop_place_id)
    trip = session.get(Trip, ride.trip_id) if ride.trip_id else None
    payment = session.scalar(select(Payment).where(Payment.ride_id == ride.id))
    solo_m, _, solo_fare = _estimated_solo_metrics(pickup, drop, ride.scheduled_at or ride.requested_at)
    co_rider_count = (
        session.scalar(select(func.count(RideRequest.id)).where(RideRequest.trip_id == trip.id))
        if trip else 1
    ) or 1
    savings_inr = max(0.0, round(solo_fare - ride.fare_inr, 2)) if ride.fare_inr is not None else None
    savings_percent = (
        round(savings_inr / solo_fare * 100, 1)
        if savings_inr is not None and solo_fare > 0 else None
    )
    co2_saved_kg = round(solo_m / 1000 * 0.12, 2) if trip and co_rider_count > 1 else 0.0
    return {
        "id": ride.id,
        "status": ride.status,
        "pickup": {
            "place_id": pickup.place_id,
            "name": pickup.name,
            "address": pickup.address,
            "latitude": pickup.latitude,
            "longitude": pickup.longitude,
        },
        "drop": {
            "place_id": drop.place_id,
            "name": drop.name,
            "address": drop.address,
            "latitude": drop.latitude,
            "longitude": drop.longitude,
        },
        "seats": ride.seats,
        "max_wait_minutes": ride.max_wait_minutes,
        "max_detour": ride.max_detour,
        "women_only": ride.women_only,
        "same_gender": ride.same_gender,
        "scheduled_at": _as_utc(ride.scheduled_at),
        "leave_by": _as_utc(ride.leave_by),
        "promised_pickup_at": _as_utc(ride.promised_pickup_at),
        "solo_fare_inr": solo_fare,
        "fare_inr": ride.fare_inr,
        "savings_inr": savings_inr,
        "savings_percent": savings_percent,
        "co2_saved_kg": co2_saved_kg,
        "co_rider_count": co_rider_count,
        "estimated": trip.estimated if trip else False,
        "trip_id": ride.trip_id,
        "payment": ({
            "method": payment.method,
            "state": payment.state,
        } if payment else None),
    }


def _trip_for_rider(session, trip_id, user):
    ride = session.scalar(
        select(RideRequest).where(
            RideRequest.trip_id == trip_id,
            RideRequest.rider_id == user.id,
        )
    )
    if ride is None:
        raise HTTPException(404, "Trip not found.")
    return ride, session.get(Trip, trip_id)


def _trip_view(session, ride, trip):
    driver = session.get(Driver, trip.driver_id)
    driver_user = session.get(User, driver.user_id)
    rides = list(session.scalars(select(RideRequest).where(RideRequest.trip_id == trip.id)))
    riders = []
    for member in rides:
        user = session.get(User, member.rider_id)
        pickup = session.get(Place, member.pickup_place_id)
        drop = session.get(Place, member.drop_place_id)
        _, _, member_solo_fare = _estimated_solo_metrics(pickup, drop, member.scheduled_at or member.requested_at)
        riders.append({
            "ride_id": member.id,
            "first_name": user.name.split(" ")[0],
            "pickup_area": pickup.name,
            "drop_area": drop.name,
            "fare_inr": member.fare_inr,
            "solo_fare_inr": member_solo_fare,
            "seats": member.seats,
        })
    stops = list(session.scalars(
        select(TripStop).where(TripStop.trip_id == trip.id).order_by(TripStop.sequence)
    ))
    return {
        "id": trip.id,
        "state": trip.state,
        "driver": {
            "first_name": driver_user.name.split(" ")[0],
            "vehicle_number": driver.vehicle_number,
            "vehicle_model": driver.vehicle_model,
            "rating": driver.rating,
        },
        "driver_location": (
            {"latitude": driver.latitude, "longitude": driver.longitude}
            if driver.latitude is not None and driver.longitude is not None else None
        ),
        "riders": riders,
        "stops": [{
            "id": stop.id,
            "sequence": stop.sequence,
            "ride_id": stop.ride_id,
            "action": stop.action,
            "place": (place := session.get(Place, stop.place_id)).name,
            "latitude": place.latitude,
            "longitude": place.longitude,
            "eta_at": _as_utc(stop.eta_at),
            "completed": stop.completed_at is not None,
        } for stop in stops],
        "polyline": trip.encoded_polyline,
        "eta_at": _as_utc(trip.eta_at),
        "distance_m": trip.total_distance_m,
        "duration_s": trip.total_duration_s,
        "fare_inr": ride.fare_inr,
        "fare_total_inr": trip.fare_total_inr,
        "estimated": trip.estimated,
    }


@app.get("/", response_class=HTMLResponse)
@app.get("/ride", response_class=HTMLResponse)
@app.get("/pools", response_class=HTMLResponse)
@app.get("/driver", response_class=HTMLResponse)
@app.get("/account", response_class=HTMLResponse)
async def home():
    return HTMLResponse((static_dir / "index.html").read_text(encoding="utf-8"))


@app.get("/api/network/stats")
async def network_stats(session: Session = Depends(get_db)):
    places_count = session.scalar(select(func.count(Place.place_id))) or 0
    active_drivers = session.scalar(select(func.count(Driver.id)).where(
        Driver.online.is_(True), Driver.approval_status == "approved"
    )) or 0
    pending_rides = session.scalar(select(func.count(RideRequest.id)).where(
        RideRequest.status == "pending",
        RideRequest.requested_at >= utc_now() - timedelta(minutes=20),
    )) or 0
    completed_trips = session.scalar(select(func.count(Trip.id)).where(
        Trip.state == "completed"
    )) or 0
    solo_sum = session.scalar(select(func.sum(MatchingRun.solo_cost_m))) or 0.0
    exact_sum = session.scalar(select(func.sum(MatchingRun.exact_cost_m))) or 0.0
    distance_saved_km = round(max(0.0, solo_sum - exact_sum) / 1000, 2)
    return {
        "places_count": places_count,
        "active_drivers": active_drivers,
        "open_requests_count": pending_rides,
        "completed_trips": completed_trips,
        "distance_saved_km": distance_saved_km,
    }


@app.get("/manifest.webmanifest")
async def manifest():
    return Response(
        (static_dir / "manifest.webmanifest").read_text(encoding="utf-8"),
        media_type="application/manifest+json",
    )


@app.get("/sw.js")
async def service_worker():
    return Response(
        (static_dir / "sw.js").read_text(encoding="utf-8"),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )


@app.get("/api/healthz")
async def health():
    if app.state.local_preview:
        return {
            "status": "preview", "mode": "preview", "maps": "disabled",
            "local_preview": True, "demo_mode": False,
        }
    if app.state.demo_mode:
        return {
            "status": "ready", "mode": "demo", "maps": "simulated",
            "local_preview": False, "demo_mode": True,
        }
    return {
        "status": "ready", "mode": "live", "maps": "connected",
        "local_preview": False, "demo_mode": False,
    }


@app.get("/api/map/config")
async def map_config():
    if app.state.demo_mode:
        return {
            "mode": "demo",
            "demo_mode": True,
            "browser_key": "",
            "map_id": "",
            "vapid_public_key": "",
            "service_area_radius_km": settings.service_area_radius_km,
        }
    return {
        "mode": "live",
        "demo_mode": False,
        "browser_key": settings.google_maps_browser_key.get_secret_value(),
        "map_id": settings.google_maps_map_id,
        "vapid_public_key": settings.vapid_public_key,
        "service_area_radius_km": settings.service_area_radius_km,
    }


@app.get("/api/places/hubs")
async def place_hubs(session: Session = Depends(get_db)):
    places = list(session.scalars(select(Place).order_by(Place.kind, Place.name)))
    return {"places": [{
        "place_id": place.place_id,
        "name": place.name,
        "address": place.address,
        "kind": place.kind,
        "latitude": place.latitude,
        "longitude": place.longitude,
    } for place in places]}


@app.get("/api/weather")
async def local_weather(session: Session = Depends(get_db)):
    if app.state.demo_mode:
        return {
            "rain_expected": False,
            "buffer_minutes": 0,
            "notice": "Demo weather: clear conditions (simulated; not a forecast).",
            "source": "PoolSmart local simulator",
            "simulated": True,
        }
    campus = _campus(session)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": campus.latitude,
                    "longitude": campus.longitude,
                    "current": "precipitation",
                    "hourly": "precipitation_probability",
                    "forecast_days": 1,
                    "timezone": "Asia/Kolkata",
                },
            )
    except httpx.RequestError as error:
        raise HTTPException(503, "Weather data is unavailable right now.") from error
    if response.is_error:
        raise HTTPException(503, "Weather data is unavailable right now.")
    weather = response.json()
    current_precipitation = weather.get("current", {}).get("precipitation", 0) or 0
    probabilities = weather.get("hourly", {}).get("precipitation_probability", [])
    hourly_times = weather.get("hourly", {}).get("time", [])
    now_hour = datetime.now(KOLKATA).strftime("%Y-%m-%dT%H:00")
    next_hour = next(
        (index for index, value in enumerate(hourly_times) if value >= now_hour),
        0,
    )
    upcoming = probabilities[next_hour:next_hour + 4]
    rain = current_precipitation > 0 or any(value >= 50 for value in upcoming)
    return {
        "rain_expected": rain,
        "buffer_minutes": settings.weather_buffer_minutes if rain else 0,
        "notice": (
            "Rain is forecast. Allow %d extra minutes for your trip." % settings.weather_buffer_minutes
            if rain else "No rain buffer is currently advised."
        ),
        "source": "Open-Meteo",
    }


@app.get("/api/places/suggest")
@limiter.limit("20/minute")
async def place_suggestions(request: Request, q: str, session: Session = Depends(get_db)):
    query = q.strip()
    if len(query) < 3 or len(query) > 160:
        raise HTTPException(422, "Enter at least three characters for a place search.")
    campus = _campus(session)
    try:
        places = await request.app.state.maps.search_text(
            query, (campus.latitude, campus.longitude), session
        )
    except MapsProviderError as error:
        raise HTTPException(503, "Place search is temporarily unavailable. Try again shortly.") from error
    matches = []
    for place in places:
        location = place.get("location") or {}
        if not location:
            continue
        if not within_service_area(
            location["latitude"], location["longitude"], campus.latitude, campus.longitude,
            settings.service_area_radius_km,
        ):
            continue
        matches.append({
            "place_id": place.get("id"),
            "name": (place.get("displayName") or {}).get("text", ""),
            "address": place.get("formattedAddress", ""),
            "latitude": location["latitude"],
            "longitude": location["longitude"],
        })
    return {"places": matches}


@app.post("/api/places/resolve")
@limiter.limit("30/minute")
async def resolve_place(
    request: Request,
    body: PlaceResolve,
    session: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    _require_verified_user(user)
    try:
        place = await request.app.state.maps.resolve_place_id(session, body.place_id)
    except MapsProviderError as error:
        raise HTTPException(422, "That place could not be verified by Google Maps.") from error
    _require_area(session, place)
    return {
        "place_id": place.place_id, "name": place.name, "address": place.address,
        "latitude": place.latitude, "longitude": place.longitude,
    }


@app.get("/api/graph")
@limiter.limit("4/minute")
async def live_graph(
    request: Request,
    algorithm: str = "dijkstra",
    source: str | None = None,
    session: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
):
    if algorithm not in ("dijkstra", "floyd_warshall"):
        raise HTTPException(422, "Choose dijkstra or floyd_warshall.")
    places = list(session.scalars(select(Place)))
    if not places:
        raise HTTPException(503, "Verified place data is not available.")
    keys = [place.place_id for place in places]
    key_to_place = {place.place_id: place for place in places}
    waypoints = [request.app.state.maps.waypoint({"place_id": key}) for key in keys]
    coordinates = {place.place_id: (place.latitude, place.longitude) for place in places}
    raw = await request.app.state.maps.route_matrix(
        session, waypoints, waypoints, coordinates=coordinates
    )
    graph = {key: [] for key in keys}
    estimated = False
    for row, origin in enumerate(keys):
        for column, destination in enumerate(keys):
            leg = raw.get((row, column))
            if leg:
                graph[origin].append((destination, leg["minutes"]))
                estimated = estimated or leg["estimated"]
    from app.algorithms import dijkstra, floyd_warshall
    if algorithm == "floyd_warshall":
        paths = {
            origin: {
                destination: (value if math.isfinite(value) else None)
                for destination, value in row.items()
            }
            for origin, row in floyd_warshall(graph).items()
        }
    else:
        if source not in graph:
            source = next((place.place_id for place in places if place.kind == "campus"), keys[0])
        paths, _ = dijkstra(graph, source)
        paths = {
            source: {
                destination: (value if math.isfinite(value) else None)
                for destination, value in paths.items()
            }
        }
    return {
        "algorithm": algorithm,
        "source": source,
        "estimated": estimated,
        "places": [{"place_id": item.place_id, "name": item.name} for item in places],
        "minutes": paths,
    }


@app.post("/api/auth/request-otp")
@limiter.limit("4/hour")
async def request_email_otp(
    request: Request,
    body: OtpRequest,
    session: Session = Depends(get_db),
):
    if request.app.state.demo_mode:
        raise HTTPException(409, "Email delivery is disabled in local demo. Use a demo account below.")
    email = body.email.lower()
    domain = email.rpartition("@")[2]
    if domain != "srmap.edu.in" and not is_allowlisted(session, email):
        raise HTTPException(403, "Use your @srmap.edu.in email or ask an administrator for access.")
    recent = session.scalar(
        select(OtpChallenge).where(
            OtpChallenge.email == email,
            OtpChallenge.created_at >= utc_now() - timedelta(seconds=60),
        ).order_by(OtpChallenge.created_at.desc())
    )
    if recent:
        raise HTTPException(429, "Wait one minute before requesting another sign-in code.")
    code = new_otp()
    challenge = OtpChallenge(
        email=email,
        digest=otp_digest(email, code),
        expires_at=utc_now() + timedelta(minutes=10),
    )
    session.add(challenge)
    session.commit()
    try:
        await asyncio.to_thread(send_otp_email, email, code)
    except Exception as error:
        session.delete(challenge)
        session.commit()
        logger.warning("Email delivery failed for sign-in challenge %s.", challenge.id)
        raise HTTPException(503, "Email delivery failed. Check your university address or try later.") from error
    return {"sent": True, "expires_in_seconds": 600}


@app.post("/api/auth/verify-otp")
@limiter.limit("8/minute")
async def verify_email_otp(
    request: Request,
    body: OtpVerify,
    session: Session = Depends(get_db),
):
    if request.app.state.demo_mode:
        raise HTTPException(409, "Email verification is disabled in local demo. Sign in with a demo account.")
    if not body.consent:
        raise HTTPException(422, "Accept the privacy notice before continuing.")
    email = body.email.strip().lower()
    challenge = session.scalar(
        select(OtpChallenge).where(
            OtpChallenge.email == email,
            OtpChallenge.consumed_at.is_(None),
        ).order_by(OtpChallenge.created_at.desc())
    )
    if (
        challenge is None
        or _as_utc(challenge.expires_at) <= utc_now()
        or challenge.attempts >= 5
    ):
        raise HTTPException(400, "The sign-in code expired. Request a new one.")
    if not hmac.compare_digest(challenge.digest, otp_digest(email, body.code)):
        challenge.attempts += 1
        session.commit()
        raise HTTPException(400, "That code is not valid. Check it and try again.")
    challenge.consumed_at = utc_now()
    user = session.scalar(select(User).where(User.email == email))
    allowlist = session.get(AdminEmailAllowlist, email)
    if user is None:
        user = User(
            email=email,
            name=body.name.strip(),
            role=allowlist.role if allowlist else "rider",
            verified_at=utc_now(),
            consent_at=utc_now(),
        )
        session.add(user)
    else:
        user.name = body.name.strip() or user.name
        user.verified_at = utc_now()
        user.consent_at = utc_now()
    session.commit()
    return {
        "access_token": create_access_token(user),
        "token_type": "bearer",
        "user": {"id": user.id, "name": user.name, "role": user.role},
    }


@app.post("/api/auth/admin-login")
@limiter.limit("5/minute")
async def admin_login(
    request: Request,
    body: AdminLogin,
    session: Session = Depends(get_db),
):
    user = session.scalar(select(User).where(User.email == body.email.strip().lower()))
    if (
        user is None or user.role != "admin" or user.suspended
        or not verify_password(body.password, user.password_hash)
    ):
        raise HTTPException(401, "The administrator credentials are not valid.")
    return {"access_token": create_access_token(user), "token_type": "bearer"}


@app.post("/api/auth/demo-login")
@limiter.limit("10/minute")
async def demo_login(
    request: Request,
    body: AdminLogin,
    session: Session = Depends(get_db),
):
    if not request.app.state.demo_mode:
        raise HTTPException(404, "Demo sign-in is available only in local demo mode.")
    email = body.email.strip().lower()
    user = session.scalar(select(User).where(User.email == email))
    if (
        user is None
        or not email.endswith("@poolsmart.demo")
        or user.suspended
        or not verify_password(body.password, user.password_hash)
    ):
        raise HTTPException(401, "Demo account or password is not valid.")
    return {
        "access_token": create_access_token(user),
        "token_type": "bearer",
        "user": {"id": user.id, "name": user.name, "role": user.role},
    }


@app.get("/api/me")
async def me(user: User = Depends(current_user)):
    return {
        "id": user.id, "email": user.email, "name": user.name, "role": user.role,
        "gender": user.gender, "women_only": user.women_only, "same_gender": user.same_gender,
        "gender_verified": user.gender_verified,
        "phone": user.phone, "accessibility_need": user.accessibility_need,
        "emergency_contacts": user.emergency_contacts or [],
        "wallet_balance_inr": round(user.wallet_balance_inr or 0.0, 2),
    }


@app.post("/api/me/wallet/topup")
async def wallet_topup(
    request: Request,
    body: WalletTopUp,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
):
    if not request.app.state.demo_mode:
        raise HTTPException(403, "Demo wallet top-up is only available in local demo mode.")
    user.wallet_balance_inr = round((user.wallet_balance_inr or 0.0) + body.amount_inr, 2)
    session.commit()
    return {"wallet_balance_inr": user.wallet_balance_inr, "simulated": True}


@app.patch("/api/me")
async def update_profile(
    body: ProfileUpdate,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
):
    if body.gender != user.gender:
        user.gender_verified = False
    for key, value in body.model_dump().items():
        setattr(user, key, value)
    session.commit()
    return {"saved": True}


@app.get("/api/me/export")
async def export_account(user: User = Depends(current_user), session: Session = Depends(get_db)):
    rides = list(session.scalars(select(RideRequest).where(RideRequest.rider_id == user.id)))
    payments = list(session.scalars(select(Payment).where(Payment.rider_id == user.id)))
    incidents = list(session.scalars(
        select(SafetyIncident).where(SafetyIncident.user_id == user.id)
    ))
    driver = session.scalar(select(Driver).where(Driver.user_id == user.id))
    return {
        "profile": {
            "email": user.email,
            "name": user.name,
            "phone": user.phone,
            "gender": user.gender,
            "gender_verified": user.gender_verified,
            "preferences": {
                "women_only": user.women_only,
                "same_gender": user.same_gender,
                "accessibility_need": user.accessibility_need,
            },
            "emergency_contacts": user.emergency_contacts,
            "wallet_balance_inr": round(user.wallet_balance_inr or 0.0, 2),
            "consent_at": user.consent_at,
        },
        "rides": [_ride_record(session, ride) for ride in rides],
        "payments": [{
            "id": item.id, "amount_inr": item.amount_inr, "method": item.method,
            "state": item.state, "created_at": item.created_at,
        } for item in payments],
        "safety_incidents": [{
            "id": item.id,
            "ride_id": item.ride_id,
            "latitude": item.latitude,
            "longitude": item.longitude,
            "details": item.details,
            "created_at": item.created_at,
            "notified_at": item.notified_at,
        } for item in incidents],
        "driver_profile": ({
            "licence_number": driver.licence_number,
            "vehicle_number": driver.vehicle_number,
            "vehicle_model": driver.vehicle_model,
            "seat_capacity": driver.seat_capacity,
            "insurance_expiry": driver.insurance_expiry,
            "approval_status": driver.approval_status,
            "online": driver.online,
        } if driver else None),
    }


@app.delete("/api/me")
async def delete_account(
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
):
    active_ride = session.scalar(select(RideRequest).where(
        RideRequest.rider_id == user.id,
        RideRequest.status.in_(("offered", "confirmed", "in_progress")),
    ))
    if active_ride:
        raise HTTPException(409, "Finish or cancel active trips before deleting this account.")
    user.email = "removed+" + user.id + "@invalid.invalid"
    user.name = "Removed account"
    user.phone = ""
    user.gender = "unspecified"
    user.gender_verified = False
    user.women_only = False
    user.same_gender = False
    user.accessibility_need = ""
    user.emergency_contacts = []
    user.verified_at = None
    user.consent_at = None
    user.suspended = True
    session.query(RideRequest).filter(
        RideRequest.rider_id == user.id,
        RideRequest.status == "pending",
    ).update({"status": "cancelled"})
    driver = session.scalar(select(Driver).where(Driver.user_id == user.id))
    if driver:
        driver.online = False
        driver.approval_status = "suspended"
        driver.licence_number = "removed-" + driver.id
        driver.vehicle_number = "REMOVED-" + driver.id[:8]
        driver.vehicle_model = "Removed"
        driver.photo_url = ""
        driver.latitude = None
        driver.longitude = None
        driver.location_at = None
    session.query(PushSubscription).filter(PushSubscription.user_id == user.id).delete()
    session.add(AuditLog(actor_id=user.id, action="account_deleted", entity_id=user.id))
    session.commit()
    return {"deleted": True}


@app.post("/api/drivers/apply")
async def driver_apply(
    body: DriverRegister,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
):
    _require_verified_user(user)
    if body.insurance_expiry.date() < datetime.now(timezone.utc).date():
        raise HTTPException(422, "Insurance must be current.")
    driver = session.scalar(select(Driver).where(Driver.user_id == user.id))
    if driver is None:
        driver = Driver(user_id=user.id, licence_number=body.licence_number, vehicle_number=body.vehicle_number,
                        vehicle_model=body.vehicle_model, seat_capacity=body.seat_capacity,
                        photo_url=body.photo_url, insurance_expiry=body.insurance_expiry)
    else:
        if driver.approval_status == "approved":
            raise HTTPException(409, "An approved driver profile cannot be replaced here.")
        driver.licence_number = body.licence_number
        driver.vehicle_number = body.vehicle_number
        driver.vehicle_model = body.vehicle_model
        driver.seat_capacity = body.seat_capacity
        driver.photo_url = body.photo_url
        driver.insurance_expiry = body.insurance_expiry
    user.name = body.name.strip()
    user.phone = body.phone.strip()
    session.add_all([user, driver])
    session.commit()
    return {"status": "pending", "message": "Your documents are waiting for administrator review."}


@app.get("/api/drivers/status")
async def driver_status(user: User = Depends(current_user), session: Session = Depends(get_db)):
    driver = _get_driver(session, user)
    active_trip = session.scalar(select(Trip).where(
        Trip.driver_id == driver.id,
        Trip.state.in_(("accepted", "started")),
    ))
    unsettled_trip_id = None
    if active_trip is None:
        recent_completed = list(session.scalars(
            select(Trip).where(
                Trip.driver_id == driver.id,
                Trip.state == "completed",
            ).order_by(Trip.completed_at.desc()).limit(5)
        ))
        for completed_trip in recent_completed:
            trip_rides = list(session.scalars(
                select(RideRequest).where(RideRequest.trip_id == completed_trip.id)
            ))
            for ride in trip_rides:
                payment = session.scalar(select(Payment).where(Payment.ride_id == ride.id))
                if payment is None or payment.state == "due":
                    unsettled_trip_id = completed_trip.id
                    break
            if unsettled_trip_id:
                break
    completed_trips = list(session.scalars(
        select(Trip).where(Trip.driver_id == driver.id, Trip.state == "completed")
    ))
    earnings_total_inr = 0.0
    pending_cash_inr = 0.0
    for completed_trip in completed_trips:
        trip_rides = list(session.scalars(
            select(RideRequest).where(RideRequest.trip_id == completed_trip.id)
        ))
        for ride in trip_rides:
            fare = ride.fare_inr or 0.0
            net = round(fare * (1 - settings.fare_commission_percent / 100), 2)
            payment = session.scalar(select(Payment).where(Payment.ride_id == ride.id))
            if payment and payment.state == "paid":
                earnings_total_inr = round(earnings_total_inr + net, 2)
            elif payment is None or payment.state == "due":
                pending_cash_inr = round(pending_cash_inr + fare, 2)
    return {
        "online": driver.online,
        "approval_status": driver.approval_status,
        "vehicle_model": driver.vehicle_model,
        "vehicle_number": driver.vehicle_number,
        "seat_capacity": driver.seat_capacity,
        "rating": driver.rating,
        "location_at": _as_utc(driver.location_at),
        "insurance_expiry": _as_utc(driver.insurance_expiry),
        "active_trip_id": active_trip.id if active_trip else None,
        "unsettled_trip_id": unsettled_trip_id,
        "earnings_total_inr": earnings_total_inr,
        "pending_cash_inr": pending_cash_inr,
        "completed_trips_count": len(completed_trips),
    }


@app.post("/api/drivers/online")
async def driver_online(
    request: Request,
    body: DriverPosition,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
):
    driver = _get_driver(session, user)
    if driver.approval_status != "approved":
        raise HTTPException(403, "Only an approved driver can go online.")
    if driver.insurance_expiry and _as_utc(driver.insurance_expiry) < utc_now():
        raise HTTPException(403, "Your vehicle insurance has expired. Update it before going online.")
    campus = _campus(session)
    if body.online and not within_service_area(
        body.latitude, body.longitude, campus.latitude, campus.longitude, settings.service_area_radius_km
    ):
        raise HTTPException(422, "Your GPS location is outside the active service area.")
    driver.latitude = body.latitude
    driver.longitude = body.longitude
    driver.location_at = utc_now()
    driver.online = body.online
    session.commit()
    if body.online:
        await run_matching(request.app)
    return {"online": driver.online, "location_at": _as_utc(driver.location_at)}


@app.post("/api/rides")
@limiter.limit("8/minute")
async def create_ride(
    request: Request,
    body: RideCreate,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
):
    _require_verified_user(user)
    if user.suspended:
        raise HTTPException(403, "This account cannot request a ride.")
    if body.women_only and user.gender != "female":
        raise HTTPException(422, "Women-only pools are available to verified female riders.")
    if (body.women_only or body.same_gender) and not user.gender_verified:
        raise HTTPException(403, "Ask an administrator to verify your gender before requesting a protected pool.")
    if body.same_gender and user.gender == "unspecified":
        raise HTTPException(422, "Choose a gender preference in your profile before requesting a same-gender pool.")
    try:
        pickup = await request.app.state.maps.resolve_place_id(session, body.pickup_place_id)
        drop = await request.app.state.maps.resolve_place_id(session, body.drop_place_id)
    except MapsProviderError as error:
        raise HTTPException(422, "Select a pickup and drop-off verified by Places.") from error
    if pickup.place_id == drop.place_id:
        raise HTTPException(422, "Pickup and drop-off must be different places.")
    _require_area(session, pickup)
    _require_area(session, drop)
    scheduled_at = await _planned_departure(request, session, body, pickup, drop)
    _require_night_contact(user, scheduled_at)
    ride = RideRequest(
        rider_id=user.id,
        pickup_place_id=pickup.place_id,
        drop_place_id=drop.place_id,
        scheduled_at=scheduled_at,
        leave_by=body.leave_by,
        seats=body.seats,
        luggage_count=body.luggage_count,
        max_wait_minutes=body.max_wait_minutes,
        max_detour=body.max_detour,
        budget_inr=body.budget_inr,
        women_only=body.women_only,
        same_gender=body.same_gender,
        status="pending",
    )
    session.add(ride)
    session.commit()
    await run_matching(request.app)
    session.refresh(ride)
    return _ride_record(session, ride)


@app.get("/api/rides")
async def my_rides(user: User = Depends(current_user), session: Session = Depends(get_db)):
    rides = list(session.scalars(
        select(RideRequest).where(RideRequest.rider_id == user.id).order_by(RideRequest.created_at.desc()).limit(100)
    ))
    return {"rides": [_ride_record(session, ride) for ride in rides]}


@app.get("/api/pools/open")
async def open_pools(
    request: Request,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
):
    pending = list(session.scalars(
        select(RideRequest).where(
            RideRequest.status == "pending",
            RideRequest.rider_id != user.id,
            RideRequest.requested_at >= utc_now() - timedelta(minutes=20),
        ).order_by(RideRequest.requested_at).limit(80)
    ))
    grouped = {}
    for ride in pending:
        grouped.setdefault(ride.drop_place_id, []).append(ride)
    result = []
    for drop_id, rides in grouped.items():
        if any(ride.women_only for ride in rides) and (
            user.gender != "female" or not user.gender_verified
        ):
            continue
        member_users = [session.get(User, ride.rider_id) for ride in rides]
        member_genders = {member.gender for member in member_users}
        if (
            (user.same_gender or any(ride.same_gender for ride in rides))
            and (
                user.gender == "unspecified"
                or not user.gender_verified
                or member_genders != {user.gender}
            )
        ):
            continue
        seats = sum(ride.seats for ride in rides)
        if seats >= 4:
            continue
        place = session.get(Place, drop_id)
        pickup_places = [session.get(Place, ride.pickup_place_id) for ride in rides]
        pickup_names = list(dict.fromkeys(item.name for item in pickup_places if item))
        _, _, solo_fare = _estimated_solo_metrics(pickup_places[0], place, rides[0].scheduled_at or rides[0].requested_at)
        pooled_share = round(solo_fare / (len(rides) + 1) * 1.1, 2)
        safety_badge = (
            "women_only" if any(ride.women_only for ride in rides)
            else "same_gender" if any(ride.same_gender for ride in rides)
            else "open"
        )
        detour_percent = round((min(ride.max_detour for ride in rides) - 1.0) * 100)
        result.append({
            "pool_id": rides[0].id,
            "drop_place_id": drop_id,
            "destination": place.name,
            "destination_area": place.address,
            "pickup_names": pickup_names,
            "seats_left": 4 - seats,
            "rider_count": len(rides),
            "safety_badge": safety_badge,
            "estimated_detour_percent": detour_percent,
            "estimated_solo_fare_inr": solo_fare,
            "estimated_pooled_fare_inr": pooled_share,
            "first_request_at": _as_utc(min(ride.requested_at for ride in rides)),
            "fare_notice": (
                "Fare uses simulated route distances."
                if request.app.state.demo_mode else "Fare is shown after live routing."
            ),
        })
    return {"pools": result}


@app.post("/api/pools/{pool_id}/join")
async def join_open_pool(
    pool_id: str,
    request: Request,
    body: RideCreate,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
):
    _require_verified_user(user)
    host = session.get(RideRequest, pool_id)
    if host is None or host.status != "pending" or host.rider_id == user.id:
        raise HTTPException(404, "That departure is no longer forming.")
    host_requested_at = _as_utc(host.requested_at)
    if host_requested_at < utc_now() - timedelta(minutes=20):
        raise HTTPException(404, "That departure is no longer forming.")
    if body.drop_place_id != host.drop_place_id:
        raise HTTPException(422, "Pool destination changed. Refresh the departures list.")
    members = list(session.scalars(select(RideRequest).where(
        RideRequest.drop_place_id == host.drop_place_id,
        RideRequest.status == "pending",
        RideRequest.requested_at >= host_requested_at - timedelta(minutes=15),
    )))
    if sum(ride.seats for ride in members) + body.seats > 4:
        raise HTTPException(409, "That departure no longer has enough seats.")
    if body.women_only and user.gender != "female":
        raise HTTPException(422, "Women-only pools are available to verified female riders.")
    if (body.women_only or body.same_gender) and not user.gender_verified:
        raise HTTPException(403, "Ask an administrator to verify your gender before joining this protected pool.")
    group_users = [session.get(User, ride.rider_id) for ride in members]
    all_genders = {member.gender for member in group_users} | {user.gender}
    if any(ride.women_only for ride in members) and (
        user.gender != "female" or not user.gender_verified
    ):
        raise HTTPException(409, "That forming pool is restricted to verified female riders.")
    if (
        (body.same_gender or any(ride.same_gender for ride in members))
        and (len(all_genders) != 1 or "unspecified" in all_genders)
    ):
        raise HTTPException(409, "That forming pool does not meet the same-gender preference.")
    try:
        pickup = await request.app.state.maps.resolve_place_id(session, body.pickup_place_id)
    except MapsProviderError as error:
        raise HTTPException(422, "Select a pickup verified by Places.") from error
    if pickup.place_id == host.drop_place_id:
        raise HTTPException(422, "Pickup and destination must be different places.")
    _require_area(session, pickup)
    _require_area(session, session.get(Place, host.drop_place_id))
    scheduled_at = await _planned_departure(
        request, session, body, pickup, session.get(Place, host.drop_place_id)
    )
    _require_night_contact(user, scheduled_at)
    ride = RideRequest(
        rider_id=user.id,
        pickup_place_id=pickup.place_id,
        drop_place_id=host.drop_place_id,
        seats=body.seats,
        luggage_count=body.luggage_count,
        max_wait_minutes=body.max_wait_minutes,
        max_detour=body.max_detour,
        budget_inr=body.budget_inr,
        women_only=body.women_only,
        same_gender=body.same_gender,
        scheduled_at=scheduled_at,
        leave_by=body.leave_by,
        status="pending",
    )
    session.add(ride)
    session.commit()
    await run_matching(request.app)
    session.refresh(ride)
    return _ride_record(session, ride)


@app.delete("/api/rides/{ride_id}")
async def cancel_ride(
    ride_id: str,
    request: Request,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
):
    ride = session.scalar(select(RideRequest).where(RideRequest.id == ride_id, RideRequest.rider_id == user.id))
    if ride is None:
        raise HTTPException(404, "Ride request not found.")
    if ride.status not in ("pending", "offered"):
        raise HTTPException(409, "A confirmed ride needs administrator assistance to cancel.")
    if ride.trip_id:
        trip = session.get(Trip, ride.trip_id)
        if trip and trip.state == "offered":
            trip.state = "cancelled"
            session.query(DriverOffer).filter(
                DriverOffer.trip_id == trip.id,
                DriverOffer.state == "pending",
            ).update({"state": "cancelled", "responded_at": utc_now()})
            for member in session.scalars(select(RideRequest).where(RideRequest.trip_id == trip.id)):
                member.status = "pending" if member.id != ride.id else "cancelled"
                member.trip_id = None if member.id != ride.id else trip.id
                if member.id != ride.id:
                    member.fare_inr = None
    ride.status = "cancelled"
    session.commit()
    await run_matching(request.app)
    return {"cancelled": True}


@app.get("/api/driver/offers")
async def driver_offers(user: User = Depends(current_user), session: Session = Depends(get_db)):
    driver = session.scalar(select(Driver).where(Driver.user_id == user.id))
    if driver is None:
        return {"offers": [], "application_status": "not_applied"}
    offers = list(session.scalars(
        select(DriverOffer).where(
            DriverOffer.driver_id == driver.id,
            DriverOffer.state == "pending",
            DriverOffer.expires_at > utc_now(),
        )
    ))
    result = []
    for offer in offers:
        trip = session.get(Trip, offer.trip_id)
        rides = list(session.scalars(select(RideRequest).where(RideRequest.trip_id == trip.id)))
        result.append({
            "offer_id": offer.id,
            "trip_id": trip.id,
            "expires_at": _as_utc(offer.expires_at),
            "vehicle_earnings_inr": round(trip.fare_total_inr * (1 - settings.fare_commission_percent / 100), 2),
            "estimated": trip.estimated,
            "stops": [{
                "action": stop.action,
                "place": session.get(Place, stop.place_id).name,
            } for stop in session.scalars(
                select(TripStop).where(TripStop.trip_id == trip.id).order_by(TripStop.sequence)
            )],
            "rider_count": len(rides),
        })
    return {"offers": result}


@app.post("/api/driver/offers/{offer_id}")
async def respond_to_offer(
    request: Request,
    offer_id: str,
    body: OfferResponse,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
):
    driver = _get_driver(session, user)
    offer = session.scalar(select(DriverOffer).where(
        DriverOffer.id == offer_id, DriverOffer.driver_id == driver.id
    ))
    if offer is None or offer.state != "pending" or _as_utc(offer.expires_at) <= utc_now():
        raise HTTPException(404, "This trip offer has expired.")
    trip = session.get(Trip, offer.trip_id)
    offer.responded_at = utc_now()
    if not body.accept:
        offer.state = "declined"
        trip.state = "declined"
        for ride in session.scalars(select(RideRequest).where(RideRequest.trip_id == trip.id)):
            ride.status = "pending"
            ride.trip_id = None
        session.commit()
        await run_matching(request.app)
        return {"accepted": False}
    active = session.scalar(select(Trip).where(
        Trip.driver_id == driver.id, Trip.state.in_(("accepted", "started"))
    ))
    if active:
        raise HTTPException(409, "Finish the current trip before accepting another.")
    offer.state = "accepted"
    trip.state = "accepted"
    trip.accepted_at = utc_now()
    pickup_etas = {}
    for stop in session.scalars(select(TripStop).where(
        TripStop.trip_id == trip.id, TripStop.action == "pickup"
    )):
        if stop.eta_at:
            pickup_etas[stop.ride_id] = stop.eta_at
    rides = list(session.scalars(select(RideRequest).where(RideRequest.trip_id == trip.id)))
    for ride in rides:
        ride.status = "confirmed"
        ride.promised_pickup_at = pickup_etas.get(ride.id, utc_now())
        await send_push(session, ride.rider_id, "Cab assigned", "Your driver accepted the trip offer.", "/#trip")
    session.commit()
    share_delivery = "not_required"
    if utc_now().astimezone(KOLKATA).hour >= 21 or utc_now().astimezone(KOLKATA).hour < 5:
        share_url = _share_url(trip)
        if request.app.state.demo_mode:
            share_delivery = "simulated"
        else:
            try:
                for ride in rides:
                    rider = session.get(User, ride.rider_id)
                    for contact in rider.emergency_contacts or []:
                        address = contact.get("email")
                        if address:
                            await send_trip_link_email(address, share_url, trip.id)
                share_delivery = "sent"
            except Exception:
                logger.exception("Night trip link delivery failed for trip %s.", trip.id)
                share_delivery = "failed"
    return {"accepted": True, "trip_id": trip.id, "night_link_delivery": share_delivery}


@app.get("/api/trips/{trip_id}")
async def trip_detail(
    trip_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
):
    ride, trip = _trip_for_rider(session, trip_id, user)
    return _trip_view(session, ride, trip)


@app.get("/api/driver/trips/{trip_id}")
async def driver_trip_detail(
    trip_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
):
    driver = _get_driver(session, user)
    trip = session.get(Trip, trip_id)
    if trip is None or trip.driver_id != driver.id:
        raise HTTPException(404, "Trip not found.")
    stops = list(session.scalars(
        select(TripStop).where(TripStop.trip_id == trip.id).order_by(TripStop.sequence)
    ))
    passengers = []
    for ride in session.scalars(select(RideRequest).where(RideRequest.trip_id == trip.id)):
        rider = session.get(User, ride.rider_id)
        drop = session.get(Place, ride.drop_place_id)
        payment = session.scalar(select(Payment).where(Payment.ride_id == ride.id))
        passengers.append({
            "ride_id": ride.id,
            "first_name": rider.name.split(" ")[0],
            "drop_area": drop.name,
            "ride_status": ride.status,
            "fare_inr": ride.fare_inr,
            "payment_method": payment.method if payment else None,
            "payment_state": payment.state if payment else None,
        })
    return {
        "id": trip.id,
        "state": trip.state,
        "distance_m": trip.total_distance_m,
        "duration_s": trip.total_duration_s,
        "vehicle_earnings_inr": round(trip.fare_total_inr * (1 - settings.fare_commission_percent / 100), 2),
        "start_code_required": trip.state == "accepted",
        "stops": [{
            "id": stop.id,
            "sequence": stop.sequence,
            "ride_id": stop.ride_id,
            "action": stop.action,
            "place": session.get(Place, stop.place_id).name,
            "eta_at": _as_utc(stop.eta_at),
            "completed": stop.completed_at is not None,
        } for stop in stops],
        "passengers": passengers,
    }


@app.get("/api/trips/{trip_id}/start-code")
async def trip_start_code(
    trip_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
):
    _, trip = _trip_for_rider(session, trip_id, user)
    if trip.state != "accepted":
        raise HTTPException(409, "The start code is available after the driver accepts.")
    return {"code": decrypt_otp(trip.start_otp_ciphertext)}


@app.post("/api/driver/trips/{trip_id}/start")
async def start_trip(
    trip_id: str,
    body: OtpStart,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
):
    driver = _get_driver(session, user)
    trip = session.get(Trip, trip_id)
    if trip is None or trip.driver_id != driver.id or trip.state != "accepted":
        raise HTTPException(404, "Accepted trip not found.")
    expected = otp_digest("trip:" + trip.id, body.code)
    if not hmac.compare_digest(trip.start_otp_digest, expected):
        raise HTTPException(400, "The rider start code is not valid.")
    trip.state = "started"
    trip.started_at = utc_now()
    trip.start_otp_ciphertext = ""
    for ride in session.scalars(select(RideRequest).where(RideRequest.trip_id == trip.id)):
        ride.status = "in_progress"
    session.commit()
    return {"started": True}


@app.post("/api/driver/trips/{trip_id}/stops/{stop_id}/complete")
async def complete_trip_stop(
    trip_id: str,
    stop_id: int,
    request: Request,
    body: FinishStop,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
):
    driver = _get_driver(session, user)
    trip = session.get(Trip, trip_id)
    stop = session.get(TripStop, body.stop_id)
    if trip is None or trip.driver_id != driver.id or trip.state != "started":
        raise HTTPException(404, "Active trip not found.")
    if stop is None or stop.trip_id != trip.id or stop.id != stop_id:
        raise HTTPException(404, "Trip stop not found.")
    if stop.completed_at:
        raise HTTPException(409, "That stop is already complete.")
    next_stop = session.scalar(
        select(TripStop).where(
            TripStop.trip_id == trip.id,
            TripStop.completed_at.is_(None),
        ).order_by(TripStop.sequence).limit(1)
    )
    if next_stop is None or next_stop.id != stop.id:
        raise HTTPException(409, "Complete trip stops in the listed order.")
    stop.completed_at = utc_now()
    if request.app.state.demo_mode:
        place = session.get(Place, stop.place_id)
        if place:
            driver.latitude = place.latitude
            driver.longitude = place.longitude
            driver.location_at = utc_now()
    if stop.action == "drop":
        ride = session.get(RideRequest, stop.ride_id)
        ride.status = "completed"
    session.flush()
    remaining = session.scalar(select(func.count(TripStop.id)).where(
        TripStop.trip_id == trip.id, TripStop.completed_at.is_(None)
    ))
    if remaining == 0:
        trip.state = "completed"
        trip.completed_at = utc_now()
    session.commit()
    return {"completed": True, "trip_completed": remaining == 0}


@app.get("/api/trips/{trip_id}/driver-contact")
async def trip_driver_contact(
    trip_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
):
    _, trip = _trip_for_rider(session, trip_id, user)
    if trip.state not in ("accepted", "started"):
        raise HTTPException(409, "Driver contact is available after trip confirmation.")
    driver = session.get(Driver, trip.driver_id)
    driver_user = session.get(User, driver.user_id)
    return {"name": driver_user.name, "phone": driver_user.phone}


def _share_token(trip):
    expires = (_as_utc(trip.eta_at) or utc_now() + timedelta(hours=2)) + timedelta(hours=1)
    return jwt.encode({
        "sub": trip.id, "scope": "trip-share", "exp": int(expires.timestamp()),
        "iss": settings.jwt_issuer,
    }, settings.jwt_secret.get_secret_value(), algorithm="HS256")


def _share_url(trip):
    return settings.public_base_url.rstrip("/") + "/share/" + _share_token(trip)


def _decode_polyline(encoded):
    points = []
    index = 0
    latitude = 0
    longitude = 0
    while index < len(encoded):
        values = []
        for _ in range(2):
            shift = 0
            result = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            values.append(~(result >> 1) if result & 1 else result >> 1)
        latitude += values[0]
        longitude += values[1]
        points.append((latitude / 1e5, longitude / 1e5))
    return points


def _distance_to_route_km(latitude, longitude, encoded):
    points = _decode_polyline(encoded)
    if not points:
        return None
    if len(points) == 1:
        return haversine_km(latitude, longitude, points[0][0], points[0][1])
    latitude_scale = 111.32
    longitude_scale = 111.32 * max(0.01, abs(math.cos(math.radians(latitude))))
    px, py = longitude * longitude_scale, latitude * latitude_scale
    best = float("inf")
    for first, second in zip(points, points[1:]):
        ax, ay = first[1] * longitude_scale, first[0] * latitude_scale
        bx, by = second[1] * longitude_scale, second[0] * latitude_scale
        dx, dy = bx - ax, by - ay
        denominator = dx * dx + dy * dy
        ratio = 0.0 if denominator == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denominator))
        best = min(best, ((px - (ax + ratio * dx)) ** 2 + (py - (ay + ratio * dy)) ** 2) ** 0.5)
    return best


@app.post("/api/trips/{trip_id}/share-link")
async def trip_share_link(
    trip_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
):
    _, trip = _trip_for_rider(session, trip_id, user)
    if trip.state not in ("accepted", "started"):
        raise HTTPException(409, "A live link is available after trip confirmation.")
    return {"url": _share_url(trip)}


@app.get("/share/{token}")
async def public_trip_share(token: str, request: Request, session: Session = Depends(get_db)):
    try:
        claims = jwt.decode(
            token, settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"], issuer=settings.jwt_issuer,
        )
    except JWTError as error:
        raise HTTPException(404, "This trip link has expired.") from error
    if claims.get("scope") != "trip-share":
        raise HTTPException(404, "This trip link has expired.")
    trip = session.get(Trip, claims.get("sub"))
    if trip is None or trip.state not in ("accepted", "started"):
        raise HTTPException(404, "This trip link is no longer active.")
    rides = list(session.scalars(select(RideRequest).where(RideRequest.trip_id == trip.id)))
    driver = session.get(Driver, trip.driver_id)
    payload = {
        "state": trip.state,
        "vehicle_number": driver.vehicle_number,
        "latitude": driver.latitude,
        "longitude": driver.longitude,
        "riders": [{
            "first_name": session.get(User, ride.rider_id).name.split(" ")[0],
            "drop_area": session.get(Place, ride.drop_place_id).name,
        } for ride in rides],
    }
    if "text/html" in request.headers.get("accept", "").lower():
        import html
        riders_html = "".join(
            f"<li><strong>{html.escape(r['first_name'])}</strong> &rarr; {html.escape(r['drop_area'])}</li>"
            for r in payload["riders"]
        )
        coords = (
            f"{payload['latitude']:.4f}, {payload['longitude']:.4f}"
            if payload["latitude"] is not None and payload["longitude"] is not None
            else "Pending location"
        )
        page = (
            "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<meta http-equiv='refresh' content='10'>"
            "<title>PoolSmart — Live Shared Trip</title>"
            "<style>body{margin:0;padding:36px 24px;background:#111210;color:#ece8de;"
            "font-family:system-ui,sans-serif;line-height:1.6}main{max-width:620px;margin:0 auto;"
            "border:1px solid #494a43;padding:28px;background:#1a1b18}"
            ".kicker{color:#c1a477;font-size:11px;letter-spacing:.16em;text-transform:uppercase}"
            "h1{font-weight:400;margin:8px 0 18px}ul{padding-left:18px;color:#d3d0c7}"
            ".meta{color:#aaa89e;font-size:14px;margin:10px 0}</style></head><body><main>"
            "<div class='kicker'>POOLSMART · LIVE SHARED TRIP</div>"
            f"<h1>Vehicle {html.escape(payload['vehicle_number'])} · {html.escape(payload['state'].upper())}</h1>"
            f"<p class='meta'>Latest coordinates: {html.escape(coords)}</p>"
            f"<p class='kicker' style='margin-top:20px'>Riders on trip</p><ul>{riders_html}</ul>"
            "<p class='meta'>This read-only link refreshes automatically and expires when the trip ends.</p>"
            "</main></body></html>"
        )
        return HTMLResponse(page)
    return payload


@app.post("/api/rides/{ride_id}/payment")
async def pay_ride(
    ride_id: str,
    request: Request,
    body: PaymentCreate,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
):
    ride = session.scalar(select(RideRequest).where(
        RideRequest.id == ride_id, RideRequest.rider_id == user.id, RideRequest.status == "completed"
    ))
    if ride is None or ride.fare_inr is None:
        raise HTTPException(404, "Completed ride not found.")
    payment = session.scalar(select(Payment).where(Payment.ride_id == ride.id))
    if payment is not None and payment.state != "failed":
        raise HTTPException(409, "A payment record already exists for this ride.")
    if payment is None:
        payment = Payment(
            ride_id=ride.id, rider_id=user.id, amount_inr=ride.fare_inr,
            method=body.method, state="pending",
        )
        session.add(payment)
        session.flush()
    else:
        payment.method = body.method
        payment.state = "pending"
        payment.provider_reference = ""
    if body.method == "wallet":
        if not request.app.state.demo_mode:
            raise HTTPException(403, "Demo wallet payments are only available in local demo mode.")
        if (user.wallet_balance_inr or 0.0) < ride.fare_inr:
            raise HTTPException(422, "Insufficient demo wallet balance. Top up in Account.")
        user.wallet_balance_inr = round((user.wallet_balance_inr or 0.0) - ride.fare_inr, 2)
        if ride.trip_id:
            trip = session.get(Trip, ride.trip_id)
            driver = session.get(Driver, trip.driver_id) if trip else None
            driver_user = session.get(User, driver.user_id) if driver else None
            if driver_user:
                driver_share = round(ride.fare_inr * (1 - settings.fare_commission_percent / 100), 2)
                driver_user.wallet_balance_inr = round((driver_user.wallet_balance_inr or 0.0) + driver_share, 2)
        payment.state = "paid"
        payment.provider_reference = "demo-wallet-" + payment.id
        session.add(payment)
        session.commit()
        return {
            "payment_id": payment.id,
            "method": payment.method,
            "state": payment.state,
            "amount_inr": payment.amount_inr,
            "wallet_balance_inr": user.wallet_balance_inr,
            "simulated": True,
            "notice": "Paid via local demo wallet.",
        }
    if request.app.state.demo_mode:
        payment.method = body.method
        if body.method == "upi":
            payment.state = "awaiting_confirmation"
        elif body.method == "cash":
            payment.state = "due"
        else:
            payment.state = "paid"
            payment.provider_reference = "demo-order-" + payment.id
            if ride.trip_id:
                trip = session.get(Trip, ride.trip_id)
                driver = session.get(Driver, trip.driver_id) if trip else None
                driver_user = session.get(User, driver.user_id) if driver else None
                if driver_user:
                    driver_share = round(ride.fare_inr * (1 - settings.fare_commission_percent / 100), 2)
                    driver_user.wallet_balance_inr = round((driver_user.wallet_balance_inr or 0.0) + driver_share, 2)
        session.add(payment)
        session.commit()
        return {
            "payment_id": payment.id,
            "method": payment.method,
            "state": payment.state,
            "amount_inr": payment.amount_inr,
            "simulated": True,
            "notice": "Local demo payment simulation; no payment provider was contacted.",
        }
    if body.method == "cash":
        payment.state = "due"
        session.add(payment)
        session.commit()
        return {"payment_id": payment.id, "state": payment.state, "amount_inr": payment.amount_inr}
    if body.method == "upi":
        if not settings.upi_payee_id or not settings.upi_payee_name:
            raise HTTPException(503, "UPI details are not configured. Choose cash or contact the service desk.")
        query = urlencode({
            "pa": settings.upi_payee_id, "pn": settings.upi_payee_name,
            "am": "%.2f" % ride.fare_inr, "cu": "INR", "tn": "PoolSmart " + ride.id,
        })
        payment.state = "awaiting_confirmation"
        session.add(payment)
        session.commit()
        upi_uri = "upi://pay?" + query
        image = qrcode.make(upi_uri)
        encoded = io.BytesIO()
        image.save(encoded, format="PNG")
        return {
            "payment_id": payment.id,
            "state": payment.state,
            "amount_inr": payment.amount_inr,
            "upi_uri": upi_uri,
            "qr_data_url": "data:image/png;base64," + base64.b64encode(encoded.getvalue()).decode("ascii"),
        }
    if not settings.razorpay_key_id or not settings.razorpay_key_secret.get_secret_value():
        raise HTTPException(503, "Razorpay is not configured. Choose UPI or cash.")
    if settings.razorpay_test_mode and not settings.razorpay_key_id.startswith("rzp_test_"):
        raise HTTPException(503, "Razorpay test mode is enabled, but a test key is not configured.")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                "https://api.razorpay.com/v1/orders",
                auth=(settings.razorpay_key_id, settings.razorpay_key_secret.get_secret_value()),
                json={"amount": round(ride.fare_inr * 100), "currency": "INR", "receipt": ride.id},
            )
    except httpx.RequestError as error:
        raise HTTPException(502, "Payment provider is temporarily unavailable.") from error
    if response.is_error:
        logger.error("Payment order creation failed for payment record %s.", payment.id)
        raise HTTPException(502, "Payment provider did not create an order.")
    payment.provider_reference = response.json()["id"]
    session.add(payment)
    session.commit()
    return {
        "payment_id": payment.id,
        "state": payment.state,
        "key_id": settings.razorpay_key_id,
        "order_id": payment.provider_reference,
        "amount_paise": round(ride.fare_inr * 100),
    }


@app.post("/api/demo/trips/{trip_id}/advance")
async def demo_advance_trip(
    trip_id: str,
    request: Request,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
):
    if not request.app.state.demo_mode:
        raise HTTPException(404, "Simulated progression is available only in local demo mode.")
    trip = session.get(Trip, trip_id)
    if trip is None:
        raise HTTPException(404, "Trip not found.")
    driver = session.get(Driver, trip.driver_id)
    is_rider = session.scalar(select(RideRequest).where(
        RideRequest.trip_id == trip.id,
        RideRequest.rider_id == user.id,
    )) is not None
    is_driver = driver is not None and driver.user_id == user.id
    if not is_rider and not is_driver and user.role != "admin":
        raise HTTPException(403, "You do not have access to this trip.")
    if trip.state == "offered":
        offer = session.scalar(select(DriverOffer).where(
            DriverOffer.trip_id == trip.id,
            DriverOffer.state == "pending",
        ))
        if offer:
            offer.state = "accepted"
            offer.responded_at = utc_now()
        trip.state = "accepted"
        trip.accepted_at = utc_now()
        pickup_etas = {
            stop.ride_id: stop.eta_at
            for stop in session.scalars(select(TripStop).where(
                TripStop.trip_id == trip.id, TripStop.action == "pickup"
            ))
            if stop.eta_at
        }
        for ride in session.scalars(select(RideRequest).where(RideRequest.trip_id == trip.id)):
            ride.status = "confirmed"
            ride.promised_pickup_at = pickup_etas.get(ride.id, utc_now())
    elif trip.state == "accepted":
        trip.state = "started"
        trip.started_at = utc_now()
        trip.start_otp_ciphertext = ""
        for ride in session.scalars(select(RideRequest).where(RideRequest.trip_id == trip.id)):
            ride.status = "in_progress"
    elif trip.state == "started":
        next_stop = session.scalar(select(TripStop).where(
            TripStop.trip_id == trip.id,
            TripStop.completed_at.is_(None),
        ).order_by(TripStop.sequence).limit(1))
        if next_stop is not None:
            next_stop.completed_at = utc_now()
            place = session.get(Place, next_stop.place_id)
            if place and driver:
                driver.latitude = place.latitude
                driver.longitude = place.longitude
                driver.location_at = utc_now()
            if next_stop.action == "drop":
                ride = session.get(RideRequest, next_stop.ride_id)
                if ride:
                    ride.status = "completed"
            session.flush()
        remaining = session.scalar(select(func.count(TripStop.id)).where(
            TripStop.trip_id == trip.id, TripStop.completed_at.is_(None)
        ))
        if remaining == 0:
            trip.state = "completed"
            trip.completed_at = utc_now()
    session.commit()
    remaining_stops = session.scalar(select(func.count(TripStop.id)).where(
        TripStop.trip_id == trip.id, TripStop.completed_at.is_(None)
    )) or 0
    return {"trip_id": trip.id, "state": trip.state, "remaining_stops": remaining_stops}


@app.post("/api/driver/trips/{trip_id}/rides/{ride_id}/cash-collected")
async def confirm_cash_collection(
    trip_id: str,
    ride_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
):
    driver = _get_driver(session, user)
    trip = session.get(Trip, trip_id)
    ride = session.scalar(select(RideRequest).where(
        RideRequest.id == ride_id,
        RideRequest.trip_id == trip_id,
        RideRequest.status == "completed",
    ))
    if trip is None or trip.driver_id != driver.id or ride is None:
        raise HTTPException(404, "Completed rider payment was not found for this trip.")
    payment = session.scalar(select(Payment).where(Payment.ride_id == ride.id))
    if payment is not None and (payment.method != "cash" or payment.state != "due"):
        raise HTTPException(409, "This ride is not awaiting cash collection.")
    if payment is None:
        payment = Payment(
            ride_id=ride.id,
            rider_id=ride.rider_id,
            amount_inr=ride.fare_inr or 0,
            method="cash",
            state="paid",
        )
    else:
        payment.state = "paid"
    session.add(payment)
    session.commit()
    return {"state": payment.state, "amount_inr": payment.amount_inr}


@app.post("/api/payments/razorpay/callback")
async def razorpay_callback(request: Request, session: Session = Depends(get_db)):
    if request.app.state.demo_mode:
        raise HTTPException(503, "External payment webhooks are disabled in local demo mode.")
    secret = settings.razorpay_webhook_secret.get_secret_value()
    if not secret:
        raise HTTPException(503, "Payment notifications are not configured.")
    signature = request.headers.get("X-Razorpay-Signature", "")
    raw = await request.body()
    expected = hmac.new(secret.encode(), raw, "sha256").hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(400, "Payment signature is not valid.")
    try:
        event = await request.json()
    except ValueError as error:
        raise HTTPException(400, "Payment event payload is not valid JSON.") from error
    if not isinstance(event, dict):
        raise HTTPException(400, "Payment event payload is not valid.")
    event_payload = event.get("payload") or {}
    if not isinstance(event_payload, dict):
        raise HTTPException(400, "Payment event payload is not valid.")
    payment_payload = event_payload.get("payment") or {}
    order_payload = event_payload.get("order") or {}
    payment_entity = (payment_payload.get("entity") or {}) if isinstance(payment_payload, dict) else {}
    order_entity = (order_payload.get("entity") or {}) if isinstance(order_payload, dict) else {}
    if not isinstance(payment_entity, dict) or not isinstance(order_entity, dict):
        raise HTTPException(400, "Payment event entity is not valid.")
    order_id = payment_entity.get("order_id") or order_entity.get("id")
    if not order_id:
        raise HTTPException(400, "Payment event did not include an order.")
    payment = session.scalar(select(Payment).where(Payment.provider_reference == order_id))
    if payment:
        if event.get("event") == "payment.captured":
            entity = payment_entity
            expected_paise = round(payment.amount_inr * 100)
            if entity.get("currency") != "INR" or entity.get("amount") != expected_paise:
                raise HTTPException(400, "Captured payment amount did not match the order.")
            payment.state = "paid"
        elif event.get("event") == "payment.failed":
            payment.state = "failed"
        session.commit()
    elif event.get("event") == "payment.captured":
        logger.warning("Captured payment webhook had no matching order record.")
    return {"received": True}


@app.get("/api/rides/{ride_id}/receipt")
async def receipt_pdf(
    ride_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
):
    ride = session.scalar(select(RideRequest).where(
        RideRequest.id == ride_id, RideRequest.rider_id == user.id
    ))
    if ride is None or ride.status != "completed" or ride.fare_inr is None:
        raise HTTPException(404, "Completed ride statement not found.")
    pickup = session.get(Place, ride.pickup_place_id)
    drop = session.get(Place, ride.drop_place_id)
    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)
    pdf.setTitle("PoolSmart ride fare statement")
    pdf.drawString(60, 780, "PoolSmart ride fare statement")
    pdf.drawString(60, 744, "Ride reference: " + ride.id)
    pdf.drawString(60, 720, "Pickup: " + pickup.name)
    pdf.drawString(60, 696, "Drop-off: " + drop.name)
    pdf.drawString(60, 672, "Fare: INR %.2f" % ride.fare_inr)
    payment = session.scalar(select(Payment).where(Payment.ride_id == ride.id))
    payment_status = (
        "%s · %s" % (payment.method.upper(), payment.state.upper())
        if payment else "Not paid"
    )
    pdf.drawString(60, 648, "Payment: " + payment_status)
    pdf.drawString(60, 624, "Trip status: " + ride.status)
    pdf.save()
    return Response(
        output.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="poolsmart-receipt.pdf"'},
    )


@app.post("/api/push/subscribe")
async def push_subscribe(
    request: Request,
    body: PushRegister,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
):
    if request.app.state.demo_mode:
        return {
            "subscribed": True,
            "simulated": True,
            "notice": "In-app demo alerts only; no browser push provider was contacted.",
        }
    subscription = session.scalar(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint))
    if subscription is None:
        subscription = PushSubscription(user_id=user.id, endpoint=body.endpoint, keys=body.keys)
    elif subscription.user_id != user.id:
        raise HTTPException(409, "This notification endpoint is already registered.")
    else:
        subscription.keys = body.keys
    session.add(subscription)
    session.commit()
    return {"subscribed": True}


@app.post("/api/rides/{ride_id}/sos")
async def raise_sos(
    ride_id: str,
    request: Request,
    body: SosCreate,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
):
    ride = session.scalar(select(RideRequest).where(
        RideRequest.id == ride_id, RideRequest.rider_id == user.id
    ))
    if ride is None or ride.status not in ("confirmed", "in_progress"):
        raise HTTPException(404, "An active trip for this account was not found.")
    campus = _campus(session)
    if not within_service_area(
        body.latitude, body.longitude, campus.latitude, campus.longitude, settings.service_area_radius_km
    ):
        raise HTTPException(422, "SOS location is outside the configured service area.")
    incident = SafetyIncident(
        ride_id=ride.id, user_id=user.id, latitude=body.latitude,
        longitude=body.longitude, details=body.details,
    )
    session.add(incident)
    session.commit()
    if request.app.state.demo_mode:
        incident.notified_at = utc_now()
        session.add(incident)
        session.commit()
        return {
            "recorded": True,
            "incident_id": incident.id,
            "delivery": "simulated_local_demo",
            "simulated": True,
        }
    try:
        await send_sos(session, incident, ride.id, user.emergency_contacts or [])
    except Exception as error:
        logger.error("Safety delivery failed for incident %s.", incident.id)
        raise HTTPException(503, "The safety alert was recorded but could not be delivered. Call local emergency services now.") from error
    admins = list(session.scalars(select(User).where(User.role == "admin", User.suspended.is_(False))))
    for admin in admins:
        await send_push(session, admin.id, "Safety alert", "An active trip raised an SOS.", "/#safety")
    return {"recorded": True, "incident_id": incident.id}


@app.get("/api/admin/drivers")
async def admin_drivers(
    status_filter: str = "pending",
    user: User = Depends(require_roles("admin")),
    session: Session = Depends(get_db),
):
    drivers = list(session.scalars(select(Driver).where(Driver.approval_status == status_filter)))
    return {"drivers": [{
        "id": driver.id, "name": session.get(User, driver.user_id).name,
        "email": session.get(User, driver.user_id).email,
        "phone": session.get(User, driver.user_id).phone,
        "licence_number": driver.licence_number,
        "vehicle_number": driver.vehicle_number,
        "vehicle_model": driver.vehicle_model,
        "seat_capacity": driver.seat_capacity,
        "online": driver.online,
        "insurance_expiry": _as_utc(driver.insurance_expiry),
        "photo_url": driver.photo_url,
    } for driver in drivers]}


@app.get("/api/admin/gender-verifications")
async def admin_gender_verifications(
    user: User = Depends(require_roles("admin")),
    session: Session = Depends(get_db),
):
    users = list(session.scalars(select(User).where(
        User.gender != "unspecified",
        User.gender_verified.is_(False),
        User.suspended.is_(False),
    ).order_by(User.created_at).limit(200)))
    return {"users": [{
        "id": item.id,
        "name": item.name,
        "email": item.email,
        "gender": item.gender,
        "role": item.role,
    } for item in users]}


@app.post("/api/admin/users/{user_id}/gender-verification")
async def set_gender_verification(
    user_id: str,
    verified: bool,
    actor: User = Depends(require_roles("admin")),
    session: Session = Depends(get_db),
):
    subject = session.get(User, user_id)
    if subject is None or subject.gender == "unspecified" or subject.suspended:
        raise HTTPException(404, "A pending gender verification request was not found.")
    subject.gender_verified = verified
    session.add(AuditLog(
        actor_id=actor.id,
        action="gender_verification",
        entity_id=subject.id,
        metadata_json={"verified": verified},
    ))
    session.commit()
    return {"gender_verified": subject.gender_verified}


@app.post("/api/admin/drivers/{driver_id}/approve")
async def approve_driver(
    driver_id: str,
    approved: bool,
    user: User = Depends(require_roles("admin")),
    session: Session = Depends(get_db),
):
    driver = session.get(Driver, driver_id)
    if driver is None:
        raise HTTPException(404, "Driver profile not found.")
    driver.approval_status = "approved" if approved else "rejected"
    if not approved:
        driver.online = False
    session.add(AuditLog(actor_id=user.id, action="driver_review", entity_id=driver.id,
                         metadata_json={"approved": approved}))
    session.commit()
    return {"status": driver.approval_status}


@app.post("/api/admin/drivers/{driver_id}/suspend")
async def suspend_driver(
    driver_id: str,
    user: User = Depends(require_roles("admin")),
    session: Session = Depends(get_db),
):
    driver = session.get(Driver, driver_id)
    if driver is None:
        raise HTTPException(404, "Driver profile not found.")
    driver.approval_status = "suspended"
    driver.online = False
    target = session.get(User, driver.user_id)
    target.suspended = True
    session.add(AuditLog(actor_id=user.id, action="driver_suspended", entity_id=driver.id))
    session.commit()
    return {"suspended": True}


async def _monitor_trip_location(session, trip, driver, latitude, longitude):
    now = utc_now()
    if trip.encoded_polyline:
        distance = _distance_to_route_km(latitude, longitude, trip.encoded_polyline)
        if distance is not None and distance > 0.3:
            if trip.deviation_since_at is None:
                trip.deviation_since_at = now
            elif (
                now - _as_utc(trip.deviation_since_at) >= timedelta(seconds=60)
                and trip.deviation_alerted_at is None
            ):
                trip.deviation_alerted_at = now
                session.add(AuditLog(
                    action="route_deviation_alert",
                    entity_id=trip.id,
                    metadata_json={"threshold_m": 300, "duration_s": 60},
                ))
                for ride in session.scalars(select(RideRequest).where(RideRequest.trip_id == trip.id)):
                    await send_push(session, ride.rider_id, "Route check", "Your cab has moved away from its planned route.", "/#trip")
        elif trip.deviation_alerted_at is None:
            trip.deviation_since_at = None

    last_lat = trip.last_movement_latitude
    last_lon = trip.last_movement_longitude
    if last_lat is None or last_lon is None:
        trip.last_movement_latitude = latitude
        trip.last_movement_longitude = longitude
        trip.stopped_since_at = now
    elif haversine_km(last_lat, last_lon, latitude, longitude) > 0.03:
        trip.last_movement_latitude = latitude
        trip.last_movement_longitude = longitude
        trip.stopped_since_at = None
        trip.long_stop_alerted_at = None
    else:
        if trip.stopped_since_at is None:
            trip.stopped_since_at = now
        elif (
            now - _as_utc(trip.stopped_since_at) >= timedelta(minutes=5)
            and trip.long_stop_alerted_at is None
        ):
            trip.long_stop_alerted_at = now
            session.add(AuditLog(
                action="long_stop_alert",
                entity_id=trip.id,
                metadata_json={"threshold_s": 300},
            ))
            for ride in session.scalars(select(RideRequest).where(RideRequest.trip_id == trip.id)):
                await send_push(session, ride.rider_id, "Long stop", "Your cab has remained in one place. Contact your driver if needed.", "/#trip")
    session.commit()


@app.get("/api/admin/analytics")
async def admin_analytics(
    user: User = Depends(require_roles("admin")),
    session: Session = Depends(get_db),
):
    now = utc_now()
    counts = {
        state: session.scalar(select(func.count(RideRequest.id)).where(RideRequest.status == state)) or 0
        for state in ("pending", "offered", "confirmed", "in_progress", "completed", "cancelled")
    }
    active_drivers = session.scalar(select(func.count(Driver.id)).where(
        Driver.online.is_(True), Driver.approval_status == "approved"
    )) or 0
    completed = counts["completed"]
    pooled_rides = session.scalar(
        select(func.count(RideRequest.id)).where(
            RideRequest.status == "completed",
            RideRequest.trip_id.in_(
                select(TripStop.trip_id)
                .join(RideRequest, RideRequest.id == TripStop.ride_id)
                .where(RideRequest.status == "completed")
                .group_by(TripStop.trip_id)
                .having(func.count(func.distinct(TripStop.ride_id)) > 1)
            ),
        )
    ) or 0
    total_distance = session.scalar(select(func.sum(Trip.total_distance_m)).where(Trip.state == "completed")) or 0
    solo_sum = session.scalar(select(func.sum(MatchingRun.solo_cost_m))) or 0.0
    exact_sum = session.scalar(select(func.sum(MatchingRun.exact_cost_m))) or 0.0
    distance_saved_km = round(max(0.0, solo_sum - exact_sum) / 1000, 2)
    safety_events = session.scalar(select(func.count(SafetyIncident.id)).where(
        SafetyIncident.created_at >= now - timedelta(days=30)
    )) or 0
    return {
        "rides_by_state": counts,
        "active_drivers": active_drivers,
        "completed_distance_km": round(total_distance / 1000, 2),
        "distance_saved_km": distance_saved_km,
        "safety_events_30_days": safety_events,
        "pool_rate_percent": round(
            pooled_rides / max(completed, 1) * 100, 1
        ),
    }


@app.get("/api/admin/incidents")
async def admin_incidents(
    user: User = Depends(require_roles("admin")),
    session: Session = Depends(get_db),
):
    incidents = list(session.scalars(
        select(SafetyIncident).order_by(SafetyIncident.created_at.desc()).limit(50)
    ))
    return {"incidents": [{
        "id": item.id,
        "ride_id": item.ride_id,
        "rider_email": session.get(User, item.user_id).email,
        "latitude": item.latitude,
        "longitude": item.longitude,
        "details": item.details,
        "created_at": _as_utc(item.created_at),
        "notified_at": _as_utc(item.notified_at),
    } for item in incidents]}


@app.get("/api/admin/payments")
async def admin_payments(
    user: User = Depends(require_roles("admin")),
    session: Session = Depends(get_db),
):
    payments = list(session.scalars(select(Payment).where(
        Payment.state == "awaiting_confirmation"
    ).order_by(Payment.created_at).limit(100)))
    return {"payments": [{
        "id": payment.id,
        "ride_id": payment.ride_id,
        "rider_email": session.get(User, payment.rider_id).email,
        "amount_inr": payment.amount_inr,
        "method": payment.method,
        "created_at": _as_utc(payment.created_at),
    } for payment in payments]}


@app.post("/api/admin/payments/{payment_id}/confirm")
async def confirm_manual_payment(
    payment_id: str,
    user: User = Depends(require_roles("admin")),
    session: Session = Depends(get_db),
):
    payment = session.get(Payment, payment_id)
    if payment is None or payment.state != "awaiting_confirmation" or payment.method != "upi":
        raise HTTPException(404, "UPI payment awaiting review was not found.")
    payment.state = "paid"
    session.add(AuditLog(
        actor_id=user.id,
        action="upi_payment_confirmed",
        entity_id=payment.id,
        metadata_json={"amount_inr": payment.amount_inr},
    ))
    session.commit()
    return {"state": payment.state}


@app.get("/api/admin/algorithm-lab")
async def algorithm_lab(
    user: User = Depends(require_roles("admin")),
    session: Session = Depends(get_db),
):
    runs = list(session.scalars(select(MatchingRun).order_by(MatchingRun.created_at.desc()).limit(200)))
    return {"runs": [{
        "created_at": _as_utc(run.created_at),
        "requests": run.requests_count,
        "greedy_cost_m": run.greedy_cost_m,
        "exact_cost_m": run.exact_cost_m,
        "solo_cost_m": run.solo_cost_m,
        "greedy_ms": run.greedy_ms,
        "exact_ms": run.exact_ms,
        "nearest_cost_m": run.nearest_cost_m,
        "hungarian_cost_m": run.hungarian_cost_m,
        "grouping_gap_percent": round(
            (run.greedy_cost_m - run.exact_cost_m) / run.exact_cost_m * 100, 2
        ) if run.exact_cost_m else 0,
    } for run in runs]}


@app.websocket("/ws/driver")
async def driver_socket(websocket: WebSocket):
    if websocket.app.state.local_preview:
        await websocket.close(code=1008, reason="Driver service is disabled in local preview.")
        return
    token = websocket.query_params.get("token", "")
    try:
        claims = jwt.decode(
            token, settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"], issuer=settings.jwt_issuer,
        )
    except JWTError:
        await websocket.close(code=4401)
        return
    session = SessionLocal()
    user = session.get(User, claims.get("sub"))
    driver = session.scalar(select(Driver).where(Driver.user_id == (user.id if user else "")))
    if user is None or user.suspended or driver is None or driver.approval_status != "approved":
        session.close()
        await websocket.close(code=4403)
        return
    await websocket.accept()
    try:
        while True:
            position = DriverPosition.model_validate(await websocket.receive_json())
            campus = _campus(session)
            if not within_service_area(
                position.latitude, position.longitude,
                campus.latitude, campus.longitude, settings.service_area_radius_km,
            ):
                driver.online = False
                session.commit()
                await websocket.send_json({"error": "GPS left the active service area.", "online": False})
                continue
            driver.latitude = position.latitude
            driver.longitude = position.longitude
            driver.location_at = utc_now()
            driver.online = position.online
            session.commit()
            trip = session.scalar(select(Trip).where(
                Trip.driver_id == driver.id, Trip.state == "started"
            ))
            await websocket.send_json({"received_at": driver.location_at.isoformat(), "online": driver.online})
            if trip:
                await _monitor_trip_location(
                    session, trip, driver, position.latitude, position.longitude
                )
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.warning("Driver location session ended for driver %s.", driver.id)
        try:
            await websocket.close(code=4400)
        except Exception:
            pass
    finally:
        session.close()


@app.websocket("/ws/trip/{trip_id}")
async def rider_trip_socket(websocket: WebSocket, trip_id: str):
    if websocket.app.state.local_preview:
        await websocket.close(code=1008, reason="Trip service is disabled in local preview.")
        return
    token = websocket.query_params.get("token", "")
    try:
        claims = jwt.decode(
            token, settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"], issuer=settings.jwt_issuer,
        )
    except JWTError:
        await websocket.close(code=4401)
        return
    session = SessionLocal()
    ride = session.scalar(select(RideRequest).where(
        RideRequest.trip_id == trip_id, RideRequest.rider_id == claims.get("sub")
    ))
    if ride is None:
        session.close()
        await websocket.close(code=4403)
        return
    await websocket.accept()
    try:
        while True:
            await websocket.receive_text()
            driver = session.get(Driver, session.get(Trip, trip_id).driver_id)
            if driver.latitude is not None:
                await websocket.send_json({
                    "latitude": driver.latitude,
                    "longitude": driver.longitude,
                    "location_at": driver.location_at.isoformat() if driver.location_at else None,
                })
    except WebSocketDisconnect:
        pass
    finally:
        session.close()
