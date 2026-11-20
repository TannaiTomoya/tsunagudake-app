from datetime import timedelta

from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import select
from test_identity import PASSWORD, _agree, sign_in, token
from test_identity import database_app as database_app
from test_identity import identity_app as identity_app
from test_identity import mysql_identity_app as mysql_identity_app

from app.extensions import db
from app.models import (
    AuditLog,
    Checkin,
    Engagement,
    MemberRole,
    Office,
    OfficeMember,
    OutboxMail,
    Role,
    User,
    today_in_japan,
)
from app.services.mail import mail_body

ADDRESS = "架空市架空町1-1"
# 海の上の、架空の地点。
LAT, LON = "10.000000", "150.000000"
NEAR = {"lat": "10.001500", "lon": "150.000000"}  # およそ167m
FAR = {"lat": "10.001900", "lon": "150.000000"}  # およそ211m


def _place(client: FlaskClient, address: str, lat: str, lon: str) -> int:
    data = {
        "csrf_token": token(client, "/account"),
        "address": address,
        "lat": lat,
        "lon": lon,
        # 事務所は、画面から受け取らない。
        "office_id": "2",
    }
    return client.post("/o/1/location", data=data).status_code


def _office(app: Flask) -> tuple[str | None, int | None, int | None]:
    with app.app_context():
        office = db.session.get(Office, 1)
        assert office is not None
        return office.address, office.lat_e6, office.lon_e6


def _press(client: FlaskClient, path: str, action: str, **fields: str) -> int:
    data = {"csrf_token": token(client, "/account"), "action": action, **fields}
    return client.post(path + "/checkins", data=data).status_code


def _row(app: Flask) -> Checkin | None:
    with app.app_context():
        return db.session.scalar(select(Checkin))


def test_t62_office_location_is_set_by_the_owner_only(identity_app: Flask) -> None:
    app = identity_app
    office, talent, office_path, talent_path = _agree(app)
    page = office.get("/o/1/profile").get_data(as_text=True)
    assert 'name="lat"' in page and "つなぎ手の画面には出しません" in page
    assert page.count('<button type="submit">') == 1

    # 緯度と経度は、両方そろった数だけ。
    for lat, lon in (
        ("91", LON),
        (LAT, "180.000001"),
        (LAT, ""),
        ("", LON),
        ("架空", LON),
        ("NaN", LON),
        ("Infinity", LON),
        ("1e400", LON),
    ):
        assert _place(office, ADDRESS, lat, lon) == 422, (lat, lon)
    assert _place(office, "あ" * 201, LAT, LON) == 422
    assert _office(app) == (None, None, None)
    assert _place(office, f"  {ADDRESS} ", "１０．０", LON) == 302
    assert _office(app) == (ADDRESS, 10_000_000, 150_000_000)
    with app.app_context():
        assert db.session.scalar(
            select(AuditLog.id).where(AuditLog.action == "office_location_saved")
        )

    # 住所と位置の中身を出すのは、代表の入力欄だけ。職員と運営には、有無だけ。
    mine = office.get("/o/1/profile").get_data(as_text=True)
    assert f'value="{ADDRESS}"' in mine and 'value="10.000000"' in mine
    with app.app_context():
        staff = User(email="staff@example.org", role=Role.OFFICE, display_name="架空")
        staff.set_password(PASSWORD)
        db.session.add(staff)
        db.session.flush()
        db.session.add(
            OfficeMember(user_id=staff.id, office_id=1, member_role=MemberRole.STAFF)
        )
        db.session.commit()
    for email in ("staff@example.org", "admin@example.org"):
        reader = app.test_client()
        assert sign_in(reader, email) == 302
        seen = reader.get("/o/1/profile").get_data(as_text=True)
        assert seen.count("登録しています") == 2
        assert ADDRESS not in seen and "10.000000" not in seen
        assert _place(reader, "架空の書き換え", LAT, LON) == 403
    assert _office(app)[0] == ADDRESS

    # 住所は、担当するつなぎ手の伴走の画面にだけ出る。位置は、どちらにも出ない。
    shown = talent.get(talent_path).get_data(as_text=True)
    assert f"事務所の住所：{ADDRESS}" in shown
    assert "10.000000" not in shown and "150.000000" not in shown
    assert "事務所の住所" not in office.get(office_path).get_data(as_text=True)
    assert ADDRESS not in talent.get("/t/").get_data(as_text=True)

    # 空にすると「登録していない」に戻る。
    assert _place(office, "", "", "") == 302
    assert _office(app) == (None, None, None)
    # デモの事務所には登録できない。
    with app.app_context():
        demo = db.session.get(Office, 1)
        assert demo is not None
        demo.is_demo = True
        db.session.commit()
    assert _place(office, ADDRESS, LAT, LON) == 409
    assert _office(app) == (None, None, None)
    assert 'name="lat"' not in office.get("/o/1/profile").get_data(as_text=True)


