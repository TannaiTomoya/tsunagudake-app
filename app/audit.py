import hmac

from flask import current_app, request

from app.extensions import db
from app.models import AuditLog


def record_audit(
    action: str, *, actor_id: int | None = None, office_id: int | None = None
) -> None:
    ip = request.remote_addr
    key = current_app.secret_key
    if isinstance(key, str):
        key = key.encode()
    db.session.add(
        AuditLog(
            actor_user_id=actor_id,
            action=action,
            office_id=office_id,
            ip_hash=hmac.digest(key, ip.encode(), "sha256").hex()
            if key and ip
            else None,
        )
    )
