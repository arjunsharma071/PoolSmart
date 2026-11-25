from datetime import datetime, timezone
import uuid

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    phone: Mapped[str] = mapped_column(String(24), default="")
    role: Mapped[str] = mapped_column(String(16), default="rider", index=True)
    password_hash: Mapped[str] = mapped_column(String(256), default="")
    gender: Mapped[str] = mapped_column(String(24), default="unspecified")
    gender_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    women_only: Mapped[bool] = mapped_column(Boolean, default=False)
    same_gender: Mapped[bool] = mapped_column(Boolean, default=False)
    accessibility_need: Mapped[str] = mapped_column(String(240), default="")
    emergency_contacts: Mapped[list] = mapped_column(JSON, default=list)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspended: Mapped[bool] = mapped_column(Boolean, default=False)
    wallet_balance_inr: Mapped[float] = mapped_column(Float, default=1000.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class OtpChallenge(Base):
    __tablename__ = "otp_challenges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(254), index=True)
    digest: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AdminEmailAllowlist(Base):
    __tablename__ = "admin_email_allowlist"

    email: Mapped[str] = mapped_column(String(254), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), default="staff")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Place(Base):
    __tablename__ = "places"

    place_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    search_query: Mapped[str | None] = mapped_column(String(240), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(180), index=True)
    address: Mapped[str] = mapped_column(String(400), default="")
    kind: Mapped[str] = mapped_column(String(32), default="destination", index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Driver(Base):
    __tablename__ = "drivers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    licence_number: Mapped[str] = mapped_column(String(80), unique=True)
    vehicle_number: Mapped[str] = mapped_column(String(32), unique=True)
    vehicle_model: Mapped[str] = mapped_column(String(100))
    seat_capacity: Mapped[int] = mapped_column(Integer, default=4)
    photo_url: Mapped[str] = mapped_column(String(500), default="")
    insurance_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    online: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rating: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RideRequest(Base):
    __tablename__ = "ride_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    rider_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    pickup_place_id: Mapped[str] = mapped_column(ForeignKey("places.place_id"))
    drop_place_id: Mapped[str] = mapped_column(ForeignKey("places.place_id"))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    leave_by: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    seats: Mapped[int] = mapped_column(Integer, default=1)
    luggage_count: Mapped[int] = mapped_column(Integer, default=0)
    max_wait_minutes: Mapped[int] = mapped_column(Integer, default=15)
    max_detour: Mapped[float] = mapped_column(Float, default=1.3)
    budget_inr: Mapped[float | None] = mapped_column(Float, nullable=True)
    women_only: Mapped[bool] = mapped_column(Boolean, default=False)
    same_gender: Mapped[bool] = mapped_column(Boolean, default=False)
    friend_group_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    trip_id: Mapped[str | None] = mapped_column(ForeignKey("trips.id"), nullable=True, index=True)
    promised_pickup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fare_inr: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Trip(Base):
    __tablename__ = "trips"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    driver_id: Mapped[str] = mapped_column(ForeignKey("drivers.id"), index=True)
    state: Mapped[str] = mapped_column(String(24), default="offered", index=True)
    start_otp_digest: Mapped[str] = mapped_column(String(128))
    start_otp_ciphertext: Mapped[str] = mapped_column(String(256), default="")
    encoded_polyline: Mapped[str] = mapped_column(Text, default="")
    eta_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deviation_since_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deviation_alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_since_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    long_stop_alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_movement_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_movement_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_distance_m: Mapped[int] = mapped_column(Integer, default=0)
    total_duration_s: Mapped[int] = mapped_column(Integer, default=0)
    fare_total_inr: Mapped[float] = mapped_column(Float, default=0.0)
    estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    stops: Mapped[list["TripStop"]] = relationship(back_populates="trip", cascade="all, delete-orphan")


class TripStop(Base):
    __tablename__ = "trip_stops"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    ride_id: Mapped[str] = mapped_column(ForeignKey("ride_requests.id"), index=True)
    place_id: Mapped[str] = mapped_column(ForeignKey("places.place_id"))
    action: Mapped[str] = mapped_column(String(12))
    eta_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trip: Mapped["Trip"] = relationship(back_populates="stops")


class DriverOffer(Base):
    __tablename__ = "driver_offers"
    __table_args__ = (UniqueConstraint("trip_id", "driver_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"), index=True)
    driver_id: Mapped[str] = mapped_column(ForeignKey("drivers.id"), index=True)
    state: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TravelCache(Base):
    __tablename__ = "travel_cache"

    cache_key: Mapped[str] = mapped_column(String(300), primary_key=True)
    duration_s: Mapped[int] = mapped_column(Integer)
    distance_m: Mapped[int] = mapped_column(Integer)
    estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class MapsQuota(Base):
    __tablename__ = "maps_quota"

    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    elements: Mapped[int] = mapped_column(Integer, default=0)
    requests: Mapped[int] = mapped_column(Integer, default=0)


class MatchingRun(Base):
    __tablename__ = "matching_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    greedy_cost_m: Mapped[float] = mapped_column(Float, default=0.0)
    exact_cost_m: Mapped[float] = mapped_column(Float, default=0.0)
    solo_cost_m: Mapped[float] = mapped_column(Float, default=0.0)
    nearest_cost_m: Mapped[float] = mapped_column(Float, default=0.0)
    hungarian_cost_m: Mapped[float] = mapped_column(Float, default=0.0)
    greedy_ms: Mapped[float] = mapped_column(Float, default=0.0)
    exact_ms: Mapped[float] = mapped_column(Float, default=0.0)
    requests_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    keys: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SafetyIncident(Base):
    __tablename__ = "safety_incidents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ride_id: Mapped[str] = mapped_column(ForeignKey("ride_requests.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    details: Mapped[str] = mapped_column(String(400), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("ride_id", name="uq_payments_ride_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ride_id: Mapped[str] = mapped_column(ForeignKey("ride_requests.id"), index=True)
    rider_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    amount_inr: Mapped[float] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(16))
    provider_reference: Mapped[str] = mapped_column(String(180), default="")
    state: Mapped[str] = mapped_column(String(24), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
