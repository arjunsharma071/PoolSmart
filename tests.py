import asyncio
import math
from types import SimpleNamespace

import pytest

from app.algorithms import (
    best_route_backtracking,
    dijkstra,
    hungarian,
    optimal_grouping,
    route_metrics,
)
from app.matcher import driver_can_serve, riders_can_share
from app.maps import GoogleMaps
from app.main import pay_ride
from app.schemas import PaymentCreate, PushRegister


def small_matrix():
    points = {
        "p1": (0, 0), "d1": (0, 4),
        "p2": (1, 0), "d2": (1, 4),
        "p3": (2, 0), "d3": (2, 4),
        "p4": (3, 0), "d4": (3, 4),
    }
    matrix = {}
    for left, (lx, ly) in points.items():
        matrix[left] = {}
        for right, (rx, ry) in points.items():
            distance = abs(lx - rx) + abs(ly - ry)
            matrix[left][right] = {
                "minutes": float(distance),
                "meters": float(distance * 1000),
                "estimated": False,
            }
    return matrix


def rider(identifier, pickup, drop, **updates):
    base = {
        "id": identifier,
        "pickup_place_id": pickup,
        "drop_place_id": drop,
        "request_minute": 0.0,
        "max_wait_minutes": 60,
        "max_detour": 2.0,
        "seats": 1,
        "solo_minutes": 4.0,
        "solo_meters": 4000.0,
        "women_only": False,
        "same_gender": False,
        "gender": "unspecified",
        "gender_verified": False,
        "budget_inr": None,
    }
    base.update(updates)
    return base


def test_dijkstra_finds_shortest_path_and_predecessors():
    graph = {
        "campus": [("gate", 8), ("library", 2)],
        "library": [("gate", 3)],
        "gate": [],
    }
    distances, previous = dijkstra(graph, "campus")
    assert distances == {"campus": 0, "library": 2, "gate": 5}
    assert previous["gate"] == "library"


def test_backtracking_observes_pickup_before_dropoff():
    matrix = small_matrix()
    request = rider("r1", "p1", "d1")
    route = best_route_backtracking([request], matrix)
    assert route is not None
    actions = [stop["action"] for stop in route["order"]]
    assert actions == ["pickup", "drop"]
    assert route["order"][0]["ride_id"] == route["order"][1]["ride_id"]


def test_route_capacity_counts_riders_on_board_not_total_group_size():
    matrix = small_matrix()
    riders = [
        rider("r1", "p1", "d1"),
        rider("r2", "p2", "d2"),
        rider("r3", "p3", "d3"),
        rider("r4", "p4", "d4"),
    ]
    sequential = []
    for item in riders:
        sequential.extend([
            {"ride_id": item["id"], "action": "pickup", "place_id": item["pickup_place_id"]},
            {"ride_id": item["id"], "action": "drop", "place_id": item["drop_place_id"]},
        ])
    assert route_metrics(riders, sequential, matrix) is not None

    overlapping = [
        {"ride_id": item["id"], "action": "pickup", "place_id": item["pickup_place_id"]}
        for item in riders[:3]
    ] + [
        {"ride_id": item["id"], "action": "drop", "place_id": item["drop_place_id"]}
        for item in riders[:3]
    ]
    too_many = riders[:3]
    for item in too_many:
        item["seats"] = 2
    assert route_metrics(too_many, overlapping, matrix) is None


def test_bitmask_grouping_cost_is_no_worse_than_separate_feasible_groups():
    matrix = small_matrix()
    riders = [
        rider("r1", "p1", "d1"),
        rider("r2", "p2", "d2"),
        rider("r3", "p3", "d3"),
    ]
    groups = optimal_grouping(riders, matrix)
    assert len(groups) >= 1
    exact_cost = sum(group["route"]["meters"] for group in groups)
    solo_cost = sum(
        best_route_backtracking([item], matrix)["meters"]
        for item in riders
    )
    assert exact_cost <= solo_cost


