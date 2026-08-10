from __future__ import annotations

from datetime import datetime

from ashare_hotpot.config import SHANGHAI_TZ
from ashare_hotpot.institutions import (
    GROUP_ALIASES,
    INSTITUTION_TYPES,
    SEED_ALIASES,
    InstitutionRegistry,
    find_needs_review_candidates,
    infer_institution_type,
    levenshtein,
    normalize_institution_name,
    participant_qualifies,
)
from ashare_hotpot.storage import Storage


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=SHANGHAI_TZ)


def test_normalize_folds_unicode_halfwidth_and_legal_suffixes() -> None:
    assert normalize_institution_name("中信证券股份有限公司") == "中信证券"
    assert normalize_institution_name(" 中信证券股份有限公司 ") == "中信证券"
    assert normalize_institution_name("易方达基金管理有限公司") == "易方达基金管理"
    assert normalize_institution_name("泰康资产管理有限责任公司") == "泰康资产管理"
    assert normalize_institution_name("中国平安保险（集团）股份有限公司") == "中国平安保险集团"
    assert normalize_institution_name("某某私募基金（有限合伙）") == "某某私募基金"
    # 全角数字与括号折叠，地区/品牌差异保留。
    assert normalize_institution_name("招商证券（香港）") == "招商证券香港"
    assert normalize_institution_name("ＡＢＣ基金") == "abc基金"
    assert normalize_institution_name("广发证券　股份　有限公司") == "广发证券股份"


def test_seed_alias_table_contains_full_and_short_names() -> None:
    for alias in ("中信证券", "易方达基金", "中国人寿", "泰康资产", "摩根士丹利"):
        assert alias in SEED_ALIASES
    assert SEED_ALIASES["中信证券"].institution_type == "brokerage"
    assert SEED_ALIASES["易方达基金"].institution_type == "public_fund"
    assert SEED_ALIASES["中国人寿"].institution_type == "insurance"
    assert SEED_ALIASES["泰康资产"].institution_type == "asset_management"
    assert SEED_ALIASES["摩根士丹利"].verification_status == "verified"


def test_institution_type_mapping_covers_all_fixed_types() -> None:
    assert set(INSTITUTION_TYPES) == {
        "brokerage",
        "public_fund",
        "private_fund",
        "insurance",
        "asset_management",
        "foreign_institution",
        "other",
    }
    assert infer_institution_type("某证券") == "brokerage"
    assert infer_institution_type("某基金") == "public_fund"
    assert infer_institution_type("某私募基金") == "private_fund"
    assert infer_institution_type("某创投") == "private_fund"
    assert infer_institution_type("某人寿保险") == "insurance"
    assert infer_institution_type("某资产管理") == "asset_management"
    assert infer_institution_type("摩根士丹利") == "foreign_institution"
    assert infer_institution_type("某研究所") == "other"


def test_participant_qualifies_excludes_media_and_individuals() -> None:
    assert participant_qualifies("中信证券股份有限公司") is True
    assert participant_qualifies("证券时报") is False
    assert participant_qualifies("某财经网") is False
    assert participant_qualifies("个人投资者") is False
    assert participant_qualifies("王先生") is False
    assert participant_qualifies("本公司") is False


def test_registry_resolves_seed_and_persists_alias(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    registry = InstitutionRegistry(storage, now=NOW)

    institution = registry.resolve("中信证券股份有限公司")
    assert institution.institution_id == "inst:seed:citic_securities"
    assert institution.verification_status == "verified"
    assert storage.get_institution(institution.institution_id) == institution
    assert storage.resolve_institution_alias("中信证券").institution_id == (
        "inst:seed:citic_securities"
    )
    assert registry.created_count == 0

    # 简称与全称命中同一实体。
    assert registry.resolve("中信证券") == institution


def test_registry_creates_conservative_needs_review_entity(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    registry = InstitutionRegistry(storage, now=NOW)

    first = registry.resolve("未来资本投资管理有限公司")
    assert first.verification_status == "needs_review"
    assert first.group_id == first.institution_id  # 未知实体按自身成组
    assert first.institution_type == "private_fund"
    assert registry.created_count == 1

    # 同一规范化别名幂等返回同一实体，不重复创建。
    second = registry.resolve("未来资本投资管理有限公司")
    assert second == first
    assert registry.created_count == 1
    assert registry.needs_review == []


def test_group_alias_aggregates_without_merging_entities(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    registry = InstitutionRegistry(storage, now=NOW)

    fund = registry.resolve("易方达基金管理有限公司")
    asset = registry.resolve("易方达资产管理有限公司")
    hk = registry.resolve("易方达资产（香港）有限公司")
    assert fund.institution_id != asset.institution_id
    assert fund.group_id == asset.group_id == hk.group_id == "group:yifangda"
    assert GROUP_ALIASES["易方达资产"] == "group:yifangda"


def test_same_name_conflict_is_flagged_not_merged(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    registry = InstitutionRegistry(storage, now=NOW)

    institution = registry.resolve("某证券有限责任公司")
    # 同名规范化但法定形式不同：仍返回既有实体（单别名策略），但必须标记待核。
    again = registry.resolve("某证券股份有限公司")
    assert again == institution
    assert any(
        candidate.reason == "name_conflict"
        and candidate.normalized_alias == "某证券"
        for candidate in registry.needs_review
    )


def test_fuzzy_matches_are_review_candidates_only(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    registry = InstitutionRegistry(storage, now=NOW)
    aliases = ["中信证券", "华泰证券", "中信建投"]

    assert levenshtein("中信证券", "中信证券") == 0
    assert levenshtein("中信证券", "华泰证券") == 2
    assert find_needs_review_candidates("中信证券股", aliases) == ["中信证券"]

    candidates = registry.fuzzy_candidates("中信证券股", aliases)
    assert [candidate.reason for candidate in candidates] == ["fuzzy"]
    # 模糊相似绝不自动合并：库中不应出现“中信证券股”实体。
    assert storage.resolve_institution_alias("中信证券股") is None