def test_t62_arrival_keeps_only_the_fact_and_the_time(database_app: Flask) -> None:
    app = database_app
    office, talent, office_path, talent_path = _agree(app)
    assert _place(office, ADDRESS, LAT, LON) == 302
    page = talent.get(talent_path).get_data(as_text=True)
    assert "data-checkin-arrive" in page and 'name="consent"' in page
    assert "緯度・経度は保存しません" in page and "/static/js/checkin.js" in page
    quiet = office.get(office_path).get_data(as_text=True)
    assert "今日の連絡は、まだありません" in quiet and 'name="minutes"' not in quiet

    # 「今から向かいます」：着くまでの時間は選択肢だけ。位置は受け取らない。
    for bad in ("", "10", "架空", "-30", "30.5"):
        assert _press(talent, talent_path, "heading", minutes=bad) == 422, bad
    assert _press(talent, talent_path, "架空", minutes="30") == 400
    assert _row(app) is None
    extra = {**FAR, "day": "2001-01-01", "near": "1", "arrived_at": "2001-01-01"}
    assert _press(talent, talent_path, "heading", minutes="30", **extra) == 302
    row = _row(app)
    assert row is not None and row.heading_at and row.eta_at
    assert row.eta_at - row.heading_at == timedelta(minutes=30)
    assert (row.day, row.arrived_at, row.near) == (today_in_japan(), None, None)
    assert row.created_by == 2
    # 事務所には、すぐに知らせる（翌朝のまとめに入れない）。
    with app.app_context():
        mail = db.session.scalar(
            select(OutboxMail).where(OutboxMail.subject.contains("向かっています"))
        )
        assert mail is not None and mail.digest_key is None
        assert mail.recipient == "office@example.org"
        assert "着く予定は" in mail_body(mail)
    assert "向かっています" in office.get(office_path).get_data(as_text=True)
    # 押し直すと、予定が変わる。行は増えない。
    assert _press(talent, talent_path, "heading", minutes="60") == 302
    with app.app_context():
        rows = db.session.scalars(select(Checkin)).all()
        assert len(rows) == 1 and rows[0].eta_at and rows[0].heading_at
        assert rows[0].eta_at - rows[0].heading_at == timedelta(minutes=60)

    # 「着きました」：同意と位置がそろい、200m以内のときだけ残る。
    for fields in (
        NEAR,
        {"consent": "yes"},
        {"consent": "yes", "lat": NEAR["lat"]},
        {"consent": "yes", "lat": "架空", "lon": "架空"},
        {"consent": "no", **NEAR},
        {"consent": "yes", **FAR},
    ):
        assert _press(talent, talent_path, "arrived", **fields) == 422, fields
    row = _row(app)
    assert row is not None and row.arrived_at is None and row.near is None
    far = talent.post(
        talent_path + "/checkins",
        data={
            "csrf_token": token(talent, "/account"),
            "action": "arrived",
            "consent": "yes",
            **FAR,
        },
    ).get_data(as_text=True)
    assert "200m以内" in far and "送られた位置は保存していません" in far
    assert FAR["lat"] not in far

    assert _press(talent, talent_path, "arrived", consent="yes", **NEAR) == 302
    row = _row(app)
    assert row is not None and row.arrived_at and row.near is True
    first = row.arrived_at
    # 緯度・経度を入れる列は無い。記録にも残らない。
    columns = {column.name for column in Checkin.__table__.columns}
    assert columns == {
        "id",
        "engagement_id",
        "day",
        "heading_at",
        "eta_at",
        "arrived_at",
        "near",
        "created_by",
        "created_at",
    }
    with app.app_context():
        logs = db.session.scalars(
            select(AuditLog).where(AuditLog.action == "checkin_arrived")
        ).all()
        assert len(logs) == 1 and logs[0].target_type == "engagement"
    # 2回押しても変わらない。着いたあとは「向かいます」を押せない。
    assert _press(talent, talent_path, "arrived", consent="yes", **FAR) == 302
    row = _row(app)
    assert row is not None and row.arrived_at == first
    assert _press(talent, talent_path, "heading", minutes="30") == 409

    for client, path in ((office, office_path), (talent, talent_path)):
        html = client.get(path).get_data(as_text=True)
        assert "事務所の近くで押した記録です" in html
        assert 'name="action" value="arrived"' not in html
        assert NEAR["lat"] not in html
    # 訪問の記録の裏づけとして、訪問の行に出る。確かめるのは、今までどおり事務所。
    new = talent_path + "/visits/new"
    soon = today_in_japan() + timedelta(days=7)
    visit = {
        "csrf_token": token(talent, new),
        "day": today_in_japan().isoformat(),
        "minutes": "60",
        "category": "collect",
        "next_visit": soon.isoformat(),
    }
    assert talent.post(new, data=visit).status_code == 302
    for client, path in ((office, office_path), (talent, talent_path)):
        html = client.get(path).get_data(as_text=True)
        assert "到着の記録あり（事務所の近く）" in html and "確認待ち" in html
        # 次の訪問日は、双方の画面に出る。
        label = f"{soon.month}月{soon.day}日({'月火水木金土日'[soon.weekday()]})"
        assert f"次の訪問は {label} の予定です" in html


