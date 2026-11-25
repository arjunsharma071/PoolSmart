import math
from datetime import timedelta

from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.maps import MapsProviderError, haversine_km
from app.models import Driver, Place, User, utc_now
from app.security import create_password_hash


DEMO_PASSWORD = "poolsmart-demo"
DEMO_PLACES = (
    ("demo-campus", "Demo Campus", "campus", 0.0000, 0.0000),
    ("demo-hostel", "Demo Hostel", "campus_hub", 0.0010, 0.0010),
    ("demo-library", "Demo Library", "campus_hub", 0.0020, 0.0020),
    ("demo-canteen", "Demo Canteen", "campus_hub", -0.0010, 0.0015),
    ("demo-main-gate", "Demo Main Gate", "campus_hub", 0.0005, -0.0020),
    ("demo-admin", "Demo Admin Block", "campus_hub", 0.0010, -0.0010),
    ("demo-sports", "Demo Sports Centre", "campus_hub", -0.0020, 0.0000),
    ("demo-metro", "Demo Metro Stop", "destination", 0.0120, 0.0090),
    ("demo-station", "Demo Station", "destination", 0.0250, 0.0200),
    ("demo-mall", "Demo Mall", "destination", 0.0150, -0.0120),
    ("demo-airport", "Demo Airport", "destination", 0.0400, 0.0350),
    ("demo-tech-park", "Demo Tech Park", "destination", -0.0220, 0.0180),
    ("demo-old-town", "Demo Old Town", "destination", -0.0150, -0.0160),
)

DEMO_ACCOUNTS = (
    ("00000000-0000-4000-8000-000000000001", "rider.one@poolsmart.demo", "Asha Demo", "rider", "female", False),
    ("00000000-0000-4000-8000-000000000002", "rider.two@poolsmart.demo", "Dev Demo", "rider", "male", True),
    ("00000000-0000-4000-8000-000000000003", "driver@poolsmart.demo", "Sam Driver", "driver", "unspecified", True),
    ("00000000-0000-4000-8000-000000000004", "driver.pending@poolsmart.demo", "Pat Applicant", "driver", "unspecified", True),
    ("00000000-0000-4000-8000-000000000005", "admin@poolsmart.demo", "PoolSmart Admin", "admin", "unspecified", True),
    ("00000000-0000-4000-8000-000000000006", "driver.new@poolsmart.demo", "Lee New Driver", "driver", "unspecified", True),
)


def seed_demo_data():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        for place_id, name, kind, latitude_offset, longitude_offset in DEMO_PLACES:
            if session.get(Place, place_id) is None:
                session.add(Place(
                    place_id=place_id,
                    name=name,
                    address="Fictional local demonstration network; not a real place.",
                    kind=kind,
                    latitude=16.5000 + latitude_offset,
                    longitude=80.5000 + longitude_offset,
                ))
        session.flush()

        password_hash = create_password_hash(DEMO_PASSWORD)
        users = {}
        for user_id, email, name, role, gender, gender_verified in DEMO_ACCOUNTS:
            user = session.get(User, user_id)
            if user is None:
                user = User(
                    id=user_id,
                    email=email,
                    name=name,
                    role=role,
                    gender=gender,
                    gender_verified=gender_verified,
                    verified_at=utc_now(),
                    consent_at=utc_now(),
                    password_hash=password_hash,
                    phone="+1 555 0100",
                    emergency_contacts=[{
                        "name": "Demo contact",
                        "email": "contact@poolsmart.demo",
                        "phone": "+1 555 0101",
                    }],
                )
            users[email] = user
            session.add(user)
        session.flush()

        campus = session.get(Place, "demo-campus")
        for email, approved, licence, vehicle, model in (
            ("driver@poolsmart.demo", True, "DEMO-LICENCE-001", "DEMO-001", "Demo Sedan"),
            ("driver.pending@poolsmart.demo", False, "DEMO-LICENCE-002", "DEMO-002", "Demo Hatchback"),
        ):
            driver_user = users[email]
            driver = session.scalar(select(Driver).where(Driver.user_id == driver_user.id))
            if driver is None:
                driver = Driver(
                    user_id=driver_user.id,
                    licence_number=licence,
                    vehicle_number=vehicle,
                    vehicle_model=model,
                    seat_capacity=4,
                    approval_status="approved" if approved else "pending",
                    online=False,
                    latitude=campus.latitude,
                    longitude=campus.longitude,
                    location_at=utc_now(),
                    insurance_expiry=utc_now() + timedelta(days=365),
                )
            elif driver.online:
                driver.location_at = utc_now()
            session.add(driver)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class DemoMaps:
    """Deterministic local route simulator. It never makes network requests."""

    def waypoint(self, place):
        if "place_id" in place:
            return {"placeId": place["place_id"]}
        if "latitude" in place:
            return {"location": {"latLng": {
                "latitude": float(place["latitude"]),
                "longitude": float(place["longitude"]),
            }}}
        raise ValueError("A demo route needs a known place or a coordinate.")

    @staticmethod
    def _point(waypoint):
        if "placeId" in waypoint:
            place = next((item for item in DEMO_PLACES if item[0] == waypoint["placeId"]), None)
            if place is None:
                raise ValueError("That place is not in the local demo network.")
            return 16.5000 + place[3], 80.5000 + place[4]
        point = waypoint["location"]["latLng"]
        return float(point["latitude"]), float(point["longitude"])

    @classmethod
    def _leg(cls, origin, destination):
        start = cls._point(origin)
        end = cls._point(destination)
        meters = int(round(haversine_km(*start, *end) * 1250))
        minutes = meters / 1000 / 24 * 60
        if meters:
            minutes = max(0.5, minutes)
        return {"minutes": minutes, "meters": meters, "estimated": True}

    async def close(self):
        return None

    async def search_text(self, text, bias=None, session=None):
        query = text.casefold()
        results = []
        for place_id, name, kind, lat_offset, lon_offset in DEMO_PLACES:
            if query not in name.casefold():
                continue
            results.append({
                "id": place_id,
                "displayName": {"text": name},
                "formattedAddress": "Fictional local demonstration network; not a real place.",
                "location": {
                    "latitude": 16.5000 + lat_offset,
                    "longitude": 80.5000 + lon_offset,
                },
            })
        return results

    async def resolve_place_id(self, session, place_id):
        place = session.get(Place, place_id)
        if place is None or not place_id.startswith("demo-"):
            raise MapsProviderError("Choose a place from the fictional local demo network.")
        return place

    async def route_matrix(self, session, origins, destinations, departure_time=None, coordinates=None):
        return {
            (row, column): self._leg(origin, destination)
            for row, origin in enumerate(origins)
            for column, destination in enumerate(destinations)
        }

    async def compute_route(self, origin, destination, intermediates, departure_time=None, session=None):
        waypoints = [origin, *intermediates, destination]
        legs = [
            self._leg(left, right)
            for left, right in zip(waypoints, waypoints[1:])
        ]
        return {
            "duration_s": math.ceil(sum(leg["minutes"] for leg in legs) * 60),
            "distance_m": sum(leg["meters"] for leg in legs),
            "polyline": "",
            "legs": legs,
            "simulated": True,
        }
