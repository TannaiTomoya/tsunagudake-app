from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from tsunagu_rules import RULES_VERSION, ledger
from tsunagu_rules.compare import CompareLine, MarkReason, compare
from tsunagu_rules.distance import NEAR_RADIUS_M, distance_m, is_near
from tsunagu_rules.matching import match
from tsunagu_rules.region import (
    is_fictional,
    mask_breakdown,
    mask_cells,
    mask_region,
    recent_months,
)
from tsunagu_rules.translation import load_dictionary, translate
from tsunagu_rules.types import (
    Exclusion,
    LedgerEntry,
    MaskedCells,
    MatchLimits,
    Need,
    NeedKind,
    Reduction,
    RegionCell,
    RegionMetrics,
    RegionRow,
    StoredEntry,
    Talent,
)
from tsunagu_rules.value import calc_fee, estimate_value

DICTIONARY = load_dictionary()
TODAY = date(2030, 1, 1)
NEED = Need("架空市", frozenset({0}), frozenset({NeedKind.COLLECT}), "架空会計")
TALENT = Talent(
    1,
    "架空町",
    frozenset({0}),
    NEED.kinds,
    frozenset({NEED.software}),
    TODAY,
    TODAY,
    0,
    1,
    True,
    False,
    2,
    0,
    True,
)


def test_t01_normalization() -> None:
    assert translate("Ａ\n　ﾁｪｯｸ", DICTIONARY) == translate("aチェック", DICTIONARY)


def test_t02_patterns_and_evidence() -> None:
    result = translate("過去と見比べる。資料が集まらない。", DICTIONARY)
    assert [p.kind for p in result.patterns] == [NeedKind.COLLECT, NeedKind.CHECK]
    assert result.patterns[0].words == ("集まらない",)
    assert result.patterns[0].tasks == ("reminder_flow", "intake_form")


@pytest.mark.parametrize("phrase", ["申告書の作成", "税務相談"])
def test_t03_taxwork_precedes_matching(phrase: str) -> None:
    result = translate(f"資料回収と{phrase}", DICTIONARY)
    assert result.blocked and not result.matched and not result.patterns
    assert result.blocked_words == (phrase,)


def test_t04_no_match() -> None:
    result = translate("困っています", DICTIONARY)
    assert not result.matched and not result.blocked


def test_value_estimate() -> None:
    result = estimate_value(30, 4000)
    assert result.monthly_value_yen == 120000
    assert result.projected_hours == (6, 10, 15)
    assert result.projected_value_yen == (24000, 40000, 60000)


def test_t05_expiration_is_inclusive() -> None:
    yesterday = TODAY - timedelta(days=1)
    talents = [
        TALENT,
        replace(TALENT, id=2, pledge_until=yesterday),
        replace(TALENT, id=3, training_until=yesterday),
    ]
    result = match(NEED, talents, {("架空町", "架空市"): 60}, TODAY, MatchLimits())
    assert [c.talent_id for c in result.candidates] == [1]
    assert result.excluded == {Exclusion.PLEDGE: 1, Exclusion.TRAINING: 1}


def test_t06_distance_cutoff_and_missing_route() -> None:
    talents = [
        TALENT,
        replace(TALENT, id=2, municipality="遠方町"),
        replace(TALENT, id=3, municipality="未登録町"),
    ]
    travel = {("架空町", "架空市"): 60, ("遠方町", "架空市"): 61}
    result = match(NEED, talents, travel, TODAY, MatchLimits())
    assert [c.talent_id for c in result.candidates] == [1]
    assert result.excluded == {Exclusion.DISTANCE: 2}


def test_t07_t10_exclusions_only_return_counts() -> None:
    talents = [
        replace(TALENT, weekdays=frozenset({1})),
        replace(TALENT, id=2, active_count=1),
        replace(TALENT, id=3),
        replace(TALENT, id=4, suspended=True),
    ]
    need = replace(NEED, declined_ids=frozenset({3}))
    result = match(need, talents, {("架空町", "架空市"): 15}, TODAY, MatchLimits())
    assert not result.candidates
    assert result.excluded == dict.fromkeys(
        (Exclusion.WEEKDAY, Exclusion.CAPACITY, Exclusion.UNLISTED, Exclusion.DECLINED),
        1,
    )


def test_t08_ties_and_top_three() -> None:
    talents = [
        replace(TALENT, municipality="遠方町", verified_visits=5),
        replace(TALENT, id=2, verified_visits=4),
        replace(TALENT, id=4, verified_visits=5),
        replace(TALENT, id=3, verified_visits=5),
    ]
    travel = {("架空町", "架空市"): 20, ("遠方町", "架空市"): 21}
    result = match(NEED, talents, travel, TODAY, MatchLimits())
    assert [c.talent_id for c in result.candidates] == [3, 4, 2]
    assert len({c.score for c in result.candidates}) == 1


