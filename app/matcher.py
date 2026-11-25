import logging
import math
import secrets
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.algorithms import (
    best_route_backtracking,
    greedy_grouping,
    hungarian,
    nearest_assignment,
    optimal_grouping,
    route_metrics,
)
from app.config import get_settings
from app.maps import GoogleMaps, MapsProviderError
from app.models import Driver, DriverOffer, MatchingRun, Place, RideRequest, Trip, TripStop, User, utc_now
from app.notifications import send_push
from app.security import encrypt_otp, otp_digest


logger = logging.getLogger("poolsmart.matching")
settings = get_settings()
PENALTY = 1e15


def _as_utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def riders_can_share(group, rider):
    riders = group + [rider]
    if any(item["women_only"] for item in riders):
        if any(
            item["gender"] != "female" or not item["gender_verified"]
            for item in riders
        ):
            return False
    requested_gender = {item["gender"] for item in riders if item["same_gender"]}
    if requested_gender:
        genders = {item["gender"] for item in riders}
        if (
            "unspecified" in genders
            or len(genders) != 1
            or any(not item["gender_verified"] for item in riders)
        ):
            return False
    return True


def driver_can_serve(driver_user, riders):
    if any(rider["women_only"] for rider in riders):
        if driver_user.gender != "female" or not driver_user.gender_verified:
            return False
    if any(rider["same_gender"] for rider in riders):
        genders = {rider["gender"] for rider in riders}
        if (
            len(genders) != 1
            or driver_user.gender not in genders
            or not driver_user.gender_verified
            or any(not rider["gender_verified"] for rider in riders)
        ):
            return False
    return True


