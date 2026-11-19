from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from tsunagu_rules import RULES_VERSION, ledger
from tsunagu_rules.matching import match
from tsunagu_rules.region import mask_region
from tsunagu_rules.translation import load_dictionary, translate
from tsunagu_rules.types import (
    Exclusion,
    LedgerEntry,
    MatchLimits,
    Need,
    NeedKind,
    Reduction,
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
        "同じ会計ソフトを使えます",
        "資料回収が得意です",
        "車で25分：毎週通える距離です",
        "確かめられた訪問が8回あります",
        "月曜に通えます",
    )


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


def test_t16_t17_region_threshold_and_total() -> None:
    rows = [RegionRow("架空町", 4, 7, 0, 9, 12), RegionRow("架空市", 5, 2, 0, 8, 10)]
    result = mask_region(rows, 5)
    assert result.rows[0].metrics == RegionMetrics(None, None, None, None, None)
    assert result.rows[1].metrics == RegionMetrics(5, None, 0, 8, 10)
    assert result.total is None


def test_region_total_cannot_reveal_one_suppressed_cell() -> None:
    rows = [RegionRow("架空町", 6, 3, 0, 9, 12), RegionRow("架空市", 5, 8, 0, 8, 10)]
    assert mask_region(rows, 5).total == RegionMetrics(11, None, 0, 17, 22)
