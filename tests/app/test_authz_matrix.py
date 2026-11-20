from dataclasses import dataclass

import pytest
from flask import Flask
from sqlalchemy import select
from test_identity import (
    PASSWORD,
    _agree,
    _publish_and_generate,
    _ranks,
    _seed_identity,
    token,
)

from app import create_app
from app.extensions import db
from app.models import (
    CandidateStatus,
    MatchCandidate,
    MemberRole,
    OfficeMember,
    Reduction,
    Role,
    Talent,
    User,
    Visit,
    today_in_japan,
)
from tsunagu_rules.types import NeedKind

OFFICE_ACTORS = ("other_office", "other_staff", "unrelated", "assigned", "municipality")
TALENT_ACTORS = (
    "other_office",
    "other_staff",
    "owner",
    "staff",
    "unrelated",
    "municipality",
)
EMAILS = {
    "other_office": "outside@example.org",
    "other_staff": "outside-staff@example.org",
    "owner": "office@example.org",
    "staff": "staff@example.org",
    "assigned": "tsunagite@example.org",
    "unrelated": "talent3@example.org",
    "municipality": "municipality@example.org",
    "admin": "admin@example.org",
}


@dataclass(frozen=True)
class Route:
    name: str
    method: str
    path: str
    hidden: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()


