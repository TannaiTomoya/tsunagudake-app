from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import date

from .types import (
    Candidate,
    Exclusion,
    MatchLimits,
    MatchResult,
    Need,
    NeedKind,
    Talent,
)

_WEEKDAYS = "月火水木金土日"
_SKILLS = {
    NeedKind.COLLECT: "資料回収",
    NeedKind.CHECK: "チェックと比較",
    NeedKind.ENTRY: "入力・取り込み",
    NeedKind.REPORT: "月次の資料づくり",
    NeedKind.TOOLING: "道具の設定",
    NeedKind.TEACHING: "使い方の説明",
}


def _score(need: Need, talent: Talent, minutes: int) -> Candidate:
    grade = talent.verified_bookkeeping_grade
    grade_points = {1: 30, 2: 30, 3: 18}.get(grade, 0) if grade is not None else 0
    skills = [
        label for kind, label in _SKILLS.items() if kind in need.kinds & talent.skills
    ]
    weekdays = sorted(need.weekdays & talent.weekdays)
    visits = talent.verified_visits
    distance_points = next(
        (
            points
            for limit, points in ((15, 15), (30, 10), (45, 5), (60, 2))
            if minutes <= limit
        ),
        0,
    )
    visit_points = next(
        (
            points
            for threshold, points in ((12, 10), (4, 6), (1, 3))
            if visits >= threshold
        ),
        0,
    )
    components = (
        (grade_points, f"簿記{grade}級：会計の言葉が通じます"),
        (
            20 if need.software and need.software in talent.software else 0,
            "同じ会計ソフトを使えます",
        ),
        (10 * min(len(skills), 2), f"{'・'.join(skills)}が得意です"),
        (distance_points, f"車で{minutes}分：毎週通える距離です"),
        (visit_points, f"確かめられた訪問が{visits}回あります"),
        (
            5 if len(weekdays) >= 2 else 3,
            f"{'・'.join(_WEEKDAYS[day] + '曜' for day in weekdays)}に通えます",
        ),
    )
    return Candidate(
        talent.id,
        sum(points for points, _ in components),
        minutes,
        visits,
        tuple(f"{reason}（{points}点）" for points, reason in components if points),
    )


def match(
    need: Need,
    talents: Iterable[Talent],
    travel: Mapping[tuple[str, str], int],
    today: date,
    limits: MatchLimits,
) -> MatchResult:
    candidates = []
    excluded: Counter[Exclusion] = Counter()
    for talent in talents:
        minutes = travel.get((talent.municipality, need.municipality))
        checks = (
            (
                Exclusion.PLEDGE,
                talent.pledge_until is not None and talent.pledge_until >= today,
            ),
            (
                Exclusion.TRAINING,
                talent.training_until is not None and talent.training_until >= today,
            ),
            (
                Exclusion.DISTANCE,
                minutes is not None and 0 <= minutes <= limits.travel_minutes,
            ),
            (Exclusion.WEEKDAY, bool(need.weekdays & talent.weekdays)),
            (Exclusion.CAPACITY, talent.active_count < talent.capacity),
            (Exclusion.UNLISTED, talent.is_listed and not talent.suspended),
            (Exclusion.DECLINED, talent.id not in need.declined_ids),
        )
        failures = [reason for reason, passed in checks if not passed]
        if failures:
            excluded.update(failures)
        else:
            assert minutes is not None
            candidates.append(_score(need, talent, minutes))
    candidates.sort(
        key=lambda c: (-c.score, c.travel_minutes, -c.verified_visits, c.talent_id)
    )
    return MatchResult(
        tuple(candidates[:3]),
        {reason: excluded[reason] for reason in Exclusion if excluded[reason]},
    )