def test_t62_next_visit_day_and_places_without_location(identity_app: Flask) -> None:
    app = identity_app
    office, talent, office_path, talent_path = _agree(app)
    today = today_in_japan()

    def next_day() -> object:
        with app.app_context():
            return db.session.scalar(select(Engagement.next_visit_on))

    new = talent_path + "/visits/new"
    form = talent.get(new).get_data(as_text=True)
    assert 'name="next_visit"' in form and "次はいつにしますか" in form
    visit = {
        "csrf_token": token(talent, new),
        "day": today.isoformat(),
        "minutes": "60",
        "category": "collect",
    }
    # 今日から90日先まで。
    for bad in (today - timedelta(days=1), today + timedelta(days=91)):
        data = {**visit, "next_visit": bad.isoformat()}
        assert talent.post(new, data=data).status_code == 409
    assert talent.post(new, data={**visit, "next_visit": "架空"}).status_code == 422
    assert next_day() is None
    last = today + timedelta(days=90)
    data = {**visit, "next_visit": last.isoformat()}
    assert talent.post(new, data=data).status_code == 302
    assert next_day() == last
    # 空のまま直しても、前に決めた日は変わらない。
    edit = talent_path + "/visits/1/edit"
    assert talent.post(edit, data={**visit, "note": "架空"}).status_code == 302
    assert next_day() == last
    # 過ぎた日は、画面に出さない。
    with app.app_context():
        engagement = db.session.scalar(select(Engagement))
        assert engagement is not None
        engagement.next_visit_on = today - timedelta(days=1)
        db.session.commit()
    assert "次の訪問は" not in office.get(office_path).get_data(as_text=True)

    # 位置を登録していない事務所では、位置を受け取らない。時刻だけを残す。
    page = talent.get(talent_path).get_data(as_text=True)
    assert "この事務所では、位置を使いません" in page
    assert "data-checkin-arrive" not in page and 'name="consent"' not in page
    assert _press(talent, talent_path, "arrived", **FAR) == 302
    row = _row(app)
    assert row is not None and row.arrived_at and row.near is None
    for client, path in ((office, office_path), (talent, talent_path)):
        html = client.get(path).get_data(as_text=True)
        assert "位置は確かめていません" in html
        assert "到着の記録あり（位置は確かめていません）" in html


def test_t62_demo_office_and_ended_engagement(identity_app: Flask) -> None:
    app = identity_app
    office, talent, office_path, talent_path = _agree(app)
    with app.app_context():
        demo = db.session.get(Office, 1)
        assert demo is not None
        demo.is_demo, demo.lat_e6, demo.lon_e6 = True, 10_000_000, 150_000_000
        demo.address = ADDRESS
        db.session.commit()
    # デモは、位置を確かめない。
    page = talent.get(talent_path).get_data(as_text=True)
    assert "data-checkin-arrive" not in page
    assert _press(talent, talent_path, "arrived") == 302
    row = _row(app)
    assert row is not None and row.arrived_at and row.near is None

    # 終わった伴走では、押せない。住所も見せない。
    with app.app_context():
        stale = db.session.scalar(select(Checkin))
        assert stale is not None
        db.session.delete(stale)
        db.session.commit()
    end = {"csrf_token": token(talent, talent_path), "reason": "goal_met"}
    assert talent.post(talent_path + "/end", data=end).status_code == 302
    assert _press(talent, talent_path, "heading", minutes="30") == 409
    assert _press(talent, talent_path, "arrived") == 409
    assert _row(app) is None
    closed = talent.get(talent_path).get_data(as_text=True)
    assert ADDRESS not in closed and 'name="action" value="arrived"' not in closed
    # 事務所の側の道からは、押せない。
    assert _press(office, office_path, "arrived") == 404
    assert _press(office, talent_path, "arrived") == 404
