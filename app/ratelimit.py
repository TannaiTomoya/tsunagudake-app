import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta
from ipaddress import ip_address
from math import ceil

from flask import current_app, request
from sqlalchemy import ColumnElement, delete, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from werkzeug.exceptions import TooManyRequests

from app.extensions import db
from app.models import RateLimitEvent, RateLimitLock, utc_now


@dataclass(frozen=True)
class Bucket:
    scope: str
    key_hash: str
    limit: int
    window: timedelta


def _digest(value: str) -> str:
    key = current_app.secret_key
    assert key
    if isinstance(key, str):
        key = key.encode()
    return hmac.digest(key, value.encode(), "sha256").hex()


def login_buckets(email: str) -> tuple[Bucket, ...]:
    if not current_app.config["RATE_LIMIT_ENABLED"]:
        return ()
    try:
        ip = str(ip_address(request.remote_addr or ""))
    except ValueError:
        ip = "unknown"
    return (
        Bucket(
            "email",
            _digest(email),
            current_app.config["RATE_LOGIN_FAIL_MAX"],
            timedelta(seconds=current_app.config["RATE_LOGIN_FAIL_SECONDS"]),
        ),
        Bucket(
            "ip",
            _digest(ip),
            current_app.config["RATE_LOGIN_IP_MAX"],
            timedelta(minutes=10),
        ),
    )


def _prune(now: datetime) -> None:
    # Commit cleanup before acquiring bucket locks. On MySQL, READ COMMITTED
    # avoids retaining gap locks from the expiry-index scan during concurrent inserts.
    engine = db.engine
    if engine.dialect.name == "mysql":
        engine = engine.execution_options(isolation_level="READ COMMITTED")
    with engine.begin() as connection:
        connection.execute(
            delete(RateLimitEvent).where(
                RateLimitEvent.created_at < now - timedelta(days=1)
            )
        )


def _lock_buckets(buckets: tuple[Bucket, ...]) -> None:
    if db.engine.dialect.name == "sqlite":
        # SQLite serializes writers. Acquire the write transaction before reading.
        db.session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        return
    # A fixed set of lock rows avoids storing extra identifiers and keeps the
    # lock index separate from the event data. Acquire them in a consistent order.
    lock_ids = sorted({int(bucket.key_hash[:2], 16) for bucket in buckets})
    for lock_id in lock_ids:
        statement = mysql_insert(RateLimitLock).values(id=lock_id)
        db.session.execute(statement.on_duplicate_key_update(id=lock_id))


def _matching(bucket: Bucket) -> tuple[ColumnElement[bool], ColumnElement[bool]]:
    return (
        RateLimitEvent.scope == bucket.scope,
        RateLimitEvent.key_hash == bucket.key_hash,
    )


def check_login(buckets: tuple[Bucket, ...]) -> None:
    if not buckets:
        return
    _prune(utc_now())
    _lock_buckets(buckets)
    now = utc_now()
    retry_after = 0
    for bucket in buckets:
        failures = db.session.scalars(
            select(RateLimitEvent.created_at)
            .where(*_matching(bucket))
            .order_by(RateLimitEvent.created_at.desc())
            .limit(bucket.limit)
        ).all()
        if len(failures) == bucket.limit and failures[0] - failures[-1] < bucket.window:
            duration = (failures[0] + bucket.window - now).total_seconds()
            retry_after = max(retry_after, ceil(duration))
    if retry_after:
        db.session.rollback()
        raise TooManyRequests(retry_after=retry_after)


def finish_login(buckets: tuple[Bucket, ...], *, successful: bool) -> None:
    now = utc_now().replace(microsecond=0)
    for bucket in buckets:
        if successful and bucket.scope == "email":
            failures = db.session.scalars(
                select(RateLimitEvent.id).where(*_matching(bucket))
            ).all()
            if failures:
                db.session.execute(
                    delete(RateLimitEvent).where(RateLimitEvent.id.in_(failures))
                )
        if not successful:
            db.session.add(
                RateLimitEvent(
                    scope=bucket.scope,
                    key_hash=bucket.key_hash,
                    created_at=now,
                )
            )