ROUTES = (
    Route("office-list", "GET", "/o/1/", OFFICE_ACTORS),
    Route("office-profile", "GET", "/o/1/profile", OFFICE_ACTORS),
    Route("office-routes", "GET", "/o/1/routes", OFFICE_ACTORS),
    Route(
        "office-routes-save",
        "POST",
        "/o/1/routes",
        OFFICE_ACTORS,
        ("staff", "admin"),
    ),
    Route(
        "office-profile-save",
        "POST",
        "/o/1/profile",
        OFFICE_ACTORS,
        ("staff", "admin"),
    ),
    Route("need-new", "GET", "/o/1/needs/new", OFFICE_ACTORS, ("admin",)),
    Route("need-create", "POST", "/o/1/needs/new", OFFICE_ACTORS, ("admin",)),
    Route("need-detail", "GET", "{need}", OFFICE_ACTORS),
    Route("need-edit-form", "GET", "{need}/edit", OFFICE_ACTORS, ("admin",)),
    Route("need-edit-save", "POST", "{need}/edit", OFFICE_ACTORS, ("admin",)),
    Route("need-publish", "POST", "{need}/publish", OFFICE_ACTORS, ("staff", "admin")),
    Route(
        "need-withdraw", "POST", "{need}/withdraw", OFFICE_ACTORS, ("staff", "admin")
    ),
    Route("candidates", "GET", "{need}/candidates", OFFICE_ACTORS),
    Route("candidate-generate", "POST", "{need}/candidates", OFFICE_ACTORS, ("admin",)),
    Route(
        "invite",
        "POST",
        "{need}/candidates/{candidate}/invite",
        OFFICE_ACTORS,
        ("staff", "admin"),
    ),
    Route(
        "cancel",
        "POST",
        "{need}/candidates/{candidate}/cancel",
        OFFICE_ACTORS,
        ("staff", "admin"),
    ),
    Route(
        "accept",
        "POST",
        "/t/invitations/{candidate}/accept",
        (*TALENT_ACTORS, "assigned"),
        ("admin",),
    ),
    Route(
        "decline",
        "POST",
        "/t/invitations/{candidate}/decline",
        (*TALENT_ACTORS, "assigned"),
        ("admin",),
    ),
    Route("office-engagement", "GET", "{engagement}", OFFICE_ACTORS),
    Route("talent-engagement", "GET", "{talent_engagement}", (*TALENT_ACTORS, "admin")),
    Route(
        "visit-new", "GET", "{talent_engagement}/visits/new", TALENT_ACTORS, ("admin",)
    ),
    Route(
        "visit-save",
        "POST",
        "{talent_engagement}/visits/new",
        TALENT_ACTORS,
        ("admin",),
    ),
    Route(
        "visit-edit-form",
        "GET",
        "{talent_engagement}/visits/1/edit",
        TALENT_ACTORS,
        ("admin",),
    ),
    Route(
        "visit-edit-save",
        "POST",
        "{talent_engagement}/visits/1/edit",
        TALENT_ACTORS,
        ("admin",),
    ),
    Route(
        "skill-verify",
        "POST",
        "{engagement}/skills/1/verify",
        ("other_office", "other_staff", "unrelated", "municipality"),
        ("assigned", "admin"),
    ),
    Route(
        "visit-confirm",
        "POST",
        "{engagement}/visits/1/confirm",
        ("other_office", "other_staff", "unrelated", "municipality"),
        ("assigned", "admin"),
    ),
    Route(
        "visit-dispute",
        "POST",
        "{engagement}/visits/1/dispute",
        ("other_office", "other_staff", "unrelated", "municipality"),
        ("assigned", "admin"),
    ),
    Route(
        "reduction-save",
        "POST",
        "{engagement}/reductions",
        ("other_office", "other_staff", "unrelated", "municipality"),
        ("assigned", "admin"),
    ),
    Route(
        "reduction-confirm",
        "POST",
        "{engagement}/reductions/1/confirm",
        ("other_office", "other_staff", "unrelated", "municipality"),
        ("staff", "assigned", "admin"),
    ),
    Route(
        "statement-finalize",
        "POST",
        "{engagement}/statements/{month}/finalize",
        ("other_office", "other_staff", "unrelated", "municipality"),
        ("staff", "assigned", "admin"),
    ),
    Route(
        "office-invoice",
        "GET",
        "{engagement}/statements/{month}/invoice",
        ("other_office", "other_staff", "unrelated", "municipality"),
        ("staff", "assigned", "admin"),
    ),
    Route(
        "talent-invoice",
        "GET",
        "{talent_engagement}/statements/{month}/invoice",
        (*TALENT_ACTORS, "admin"),
    ),
    Route(
        "transfer",
        "POST",
        "{engagement}/statements/{month}/report-transfer",
        ("other_office", "other_staff", "unrelated", "municipality"),
        ("staff", "assigned", "admin"),
    ),
    Route(
        "receipt",
        "POST",
        "{talent_engagement}/statements/{month}/confirm-receipt",
        TALENT_ACTORS,
        ("admin",),
    ),
    Route(
        "profile",
        "GET",
        "/t/profile",
        ("other_office", "other_staff", "owner", "staff", "municipality", "admin"),
    ),
    Route(
        "profile-save",
        "POST",
        "/t/profile",
        ("other_office", "other_staff", "owner", "staff", "municipality", "admin"),
    ),
    Route(
        "pledge",
        "GET",
        "/t/pledge",
        ("other_office", "other_staff", "owner", "staff", "municipality", "admin"),
    ),
    Route(
        "pledge-save",
        "POST",
        "/t/pledge",
        ("other_office", "other_staff", "owner", "staff", "municipality", "admin"),
    ),
    Route(
        "talent-home",
        "GET",
        "/t/",
        ("other_office", "other_staff", "owner", "staff", "municipality", "admin"),
    ),
    Route(
        "region",
        "GET",
        "/r/00002/",
        (
            "other_office",
            "other_staff",
            "owner",
            "staff",
            "assigned",
            "unrelated",
            "municipality",
        ),
    ),
    Route("admin-index", "GET", "/admin/", (*TALENT_ACTORS, "assigned")),
    Route("admin-ledger", "GET", "/admin/ledger", (*TALENT_ACTORS, "assigned")),
    Route(
        "admin-talent", "GET", "/admin/talents/{talent}", (*TALENT_ACTORS, "assigned")
    ),
    Route(
        "translation",
        "POST",
        "/admin/talents/{talent}/translation",
        TALENT_ACTORS,
        ("assigned",),
    ),
    Route(
        "admin-grade",
        "POST",
        "/admin/talents/{talent}/verify-grade",
        TALENT_ACTORS,
        ("assigned",),
    ),
    Route(
        "admin-training",
        "POST",
        "/admin/talents/{talent}/training",
        TALENT_ACTORS,
        ("assigned",),
    ),
    Route(
        "admin-suspend", "POST", "/admin/users/2/suspend", (*TALENT_ACTORS, "assigned")
    ),
    Route("admin-self-suspend", "POST", "/admin/users/4/suspend", (), ("admin",)),
    Route(
        "office-end",
        "POST",
        "{engagement}/end",
        ("other_office", "other_staff", "unrelated", "municipality"),
        ("staff", "assigned", "admin"),
    ),
    Route("talent-end", "POST", "{talent_engagement}/end", TALENT_ACTORS, ("admin",)),
    Route("collection", "GET", "/o/1/collection", OFFICE_ACTORS),
    Route("collection-save", "POST", "/o/1/collection", OFFICE_ACTORS, ("admin",)),
    Route(
        "collection-token",
        "POST",
        "/o/1/collection/token",
        OFFICE_ACTORS,
        ("staff", "admin"),
    ),
    Route("withdraw-form", "GET", "/account/withdraw", (), ("admin",)),
    Route("withdraw", "POST", "/account/withdraw", (), ("admin",)),
)