def test_t09_reasons_and_unverified_grade() -> None:
    talent = replace(TALENT, verified_bookkeeping_grade=None, verified_visits=8)
    result = match(NEED, [talent], {("架空町", "架空市"): 25}, TODAY, MatchLimits())
    (candidate,) = result.candidates
    assert candidate.score == 49
    assert candidate.reasons == (
        "同じ会計ソフトを使えます（20点）",
        "資料回収が得意です（10点）",
        "車で25分：毎週通える距離です（10点）",
        "確かめられた訪問が8回あります（6点）",
        "月曜に通えます（3点）",
    )
    points = [int(r.rsplit("（", 1)[1].removesuffix("点）")) for r in candidate.reasons]
    assert sum(points) == candidate.score


def test_t11_reduction_and_waiver() -> None:
    assert calc_fee([Reduction(30, 10, True)], 4000, 4000, None).fee_yen == 32000
    waived = calc_fee([Reduction(30, 10, False)], 4000, 4000, None)
    assert waived.fee_yen == 0 and waived.waived


def test_t12_floor_cap_and_increases() -> None:
    rows = [Reduction(3, 2, True), Reduction(0, 8, True)]
    assert calc_fee(rows, 9, 4000, None).fee_yen == 3
    assert calc_fee(rows, 9, 4000, 2).fee_yen == 2


@pytest.mark.parametrize("share", [0, 5000, -1])
def test_t13_invalid_share(share: int) -> None:
    with pytest.raises(ValueError, match="割合"):
        calc_fee([], 4000, share, None, fee_share_max_bp=5000)


def test_t14_chain_detects_tampering() -> None:
    entry = LedgerEntry(
        1,
        1,
        2,
        datetime(2030, 1, 1, tzinfo=UTC),
        "written",
        RULES_VERSION,
        {"text": "資料"},
    )
    first = StoredEntry(
        entry, ledger.GENESIS_HASH, ledger.append(ledger.GENESIS_HASH, entry)
    )
    second_entry = replace(entry, seq=2)
    second = StoredEntry(
        second_entry, first.entry_hash, ledger.append(first.entry_hash, second_entry)
    )
    assert ledger.verify([first, second]).valid
    tampered = replace(first, entry=replace(entry, data={"text": "書類"}))
    assert ledger.verify([tampered, second]).invalid_seq == 1


def test_t15_agreement_requires_boundaries() -> None:
    entry = LedgerEntry(
        1,
        1,
        2,
        datetime(2030, 1, 1, tzinfo=UTC),
        "agreed",
        RULES_VERSION,
        {"not_do": ["追加条件"]},
    )
    prepared = ledger.with_required_boundaries(entry)
    assert prepared.data["not_do"] == [*ledger.REQUIRED_BOUNDARIES, "追加条件"]
    digest = ledger.append(ledger.GENESIS_HASH, entry)
    assert ledger.verify([StoredEntry(prepared, ledger.GENESIS_HASH, digest)]).valid
    assert not ledger.verify([StoredEntry(entry, ledger.GENESIS_HASH, digest)]).valid


def test_t55_anchor_digest_and_rewritten_chains() -> None:
    tips = [(1, 3, "a" * 64), (2, 1, "b" * 64)]
    digest = ledger.anchor_digest(tips)
    assert digest == ledger.anchor_digest(reversed(tips)) and len(digest) == 64
    assert digest != ledger.anchor_digest([(1, 3, "a" * 64), (2, 1, "c" * 64)])
    assert digest != ledger.anchor_digest(tips[:1])
    current = {(1, 3): "a" * 64, (1, 4): "d" * 64, (2, 1): "b" * 64}
    # 鎖が伸びただけなら、控えた行は同じまま。
    assert ledger.rewritten_chains(tips, current) == []
    assert ledger.rewritten_chains(tips, {**current, (2, 1): "c" * 64}) == [2]
    # 控えた行が無くなっても見つかる。
    assert ledger.rewritten_chains(tips, {(2, 1): "b" * 64}) == [1]


def test_t58_compare_never_treats_a_missing_period_as_zero() -> None:
    lines = [
        CompareLine("架空A", 121, (100, 90, 110)),
        CompareLine("架空B", 130, (100, 90, 110)),
        CompareLine("架空C", 71, (100, 90, 110)),
        CompareLine("架空D", 69, (100, 90, 110)),
        # 過去が無い・今期が無いは、0 と比べない。
        CompareLine("架空E", 5000, (None, None, None)),
        CompareLine("架空F", 5000, ()),
        CompareLine("架空G", None, (100, None, None)),
        # 過去が1期だけでも、ある分で比べる。0 は「0円だった」で、無いのとは違う。
        CompareLine("架空H", 120, (None, 100, None)),
        CompareLine("架空I", 1, (0, 0, 0)),
        # 負の金額でも、幅は -130〜-70。境目は中、1円外は外。
        CompareLine("架空J", -130, (-100, -90, -110)),
        CompareLine("架空K", -131, (-100, -90, -110)),
    ]
    result = compare(lines)
    # 幅 = 90〜110 に、平均100の20%（20）を足して 70〜130。
    assert (result.compared, result.flagged, result.uncomparable) == (8, 3, 3)
    assert [(mark.account, mark.reason) for mark in result.marks] == [
        ("架空D", MarkReason.OUTSIDE),
        ("架空E", MarkReason.NO_PAST),
        ("架空F", MarkReason.NO_PAST),
        ("架空G", MarkReason.NO_CURRENT),
        ("架空I", MarkReason.OUTSIDE),
        ("架空K", MarkReason.OUTSIDE),
    ]
    assert result.marks[0].low == 70 and result.marks[0].high == 130
    assert compare(lines) == result
    # 余裕が無ければ、過去の最小〜最大の外はすべて印（比べた8つ）。
    assert compare(lines, 0).flagged == 8
    with pytest.raises(ValueError):
        compare(lines, 10001)


