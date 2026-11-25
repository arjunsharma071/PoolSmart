import hashlib
import hmac
import secrets
import smtplib
import base64
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import AdminEmailAllowlist, User


bearer = HTTPBearer(auto_error=False)
settings = get_settings()


def new_otp():
    return "%06d" % secrets.randbelow(1_000_000)


def otp_digest(email, code):
    key = settings.jwt_secret.get_secret_value().encode("utf-8")
    return hmac.new(key, (email.lower() + ":" + code).encode("utf-8"), hashlib.sha256).hexdigest()


def _otp_cipher():
    secret = settings.jwt_secret.get_secret_value().encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret + b"otp-storage").digest())
    return Fernet(key)


def encrypt_otp(code):
    return _otp_cipher().encrypt(code.encode("utf-8")).decode("ascii")


def decrypt_otp(token):
    return _otp_cipher().decrypt(token.encode("ascii")).decode("utf-8")


def create_access_token(user):
    now = datetime.now(timezone.utc)
    return jwt.encode({
        "sub": user.id,
        "role": user.role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.token_minutes)).timestamp()),
        "iss": settings.jwt_issuer,
    }, settings.jwt_secret.get_secret_value(), algorithm="HS256")


def create_password_hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + ":" + digest.hex()


def verify_password(password, encoded):
    try:
        salt_hex, digest_hex = encoded.split(":", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False
    actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return hmac.compare_digest(actual, expected)


def send_otp_email(email, code):
    message = EmailMessage()
    message["Subject"] = "Your PoolSmart sign-in code"
    message["From"] = settings.smtp_from
    message["To"] = email
    message.set_content(
        "Your sign-in code is %s. It expires in 10 minutes. If you did not request it, ignore this message." % code
    )
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as client:
        if settings.smtp_starttls:
            client.starttls()
        client.login(settings.smtp_user, settings.smtp_password.get_secret_value())
        client.send_message(message)


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: Session = Depends(get_db),
):
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to continue.")
    try:
        claims = jwt.decode(
            credentials.credentials,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
        )
        user = session.get(User, claims.get("sub"))
    except (JWTError, ValueError):
        user = None
    if user is None or user.suspended:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Your session is invalid or inactive.")
    return user


def require_roles(*roles):
    def dependency(user=Depends(current_user)):
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="This action is not available to your account.")
        return user
    return dependency


def is_allowlisted(session, email):
    return session.scalar(select(AdminEmailAllowlist).where(AdminEmailAllowlist.email == email)) is not None
