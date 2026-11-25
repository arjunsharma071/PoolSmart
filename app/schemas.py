from datetime import datetime
import re
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, field_validator


class OtpRequest(BaseModel):
    email: str = Field(min_length=6, max_length=254)

    @field_validator("email")
    @classmethod
    def email_lower(cls, value):
        return value.strip().lower()


class OtpVerify(BaseModel):
    email: str = Field(min_length=6, max_length=254)
    code: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1, max_length=120)
    consent: bool


class AdminLogin(BaseModel):
    email: str
    password: str


class ProfileUpdate(BaseModel):
    phone: str = Field(default="", max_length=24)
    gender: str = Field(default="unspecified", pattern=r"^(female|male|nonbinary|unspecified)$")
    women_only: bool = False
    same_gender: bool = False
    accessibility_need: str = Field(default="", max_length=240)
    emergency_contacts: list[dict] = Field(default_factory=list, max_length=5)

    @field_validator("emergency_contacts")
    @classmethod
    def validate_contacts(cls, contacts):
        for contact in contacts:
            if not isinstance(contact, dict):
                raise ValueError("Each emergency contact must be an object.")
            if not str(contact.get("name", "")).strip():
                raise ValueError("Each emergency contact needs a name.")
            email = str(contact.get("email", "")).strip()
            phone = str(contact.get("phone", "")).strip()
            if not email and not phone:
                raise ValueError("Each emergency contact needs an email or phone.")
            if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                raise ValueError("Emergency contact email is not valid.")
            if phone and not re.fullmatch(r"[+0-9][0-9 -]{7,22}", phone):
                raise ValueError("Emergency contact phone is not valid.")
        return contacts


class PlaceResolve(BaseModel):
    place_id: str = Field(min_length=3, max_length=180, pattern=r"^[A-Za-z0-9_-]+$")


class RideCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pickup_place_id: str = Field(min_length=3, max_length=180)
    drop_place_id: str = Field(min_length=3, max_length=180)
    scheduled_at: datetime | None = None
    leave_by: datetime | None = None
    seats: int = Field(default=1, ge=1, le=4)
    luggage_count: int = Field(default=0, ge=0, le=6)
    max_wait_minutes: int = Field(default=15, ge=1, le=90)
    max_detour: float = Field(default=1.3, ge=1.0, le=2.0)
    budget_inr: float | None = Field(default=None, gt=0, le=50000)
    women_only: bool = False
    same_gender: bool = False
    @field_validator("scheduled_at", "leave_by")
    @classmethod
    def require_timezone_on_schedule(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError("Scheduled times must include a timezone.")
        return value


class DriverRegister(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=8, max_length=24)
    licence_number: str = Field(min_length=4, max_length=80)
    vehicle_number: str = Field(min_length=4, max_length=32)
    vehicle_model: str = Field(min_length=1, max_length=100)
    seat_capacity: int = Field(default=4, ge=1, le=4)
    photo_url: str = Field(default="", max_length=500)
    insurance_expiry: datetime


class DriverPosition(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    online: bool = True
    speed_m_s: float | None = Field(default=None, ge=0, le=100)


class OfferResponse(BaseModel):
    accept: bool


class OtpStart(BaseModel):
    code: str = Field(pattern=r"^\d{4}$")


class FinishStop(BaseModel):
    stop_id: int = Field(gt=0)


class PaymentCreate(BaseModel):
    method: str = Field(pattern=r"^(upi|razorpay|cash|wallet)$")


class WalletTopUp(BaseModel):
    amount_inr: float = Field(gt=0, le=10000)


class PushRegister(BaseModel):
    endpoint: str = Field(min_length=20, max_length=2048)
    keys: dict[str, str]

    @field_validator("endpoint")
    @classmethod
    def trusted_push_service(cls, value):
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower()
        trusted = (
            hostname == "fcm.googleapis.com"
            or hostname == "web.push.apple.com"
            or hostname == "updates.push.services.mozilla.com"
            or hostname.endswith(".notify.windows.com")
        )
        if (
            parsed.scheme != "https"
            or not trusted
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
        ):
            raise ValueError("Push endpoint must belong to a supported HTTPS browser push service.")
        return value

    @field_validator("keys")
    @classmethod
    def valid_push_keys(cls, value):
        if not value.get("p256dh") or not value.get("auth"):
            raise ValueError("Push subscription must include p256dh and auth keys.")
        if any(len(item) > 512 for item in value.values()):
            raise ValueError("Push subscription keys are too long.")
        return value


class SosCreate(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    details: str = Field(default="", max_length=400)


class RideView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    pickup_place_id: str
    drop_place_id: str
    promised_pickup_at: datetime | None
    fare_inr: float | None