def test_t62_distance_is_pure_and_the_edge_is_inside() -> None:
    # 海の上の、架空の地点。
    here = (10_000_000, 150_000_000)
    assert distance_m(*here, *here) == 0
    # 緯度1度は、およそ 111.19km。
    assert distance_m(0, 0, 1_000_000, 0) == 111_194
    assert distance_m(0, 0, 1_000_000, 0) == distance_m(1_000_000, 0, 0, 0)
    # 日付変更線をまたいでも、近いものは近い。
    assert distance_m(0, 179_999_000, 0, -179_999_000) == 222
    there = (10_001_500, 150_000_000)
    apart = distance_m(*here, *there)
    assert 160 < apart < 170
    assert is_near(*here, *there) and is_near(*here, *there, apart)
    assert not is_near(*here, *there, apart - 1)
    assert not is_near(*here, 10_001_900, 150_000_000)
    assert NEAR_RADIUS_M == 200
    for bad in ((90_000_001, 0), (0, 180_000_001), (-90_000_001, 0)):
        with pytest.raises(ValueError):
            distance_m(*bad, 0, 0)
    with pytest.raises(ValueError):
        is_near(*here, *there, -1)


def test_t16_t17_region_threshold_and_total() -> None:
    rows = [RegionRow("架空町", 4, 7, 0, 9, 12), RegionRow("架空市", 5, 2, 0, 8, 10)]
    result = mask_region(rows, 5)
    assert result.rows[0].metrics == RegionMetrics(None, None, None, None, None)
    assert result.rows[1].metrics == RegionMetrics(5, None, 0, 8, 10)
    assert result.total is None


def test_region_total_cannot_reveal_one_suppressed_cell() -> None:
    rows = [RegionRow("架空町", 6, 3, 0, 9, 12), RegionRow("架空市", 5, 8, 0, 8, 10)]
    assert mask_region(rows, 5).total == RegionMetrics(11, None, 0, 17, 22)


def test_t48_chart_cells_hide_small_offices_and_what_subtraction_reveals() -> None:
    cell = RegionCell
    assert mask_cells([cell(5, 20), cell(0, 0)], 5) == MaskedCells((20, 0), 20)
    # 4つの事務所のマスは伏せる。0でないいちばん小さいマスも伏せる。
    assert mask_cells(
        [cell(4, 12), cell(5, 30), cell(6, 9), cell(0, 0)], 5
    ) == MaskedCells((None, 30, None, 0), 51)
    # ほかに伏せるマスが無ければ、合計を出さない。
    assert mask_cells([cell(4, 12), cell(0, 0)], 5) == MaskedCells((None, 0), None)
    # 2つ伏せたなら、合計は出す。
    assert mask_cells([cell(1, 3), cell(2, 4), cell(5, 30)], 5) == MaskedCells(
        (None, None, 30), 37
    )
    for broken in ([cell(0, 3)], [cell(5, -1)], [cell(-1, 0)]):
        with pytest.raises(ValueError):
            mask_cells(broken, 5)
    with pytest.raises(ValueError):
        mask_cells([cell(5, 20)], 0)


def test_t48_breakdown_is_all_or_nothing_and_months_follow_today() -> None:
    assert mask_breakdown([5, 0, 7], 5) == (5, 0, 7)
    assert mask_breakdown([5, 4, 0], 5) is None
    with pytest.raises(ValueError):
        mask_breakdown([5, -1, 0], 5)
    assert recent_months(date(2030, 2, 10), 6) == (
        "2029-09",
        "2029-10",
        "2029-11",
        "2029-12",
        "2030-01",
        "2030-02",
    )
    assert recent_months(date(2030, 12, 31), 1) == ("2030-12",)


def test_t41_translation_is_required_even_with_otherwise_full_score() -> None:
    result = match(
        NEED,
        [replace(TALENT, translation_verified=False)],
        {("架空町", "架空市"): 15},
        TODAY,
        MatchLimits(),
    )
    assert not result.candidates
    assert result.excluded == {Exclusion.TRANSLATION: 1}


def test_t51_only_codes_starting_with_nine_are_fictional() -> None:
    assert all(is_fictional(code) for code in ("90001", "99999", "9"))
    assert not any(is_fictional(code) for code in ("01100", "47382", "00001", "09201"))
    assert not is_fictional("")