def test_hungarian_minimizes_a_small_assignment_matrix():
    costs = [[10, 1], [2, 9]]
    assignment = hungarian(costs)
    assert sum(costs[row][column] for row, column in enumerate(assignment)) == 3


def test_detour_limit_rejects_an_infeasible_shared_stop_order():
    matrix = small_matrix()
    request = rider(
        "r1", "p1", "d1",
        max_detour=1.0,
        solo_minutes=4,
    )
    detour_route = [
        {"ride_id": "r1", "action": "pickup", "place_id": "p1"},
        {"ride_id": "r1", "action": "drop", "place_id": "d2"},
    ]
    assert route_metrics([request], detour_route, matrix) is None
    assert best_route_backtracking([request], matrix) is not None


def test_live_leg_times_recheck_detour_before_dispatch():
    matrix = small_matrix()
    request = rider("r1", "p1", "d1", max_detour=1.4, solo_minutes=4)
    planned = best_route_backtracking([request], matrix)
    live_legs = [
        {"minutes": 1.0, "meters": 1000, "estimated": False},
        {"minutes": 6.0, "meters": 1000, "estimated": False},
    ]
    assert route_metrics(
        [request], planned["order"], matrix, leg_metrics=live_legs
    ) is None


def test_fractional_live_route_seconds_round_up_for_limit_checks():
    assert GoogleMaps._duration_seconds("1.2s") == 2


def test_failed_payment_retry_reuses_the_existing_ride_record():
    ride = SimpleNamespace(id="ride-1", fare_inr=120.0)
    payment = SimpleNamespace(
        id="payment-1",
        ride_id=ride.id,
        rider_id="rider-1",
        amount_inr=120.0,
        method="razorpay",
        provider_reference="old-order",
        state="failed",
    )

    class SessionStub:
        def __init__(self):
            self.results = iter((ride, payment))
            self.added = []
            self.commits = 0

        def scalar(self, _statement):
            return next(self.results)

        def add(self, value):
            self.added.append(value)

        def commit(self):
            self.commits += 1

    session = SessionStub()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(demo_mode=False)))
    result = asyncio.run(pay_ride(
        ride.id,
        request,
        PaymentCreate(method="cash"),
        SimpleNamespace(id="rider-1"),
        session,
    ))

    assert result["state"] == "due"
    assert session.added == [payment]
    assert payment.method == "cash"
    assert payment.provider_reference == ""
    assert session.commits == 1


def test_departure_deadline_is_enforced_at_dropoff():
    matrix = small_matrix()
    request = rider("r1", "p1", "d1", leave_by_minute=3)
    assert best_route_backtracking([request], matrix) is None
    request["leave_by_minute"] = 8
    assert best_route_backtracking([request], matrix) is not None


def test_current_wait_time_counts_before_driver_arrival():
    matrix = small_matrix()
    matrix["cab"] = {
        "p1": {"minutes": 2, "meters": 2000, "estimated": False},
    }
    request = rider(
        "r1", "p1", "d1",
        request_minute=0,
        current_minute=14,
        max_wait_minutes=15,
    )
    assert best_route_backtracking([request], matrix, start_node="cab") is None


def test_protected_pools_require_verified_gender_for_riders_and_driver():
    female = rider(
        "r1", "p1", "d1", women_only=True,
        gender="female", gender_verified=False,
    )
    assert not riders_can_share([], female)
    female["gender_verified"] = True
    assert riders_can_share([], female)
    driver = type("DriverUser", (), {"gender": "female", "gender_verified": False})()
    assert not driver_can_serve(driver, [female])
    driver.gender_verified = True
    assert driver_can_serve(driver, [female])


def test_unreachable_graph_nodes_remain_infinite():
    distances, _ = dijkstra({"a": [], "b": []}, "a")
    assert math.isinf(distances["b"])


def test_push_endpoints_reject_local_urls_and_require_subscription_keys():
    keys = {"p256dh": "public", "auth": "secret"}
    with pytest.raises(ValueError):
        PushRegister(endpoint="https://127.0.0.1/admin", keys=keys)
    subscription = PushRegister(
        endpoint="https://fcm.googleapis.com/fcm/send/subscription",
        keys=keys,
    )
    assert subscription.keys == keys


