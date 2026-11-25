import asyncio
import json
import logging
import smtplib
from email.message import EmailMessage

import httpx
from pywebpush import WebPushException, webpush
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import PushSubscription, utc_now


logger = logging.getLogger("poolsmart.notifications")
settings = get_settings()


async def send_push(session: Session, user_id: str, title: str, message: str, url: str = "/"):
    subscriptions = list(session.scalars(
        select(PushSubscription).where(PushSubscription.user_id == user_id)
    ))
    if not subscriptions:
        return
    data = json.dumps({"title": title, "body": message, "url": url})
    vapid = {
        "vapid_private_key": settings.vapid_private_key.get_secret_value(),
        "vapid_claims": {"sub": settings.vapid_subject},
    }

    def dispatch(subscription):
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": subscription.keys,
                },
                data=data,
                vapid_private_key=vapid["vapid_private_key"],
                vapid_claims=vapid["vapid_claims"],
            )
        except WebPushException:
            logger.warning("Push delivery failed for subscription %s.", subscription.id)

    await asyncio.gather(*(asyncio.to_thread(dispatch, item) for item in subscriptions))


async def send_sos(session: Session, incident, ride_id: str, emergency_contacts):
    payload = {
        "event": "sos",
        "incident_id": incident.id,
        "ride_id": ride_id,
        "latitude": incident.latitude,
        "longitude": incident.longitude,
        "created_at": incident.created_at.isoformat(),
        "emergency_contacts": emergency_contacts,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(settings.sos_webhook_url, json=payload)
        if response.is_error:
            logger.error("SOS delivery failed for incident %s with HTTP %s", incident.id, response.status_code)
            raise RuntimeError("The safety channel did not acknowledge the alert.")
    incident.notified_at = utc_now()
    session.add(incident)
    session.commit()
    if settings.telegram_bot_token.get_secret_value() and settings.telegram_chat_id:
        message = "PoolSmart SOS. Trip %s. Location: https://maps.google.com/?q=%s,%s" % (
            ride_id, incident.latitude, incident.longitude
        )
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token.get_secret_value(),
                json={"chat_id": settings.telegram_chat_id, "text": message},
            )
        if response.is_error:
            logger.error("Telegram safety delivery failed for incident %s.", incident.id)
            raise RuntimeError("The Telegram safety channel did not acknowledge the alert.")


async def send_trip_link_email(address, link, trip_reference):
    message = EmailMessage()
    message["Subject"] = "Live PoolSmart trip link"
    message["From"] = settings.smtp_from
    message["To"] = address
    message.set_content(
        "A rider has shared a live trip link with you.\n"
        "Trip reference: %s\n"
        "View trip: %s\n"
        "The link expires when the trip ends." % (trip_reference, link)
    )

    def deliver():
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as client:
            if settings.smtp_starttls:
                client.starttls()
            client.login(settings.smtp_user, settings.smtp_password.get_secret_value())
            client.send_message(message)

    await asyncio.to_thread(deliver)