def _bearing_degrees(lat1, lon1, lat2, lon2):
    first = math.radians(lat1)
    second = math.radians(lat2)
    delta = math.radians(lon2 - lon1)
    y = math.sin(delta) * math.cos(second)
    x = math.cos(first) * math.sin(second) - math.sin(first) * math.cos(second) * math.cos(delta)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def cluster_requests(requests, user_by_id, places, campus):
    """Divide requests into time/direction/gender clusters of at most 10."""
    clusters = {}
    for ride in requests:
        pickup = places[ride.pickup_place_id]
        destination = places[ride.drop_place_id]
        gender_key = (
            "women", user_by_id[ride.rider_id].gender
        ) if ride.women_only else (
            "gender", user_by_id[ride.rider_id].gender
        ) if ride.same_gender else ("open", "")
        ready = ride.scheduled_at or ride.requested_at
        time_band = int(_as_utc(ready).timestamp() // (15 * 60))
        direction = int(_bearing_degrees(
            campus.latitude, campus.longitude, destination.latitude, destination.longitude
        ) // 45)
        key = (gender_key, time_band, direction)
        clusters.setdefault(key, []).append(ride)
    result = []
    for group in clusters.values():
        group.sort(key=lambda item: _as_utc(item.scheduled_at or item.requested_at))
        result.extend(group[index:index + 10] for index in range(0, len(group), 10))
    return result


def _rider_record(ride, user, places, request_minute, matrix, earliest_ready, current_minute):
    solo = matrix.get(ride.pickup_place_id, {}).get(ride.drop_place_id)
    if solo is None:
        return None
    return {
        "id": ride.id,
        "pickup_place_id": ride.pickup_place_id,
        "drop_place_id": ride.drop_place_id,
        "request_minute": request_minute,
        "current_minute": current_minute,
        "leave_by_minute": (
            (_as_utc(ride.leave_by) - earliest_ready).total_seconds() / 60
            if ride.leave_by else None
        ),
        "max_wait_minutes": ride.max_wait_minutes,
        "max_detour": ride.max_detour,
        "seats": ride.seats,
        "solo_minutes": solo["minutes"],
        "solo_meters": solo["meters"],
        "women_only": ride.women_only,
        "same_gender": ride.same_gender,
        "gender": user.gender,
        "gender_verified": user.gender_verified,
        "budget_inr": ride.budget_inr,
    }


def fare_inr(distance_m, duration_s, hour):
    factor = 1.2 if hour >= 21 or hour < 6 else 1.0
    factor = min(settings.fare_surge_cap, factor)
    return round(
        (settings.fare_base_inr
         + distance_m / 1000 * settings.fare_per_km_inr
         + duration_s / 60 * settings.fare_per_minute_inr) * factor,
        2,
    )


def split_fares(riders, shared_distance_m, shared_duration_s, hour):
    solo = {
        rider["id"]: fare_inr(rider["solo_meters"], rider["solo_minutes"] * 60, hour)
        for rider in riders
    }
    shared_total = fare_inr(shared_distance_m, shared_duration_s, hour)
    denominator = sum(solo.values()) or 1.0
    shares = {
        rider_id: min(solo[rider_id], round(shared_total * solo[rider_id] / denominator, 2))
        for rider_id in solo
    }
    return solo, shares


async def _build_travel_matrix(session, maps, places, drivers, departure):
    keys = list(places)
    waypoint_by_key = {key: {"place_id": key} for key in keys}
    coordinate_by_key = {
        key: (place.latitude, place.longitude)
        for key, place in places.items()
    }
    for driver in drivers:
        key = "driver:" + driver.id
        keys.append(key)
        waypoint_by_key[key] = {"latitude": driver.latitude, "longitude": driver.longitude}
        coordinate_by_key[key] = (driver.latitude, driver.longitude)
        coordinate_by_key["%.5f,%.5f" % (driver.latitude, driver.longitude)] = (
            driver.latitude, driver.longitude
        )
    api_waypoints = [maps.waypoint(waypoint_by_key[key]) for key in keys]
    raw = await maps.route_matrix(
        session,
        api_waypoints,
        api_waypoints,
        departure_time=departure,
        coordinates=coordinate_by_key,
    )
    matrix = {}
    for i, origin in enumerate(keys):
        matrix[origin] = {}
        for j, destination in enumerate(keys):
            value = raw.get((i, j))
            if value is not None:
                matrix[origin][destination] = value
    return matrix


def _prepare_cluster(cluster, users, places, matrix, now):
    earliest = min(_as_utc(item.scheduled_at or item.requested_at) for item in cluster)
    current_minute = max(0.0, (_as_utc(now) - earliest).total_seconds() / 60)
    riders = []
    for ride in cluster:
        user = users[ride.rider_id]
        ready = _as_utc(ride.scheduled_at or ride.requested_at)
        minutes = (ready - earliest).total_seconds() / 60
        rider = _rider_record(ride, user, places, minutes, matrix, earliest, current_minute)
        if rider is None:
            return None
        riders.append(rider)
    return riders


def _valid_gender_group(riders):
    return riders_can_share(riders[:-1], riders[-1]) if riders else True


async def match_pending(session, maps: GoogleMaps):
    now = utc_now()
    expired_trip_ids = list(session.scalars(
        select(DriverOffer.trip_id).where(
            DriverOffer.state == "pending",
            DriverOffer.expires_at <= now,
        )
    ))
    session.query(DriverOffer).filter(
        DriverOffer.state == "pending",
        DriverOffer.expires_at <= now,
    ).update({"state": "expired", "responded_at": now})
    if expired_trip_ids:
        session.query(Trip).filter(Trip.id.in_(expired_trip_ids), Trip.state == "offered").update(
            {"state": "expired"}
        )
        session.query(RideRequest).filter(
            RideRequest.status == "offered",
            RideRequest.trip_id.in_(expired_trip_ids),
        ).update({"status": "pending", "trip_id": None})
    session.commit()

    requests = list(session.scalars(
        select(RideRequest).where(
            RideRequest.status == "pending",
            (RideRequest.scheduled_at.is_(None)) | (RideRequest.scheduled_at <= now),
        ).order_by(RideRequest.requested_at).limit(100)
    ))
    expired_requests = []
    for ride in requests:
        ready = _as_utc(ride.scheduled_at or ride.requested_at)
        if now > ready + timedelta(minutes=ride.max_wait_minutes):
            ride.status = "expired"
            expired_requests.append(ride)
    if expired_requests:
        session.commit()
        for ride in expired_requests:
            await send_push(
                session,
                ride.rider_id,
                "Ride request expired",
                "No feasible cab was assigned within your waiting limit. Submit a new request to try again.",
                "/#ride",
            )
        expired_ids = {ride.id for ride in expired_requests}
        requests = [ride for ride in requests if ride.id not in expired_ids]
    if not requests:
        return {"matched": 0}
    drivers = list(session.scalars(
        select(Driver).where(
            Driver.online.is_(True),
            Driver.approval_status == "approved",
            Driver.location_at >= now - timedelta(seconds=45),
            Driver.latitude.is_not(None),
            Driver.longitude.is_not(None),
            (Driver.insurance_expiry.is_(None)) | (Driver.insurance_expiry >= now),
            ~Driver.id.in_(select(Trip.driver_id).where(Trip.state.in_(("accepted", "started")))),
            ~Driver.id.in_(select(DriverOffer.driver_id).where(
                DriverOffer.state == "pending", DriverOffer.expires_at > now
            )),
        )
    ))
    if not drivers:
        return {"matched": 0, "pending": len(requests)}
    users = {
        user.id: user for user in session.scalars(
            select(User).where(User.id.in_([item.rider_id for item in requests] + [item.user_id for item in drivers]))
        )
    }
    all_place_ids = {item.pickup_place_id for item in requests} | {item.drop_place_id for item in requests}
    places = {
        item.place_id: item for item in session.scalars(
            select(Place).where(Place.place_id.in_(all_place_ids))
        )
    }
    campus = session.scalar(select(Place).where(Place.kind == "campus"))
    if campus is None or len(places) != len(all_place_ids):
        raise RuntimeError("Verified campus and request places are required before matching.")
    matrix = await _build_travel_matrix(session, maps, places, drivers, now)
    clusters = cluster_requests(requests, users, places, campus)
    groups = []
    greedy_total = 0.0
    exact_total = 0.0
    greedy_ms = 0.0
    exact_ms = 0.0
    solo_total = 0.0
    for cluster in clusters:
        riders = _prepare_cluster(cluster, users, places, matrix, now)
        if riders is None:
            continue
        solo_total += sum(rider["solo_meters"] for rider in riders)
        started = time.perf_counter()
        try:
            greedy = greedy_grouping(riders, matrix)
        except ValueError:
            greedy = []
        greedy_ms += (time.perf_counter() - started) * 1000
        greedy_total += sum(group["route"]["meters"] for group in greedy)
        if len(cluster) <= 10:
            started = time.perf_counter()
            try:
                exact = optimal_grouping(riders, matrix)
            except ValueError:
                exact = []
            exact_ms += (time.perf_counter() - started) * 1000
            exact_total += sum(group["route"]["meters"] for group in exact)
            groups.extend(exact)
        else:
            groups.extend(greedy)

    driver_users = {driver.id: users[driver.user_id] for driver in drivers}
    pair_routes = {}
    costs = []
    for group_index, group in enumerate(groups):
        row = []
        for driver_index, driver in enumerate(drivers):
            route = None
            if (
                sum(rider["seats"] for rider in group["riders"]) <= driver.seat_capacity
                and driver_can_serve(driver_users[driver.id], group["riders"])
            ):
                route = best_route_backtracking(
                    group["riders"], matrix, start_node="driver:" + driver.id
                )
            if route is not None:
                pair_routes[(group_index, driver_index)] = route
                row.append(route["meters"])
            else:
                row.append(PENALTY)
        costs.append(row)

    chosen = []
    nearest_cost = 0.0
    hungarian_cost = 0.0
    if groups and drivers:
        if len(groups) <= len(drivers):
            size = len(drivers)
            square = [row[:] for row in costs]
            square.extend([[0.0] * size for _ in range(size - len(groups))])
        else:
            size = len(groups)
            square = [row + [PENALTY] * (size - len(drivers)) for row in costs]
        assignment = hungarian(square)
        for group_index, driver_index in enumerate(assignment[:len(groups)]):
            if driver_index < len(drivers):
                route = pair_routes.get((group_index, driver_index))
                if route is not None:
                    chosen.append((group_index, driver_index, route))
                    hungarian_cost += route["meters"]
        for group_index, driver_index in enumerate(nearest_assignment(costs, PENALTY)):
            if driver_index is not None:
                route = pair_routes.get((group_index, driver_index))
                if route is not None:
                    nearest_cost += route["meters"]

    assigned_driver_ids = set()
    assigned_count = 0
    assigned_ride_count = 0
    for group_index, driver_index, route in chosen:
        group = groups[group_index]
        driver = drivers[driver_index]
        if driver.id in assigned_driver_ids:
            continue
        fare_hour = now.astimezone(ZoneInfo("Asia/Kolkata")).hour
        duration_s = round(route["minutes"] * 60)
        distance_m = round(route["meters"])
        order = route["order"]
        polyline = ""
        estimated = route["estimated"]
        if not estimated and order:
            points = [stop["place_id"] for stop in order]
            try:
                route_data = await maps.compute_route(
                    maps.waypoint({"latitude": driver.latitude, "longitude": driver.longitude}),
                    maps.waypoint({"place_id": points[-1]}),
                    [maps.waypoint({"place_id": point}) for point in points[:-1]],
                    departure_time=now,
                    session=session,
                )
                validated_route = route_metrics(
                    group["riders"],
                    order,
                    {},
                    leg_metrics=route_data["legs"],
                )
                if validated_route is None:
                    logger.info(
                        "Discarding route for group %s: live route legs violate rider limits.",
                        group_index,
                    )
                    continue
                route.update(validated_route)
                polyline = route_data["polyline"]
                distance_m = route_data["distance_m"] or distance_m
                duration_s = route_data["duration_s"] or duration_s
            except MapsProviderError as error:
                if not error.quota:
                    raise
                estimated = True
                logger.warning("Final route quota exhausted for trip %s; route is labeled estimated.", group_index)

        solo, shares = split_fares(group["riders"], distance_m, duration_s, fare_hour)
        if any(
            rider["budget_inr"] is not None and shares[rider["id"]] > rider["budget_inr"]
            for rider in group["riders"]
        ):
            continue
        code = "%04d" % secrets.randbelow(10000)
        trip = Trip(
            driver_id=driver.id,
            state="offered",
            start_otp_digest="",
            start_otp_ciphertext="",
            encoded_polyline=polyline,
            total_distance_m=distance_m,
            total_duration_s=duration_s,
            fare_total_inr=sum(shares.values()),
            estimated=estimated,
            eta_at=now + timedelta(seconds=duration_s),
        )
        session.add(trip)
        session.flush()
        trip.start_otp_digest = otp_digest("trip:" + trip.id, code)
        trip.start_otp_ciphertext = encrypt_otp(code)
        request_by_id = {item.id: item for item in cluster_requests_for_group(group, requests)}
        for sequence, stop in enumerate(order):
            ride = request_by_id[stop["ride_id"]]
            ready_at = _as_utc(ride.scheduled_at or ride.requested_at)
            eta = None
            if stop["action"] == "pickup":
                eta = ready_at + timedelta(minutes=route["waits"][ride.id])
            else:
                eta = (
                    ready_at
                    + timedelta(minutes=route["waits"][ride.id] + route["ride_times"][ride.id])
                )
            session.add(TripStop(
                trip_id=trip.id,
                sequence=sequence,
                ride_id=ride.id,
                place_id=stop["place_id"],
                action=stop["action"],
                eta_at=eta,
            ))
            ride.status = "offered"
            ride.trip_id = trip.id
            ride.fare_inr = shares[ride.id]
            session.add(ride)
        session.add(DriverOffer(
            trip_id=trip.id,
            driver_id=driver.id,
            expires_at=now + timedelta(seconds=30),
        ))
        await send_push(session, driver.user_id, "New trip offer", "Review the route and earnings before the offer expires.", "/#driver")
        assigned_driver_ids.add(driver.id)
        assigned_count += 1
        assigned_ride_count += len(group["riders"])
    session.add(MatchingRun(
        greedy_cost_m=greedy_total,
        exact_cost_m=exact_total,
        solo_cost_m=solo_total,
        nearest_cost_m=nearest_cost,
        hungarian_cost_m=hungarian_cost,
        greedy_ms=greedy_ms,
        exact_ms=exact_ms,
        requests_count=len(requests),
    ))
    session.commit()
    logger.info(
        "matching batch complete requests=%d groups=%d assigned=%d greedy_ms=%.2f exact_ms=%.2f",
        len(requests), len(groups), assigned_count, greedy_ms, exact_ms,
    )
    return {"matched": assigned_count, "pending": len(requests) - assigned_ride_count}


def cluster_requests_for_group(group, all_requests):
    identifiers = {rider["id"] for rider in group["riders"]}
    return [ride for ride in all_requests if ride.id in identifiers]