@pytest.fixture()
def demo_client(tmp_path, monkeypatch):
    from datetime import timedelta
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from sqlalchemy.orm import sessionmaker

    import app.config as app_config
    import app.database as app_database
    import app.demo as app_demo
    import app.main as app_main
    import app.matcher as app_matcher
    import app.security as app_security

    db_url = f"sqlite:///{tmp_path / 'e2e-demo.db'}"
    test_engine = app_database.build_engine(db_url)
    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setenv("POOLSMART_MODE", "demo")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setattr(app_database, "engine", test_engine)
    monkeypatch.setattr(app_database, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(app_demo, "engine", test_engine)
    monkeypatch.setattr(app_demo, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(app_main, "SessionLocal", TestSessionLocal)

    settings_obj = app_config.get_settings()
    monkeypatch.setattr(settings_obj, "jwt_secret", SecretStr("poolsmart-demo-only-secret-change-before-use-2026"))
    monkeypatch.setattr(settings_obj, "public_base_url", "http://127.0.0.1:8000")
    monkeypatch.setattr(app_main, "settings", settings_obj)
    monkeypatch.setattr(app_matcher, "settings", settings_obj)
    monkeypatch.setattr(app_security, "settings", settings_obj)
    monkeypatch.setattr(app_main.limiter, "enabled", False)

    app_main.app.dependency_overrides[app_database.get_db] = override_get_db
    try:
        with TestClient(app_main.app) as client:
            yield client, TestSessionLocal, timedelta
    finally:
        app_main.app.dependency_overrides.clear()
        app_main.app.state.demo_mode = False
        app_main.app.state.local_preview = False
        test_engine.dispose()


def _demo_auth_header(client, email):
    resp = client.post(
        "/api/auth/demo-login",
        json={"email": email, "password": "poolsmart-demo"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_demo_landing_and_account_profile_and_gender_verification(demo_client):
    client, _, _ = demo_client

    home = client.get("/")
    assert home.status_code == 200
    assert "styles.css?v=local-demo-5" in home.text
    assert "app.js?v=local-demo-5" in home.text

    health = client.get("/api/healthz")
    assert health.status_code == 200
    assert health.json()["demo_mode"] is True

    hubs = client.get("/api/places/hubs")
    assert hubs.status_code == 200
    assert len(hubs.json()["places"]) == 13

    suggest = client.get("/api/places/suggest", params={"q": "mall"})
    assert suggest.status_code == 200
    assert suggest.json()["places"][0]["place_id"] == "demo-mall"

    weather = client.get("/api/weather")
    assert weather.status_code == 200
    assert weather.json()["simulated"] is True

    rider_headers = _demo_auth_header(client, "rider.one@poolsmart.demo")
    me = client.get("/api/me", headers=rider_headers)
    assert me.status_code == 200
    assert me.json()["gender_verified"] is False

    update = client.patch(
        "/api/me",
        headers=rider_headers,
        json={
            "phone": "+91 90000 00001",
            "gender": "female",
            "women_only": True,
            "same_gender": False,
            "accessibility_need": "Front seat",
            "emergency_contacts": [{"name": "Guardian", "email": "guardian@poolsmart.demo", "phone": "+91 90000 00002"}],
        },
    )
    assert update.status_code == 200
    assert client.get("/api/me", headers=rider_headers).json()["accessibility_need"] == "Front seat"

    blocked_ride = client.post(
        "/api/rides",
        headers=rider_headers,
        json={"pickup_place_id": "demo-hostel", "drop_place_id": "demo-mall", "women_only": True},
    )
    assert blocked_ride.status_code == 403

    admin_headers = _demo_auth_header(client, "admin@poolsmart.demo")
    pending_gender = client.get("/api/admin/gender-verifications", headers=admin_headers).json()["users"]
    asha = next(item for item in pending_gender if item["email"] == "rider.one@poolsmart.demo")
    verified = client.post(
        f"/api/admin/users/{asha['id']}/gender-verification",
        params={"verified": True},
        headers=admin_headers,
    )
    assert verified.status_code == 200
    assert verified.json()["gender_verified"] is True

    exported = client.get("/api/me/export", headers=rider_headers)
    assert exported.status_code == 200
    assert exported.json()["profile"]["gender_verified"] is True


def test_demo_end_to_end_pooling_driver_trip_payment_and_onboarding(demo_client):
    client, _, _ = demo_client

    rider1 = _demo_auth_header(client, "rider.one@poolsmart.demo")
    rider2 = _demo_auth_header(client, "rider.two@poolsmart.demo")
    driver = _demo_auth_header(client, "driver@poolsmart.demo")
    new_driver = _demo_auth_header(client, "driver.new@poolsmart.demo")
    admin = _demo_auth_header(client, "admin@poolsmart.demo")

    # 1. Cancel an offered ride cleanly removes pending driver offer
    assert client.post(
        "/api/drivers/online",
        headers=driver,
        json={"latitude": 16.5, "longitude": 80.5, "online": True},
    ).status_code == 200
    solo_offer_ride = client.post(
        "/api/rides",
        headers=rider1,
        json={"pickup_place_id": "demo-hostel", "drop_place_id": "demo-mall"},
    )
    assert solo_offer_ride.status_code == 200
    assert solo_offer_ride.json()["status"] == "offered"
    assert len(client.get("/api/driver/offers", headers=driver).json()["offers"]) == 1

    assert client.delete(f"/api/rides/{solo_offer_ride.json()['id']}", headers=rider1).status_code == 200
    assert client.get("/api/driver/offers", headers=driver).json()["offers"] == []

    # 2. Driver goes offline; two riders form a pool via Open Pools; driver goes online & matches both
    assert client.post(
        "/api/drivers/online",
        headers=driver,
        json={"latitude": 16.5, "longitude": 80.5, "online": False},
    ).status_code == 200

    ride1 = client.post(
        "/api/rides",
        headers=rider1,
        json={"pickup_place_id": "demo-hostel", "drop_place_id": "demo-mall"},
    )
    assert ride1.status_code == 200
    assert ride1.json()["status"] == "pending"

    pools = client.get("/api/pools/open", headers=rider2).json()["pools"]
    assert len(pools) == 1
    assert pools[0]["drop_place_id"] == "demo-mall"

    ride2 = client.post(
        f"/api/pools/{pools[0]['pool_id']}/join",
        headers=rider2,
        json={"pickup_place_id": "demo-main-gate", "drop_place_id": "demo-mall"},
    )
    assert ride2.status_code == 200
    assert ride2.json()["status"] == "pending"

    assert client.post(
        "/api/drivers/online",
        headers=driver,
        json={"latitude": 16.5, "longitude": 80.5, "online": True},
    ).status_code == 200

    offers = client.get("/api/driver/offers", headers=driver).json()["offers"]
    assert len(offers) == 1
    assert offers[0]["rider_count"] == 2
    offer_id = offers[0]["offer_id"]
    trip_id = offers[0]["trip_id"]

    # 3. Driver accepts, rider views live trip/start code/share/SOS, driver starts & completes stops
    accepted = client.post(f"/api/driver/offers/{offer_id}", headers=driver, json={"accept": True})
    assert accepted.status_code == 200
    assert accepted.json()["accepted"] is True

    trip_view = client.get(f"/api/trips/{trip_id}", headers=rider1)
    assert trip_view.status_code == 200
    assert len(trip_view.json()["stops"]) == 4
    assert trip_view.json()["driver_location"] is not None
    assert "latitude" in trip_view.json()["stops"][0]

    start_code = client.get(f"/api/trips/{trip_id}/start-code", headers=rider1).json()["code"]
    assert client.get(f"/api/trips/{trip_id}/driver-contact", headers=rider1).status_code == 200

    share_url = client.post(f"/api/trips/{trip_id}/share-link", headers=rider1).json()["url"]
    share_token = share_url.rsplit("/share/", 1)[1]
    assert client.get(f"/share/{share_token}").status_code == 200
    share_html = client.get(f"/share/{share_token}", headers={"Accept": "text/html"})
    assert share_html.status_code == 200
    assert "text/html" in share_html.headers["content-type"]

    sos = client.post(
        f"/api/rides/{ride1.json()['id']}/sos",
        headers=rider1,
        json={"latitude": 16.5, "longitude": 80.5, "details": "Test SOS"},
    )
    assert sos.status_code == 200
    assert sos.json()["simulated"] is True
    assert len(client.get("/api/admin/incidents", headers=admin).json()["incidents"]) == 1

    assert client.post(
        f"/api/driver/trips/{trip_id}/start",
        headers=driver,
        json={"code": start_code},
    ).status_code == 200

    driver_trip = client.get(f"/api/driver/trips/{trip_id}", headers=driver).json()
    assert driver_trip["vehicle_earnings_inr"] > 0
    for stop in driver_trip["stops"]:
        done = client.post(
            f"/api/driver/trips/{trip_id}/stops/{stop['id']}/complete",
            headers=driver,
            json={"stop_id": stop["id"]},
        )
        assert done.status_code == 200

    # 4. Unsettled completed trip remains visible to driver until cash/payments are settled
    status_after_drop = client.get("/api/drivers/status", headers=driver).json()
    assert status_after_drop["active_trip_id"] is None
    assert status_after_drop["unsettled_trip_id"] == trip_id
    assert status_after_drop["vehicle_model"] == "Demo Sedan"
    assert "distance_saved_km" in client.get("/api/admin/analytics", headers=admin).json()

    pay_cash = client.post(f"/api/rides/{ride1.json()['id']}/payment", headers=rider1, json={"method": "cash"})
    assert pay_cash.status_code == 200
    assert pay_cash.json()["state"] == "due"

    pay_upi = client.post(f"/api/rides/{ride2.json()['id']}/payment", headers=rider2, json={"method": "upi"})
    assert pay_upi.status_code == 200
    assert pay_upi.json()["state"] == "awaiting_confirmation"

    cash_ok = client.post(
        f"/api/driver/trips/{trip_id}/rides/{ride1.json()['id']}/cash-collected",
        headers=driver,
    )
    assert cash_ok.status_code == 200
    assert cash_ok.json()["state"] == "paid"

    pending_upi = client.get("/api/admin/payments", headers=admin).json()["payments"]
    assert len(pending_upi) == 1
    assert client.post(f"/api/admin/payments/{pending_upi[0]['id']}/confirm", headers=admin).json()["state"] == "paid"

    assert client.get("/api/drivers/status", headers=driver).json()["unsettled_trip_id"] is None

    receipt = client.get(f"/api/rides/{ride1.json()['id']}/receipt", headers=rider1)
    assert receipt.status_code == 200
    assert receipt.headers["content-type"].startswith("application/pdf")

    # 5. New driver onboarding + admin approval
    assert client.get("/api/driver/offers", headers=new_driver).json()["application_status"] == "not_applied"
    applied = client.post(
        "/api/drivers/apply",
        headers=new_driver,
        json={
            "name": "Lee New Driver",
            "phone": "+91 90000 12345",
            "licence_number": "DEMO-NEW-001",
            "vehicle_number": "DEMO-999",
            "vehicle_model": "Demo EV",
            "seat_capacity": 4,
            "insurance_expiry": "2030-01-01T23:59:00Z",
            "photo_url": "",
        },
    )
    assert applied.status_code == 200
    pending_drivers = client.get("/api/admin/drivers", params={"status_filter": "pending"}, headers=admin).json()["drivers"]
    lee = next(item for item in pending_drivers if item["vehicle_number"] == "DEMO-999")
    assert client.post(f"/api/admin/drivers/{lee['id']}/approve", params={"approved": True}, headers=admin).json()["status"] == "approved"
    online_new = client.post(
        "/api/drivers/online",
        headers=new_driver,
        json={"latitude": 16.5, "longitude": 80.5, "online": True},
    )
    assert online_new.status_code == 200
    assert online_new.json()["online"] is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