@pytest.fixture(scope="module")
def matrix() -> tuple[Flask, dict[str, str], dict[str, str]]:
    with pytest.MonkeyPatch.context() as setup:
        setup.setenv("APP_ENV", "test")
        app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite://"})
    _seed_identity(app)
    owner, _, engagement, talent_engagement = _agree(app)
    need, _ = _publish_and_generate(owner)
    candidate_id = _ranks(app)[0][0]
    today = today_in_japan()
    with app.app_context():
        candidate = db.session.get(MatchCandidate, candidate_id)
        assert candidate is not None
        candidate.status = CandidateStatus.INVITED
        for account, office_id, member_role in (
            ("staff", 1, MemberRole.STAFF),
            ("other_office", 2, MemberRole.OWNER),
            ("other_staff", 2, MemberRole.STAFF),
        ):
            person = User(
                email=EMAILS[account], role=Role.OFFICE, display_name="架空利用者"
            )
            person.set_password(PASSWORD)
            db.session.add(person)
            db.session.flush()
            db.session.add(
                OfficeMember(
                    user_id=person.id, office_id=office_id, member_role=member_role
                )
            )
        engagement_id = int(engagement.rsplit("/", 1)[-1])
        db.session.add_all(
            [
                Visit(
                    engagement_id=engagement_id,
                    visit_date=today,
                    minutes=60,
                    category=NeedKind.COLLECT,
                ),
                Reduction(
                    engagement_id=engagement_id,
                    month=today.strftime("%Y-%m"),
                    category=NeedKind.COLLECT,
                    before_hours=30,
                    after_hours=10,
                ),
            ]
        )
        talent_id = db.session.scalar(select(Talent.id).where(Talent.user_id == 2))
        db.session.commit()
        identities = {
            user.email: user.get_id()
            for user in db.session.scalars(
                select(User).where(User.email.in_(tuple(EMAILS.values())))
            )
        }
    paths = {
        "need": need,
        "candidate": str(candidate_id),
        "engagement": engagement,
        "talent_engagement": talent_engagement,
        "talent": str(talent_id),
        "month": today.strftime("%Y-%m"),
    }
    return app, paths, identities


CASES = [
    pytest.param(route, actor, status, id=f"{route.name}-{actor}-{status}")
    for route in ROUTES
    for status, actors in ((404, route.hidden), (403, route.forbidden))
    for actor in actors
]


@pytest.mark.parametrize(("route", "actor", "status"), CASES)
def test_denial_matrix(
    matrix: tuple[Flask, dict[str, str], dict[str, str]],
    route: Route,
    actor: str,
    status: int,
) -> None:
    app, paths, identities = matrix
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = identities[EMAILS[actor]]
    path = route.path.format_map(paths)
    if route.method == "POST":
        response = client.post(
            path,
            data={
                "csrf_token": token(client, "/login"),
                "weekday": "0",
                "share_percent": "40",
                "month": paths["month"],
                "category": "collect",
                "before": "30",
                "after": "10",
                "minutes": "60",
                "day": today_in_japan().isoformat(),
                "grade": "2",
                "client_count": "12",
                "municipality": "2",
                "reason": "goal_met",
                "action": "add",
                "client_no": "5",
                "password": PASSWORD,
                "confirm": "yes",
            },
        )
    else:
        response = client.get(path)
    assert response.status_code == status


@pytest.mark.parametrize("actor", ["staff", "admin"])
@pytest.mark.parametrize(
    "path",
    [
        "/o/1/needs/99999/publish",
        "/o/1/needs/99999/candidates",
        "/o/1/engagements/99999/statements/2030-01/finalize",
    ],
)
def test_missing_record_stays_hidden_for_office_readers(
    matrix: tuple[Flask, dict[str, str], dict[str, str]], actor: str, path: str
) -> None:
    app, _, identities = matrix
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = identities[EMAILS[actor]]
    response = client.post(path, data={"csrf_token": token(client, "/login")})
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("item", "suffix"),
    [
        ("need", ""),
        ("need", "/candidates"),
        ("engagement", ""),
    ],
)
def test_child_lookup_is_restricted_to_the_members_office(
    matrix: tuple[Flask, dict[str, str], dict[str, str]],
    item: str,
    suffix: str,
) -> None:
    app, paths, identities = matrix
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = identities[EMAILS["other_office"]]
    assert client.get("/o/2/").status_code == 200
    path = paths[item].replace("/o/1/", "/o/2/") + suffix
    assert client.get(path).status_code == 404
