"""v2 里程碑 5 灰度切换：v1/v2 机构解析管线版本与回退（plan.md 第三部分）。"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from ashare_hotpot.config import AppSettings, SHANGHAI_TZ
from ashare_hotpot.institution_metrics import ResearchBoardService
from ashare_hotpot.institutions import InstitutionRegistry
from ashare_hotpot.models import SourceDocument
from ashare_hotpot.research_activities import parse_research_activity
from ashare_hotpot.research_views import build_discovery_quality
from ashare_hotpot.storage import Storage

EVAL_DIR = Path(__file__).resolve().parents[1] / "scripts" / "evaluation"
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))
import compare_pipeline_versions  # noqa: E402


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=SHANGHAI_TZ)


def _document(body: str, document_id: str, code: str = "300999") -> SourceDocument:
    return SourceDocument(
        document_id=document_id,
        provider_key="cninfo",
        provider_name="巨潮资讯",
        kind="research_activity",
        source_url=f"https://example.test/list?{document_id}",
        document_url=None,
        title="XX科技投资者关系活动记录表",
        published_at=NOW,
        stock_codes=(code,),
        body_text=body,
        content_hash=f"hash-{document_id}",
        parse_status="parsed",
        parse_error=None,
    )


COLON_LIST_BODY = (
    "参与单位名称\n"
    "国新投资：马雨浓、肖泽中\n"
    "盘京投资：王莉\n"
    "中信证券：程子盈\n"
    "时间 2026 年 6 月 16 日\n"
)


def _names(storage: Storage, result) -> set[str]:
    if result is None:
        return set()
    return {
        storage.get_institution(p.institution_id).canonical_name
        for p in result.participants
    }


def test_v2_default_parses_colon_list_and_v1_legacy_does_not(
    tmp_path: Path,
) -> None:
    """v1 兼容口径（整篇行级、旧后缀集）对“机构：姓名”名单不提取；
    v2 默认提取（灰度切换的可验证差异点）。"""

    document = _document(COLON_LIST_BODY, "doc-ver")
    storage_v1 = Storage(tmp_path / "v1.db")
    storage_v2 = Storage(tmp_path / "v2.db")
    result_v1 = parse_research_activity(
        document, InstitutionRegistry(storage_v1), pipeline_version="v1"
    )
    result_v2 = parse_research_activity(
        document, InstitutionRegistry(storage_v2), pipeline_version="v2"
    )
    names_v1 = _names(storage_v1, result_v1)
    names_v2 = _names(storage_v2, result_v2)
    assert "国新投资" not in names_v1
    assert "盘京投资" not in names_v1
    assert "国新投资" in names_v2
    assert "上海盘京投资管理中心（有限合伙）" in names_v2
    assert "中信证券股份有限公司" in names_v2
    # v1 提及解析版本标记。
    assert result_v1 is not None
    assert all(
        m.parse_version == "v1-legacy"
        for m in result_v1.raw_mentions
    )
    assert result_v2 is not None
    assert all(
        m.parse_version == "v2-20260809"
        for m in result_v2.raw_mentions
    )


def test_unknown_pipeline_version_raises(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    with pytest.raises(ValueError):
        parse_research_activity(
            _document(COLON_LIST_BODY, "doc-x"),
            InstitutionRegistry(storage),
            pipeline_version="v3",
        )


def test_service_respects_pipeline_version_setting_and_override(
    tmp_path: Path,
) -> None:
    """settings.research_pipeline_version 控制解析管线；run 参数可覆盖；
    结果记录实际管线版本。"""

    storage = Storage(tmp_path / "hotpot.db")
    storage.upsert_source_document(_document(COLON_LIST_BODY, "doc-svc"), NOW)
    settings_v1 = AppSettings(app_root=tmp_path)
    settings_v1.research_pipeline_version = "v1"
    result_v1 = ResearchBoardService(settings_v1, storage).run(now=NOW)
    assert result_v1.pipeline_version == "v1"
    assert result_v1.documents_scanned == 1
    # v1 只命中旧后缀集（中信证券），不提取“国新投资/盘京投资”（投资/资产
    # 为 M4 扩展后缀）。
    assert result_v1.participants_added == 1

    result_v2 = ResearchBoardService(settings_v1, storage).run(
        now=NOW, pipeline_version="v2"
    )
    assert result_v2.pipeline_version == "v2"
    assert result_v2.participants_added == 3

    settings_default = AppSettings(app_root=tmp_path)
    assert settings_default.research_pipeline_version == "v2"
    with pytest.raises(ValueError):
        ResearchBoardService(settings_default, storage).run(
            now=NOW, pipeline_version="v3"
        )


def test_discovery_quality_shows_v1_rollback_marker(tmp_path: Path) -> None:
    """UI 数据质量文本：v1 回退模式下必须可见“v1 兼容口径”。"""

    storage = Storage(tmp_path / "hotpot.db")
    settings_default = AppSettings(app_root=tmp_path)
    text_default = build_discovery_quality(settings_default, storage)
    assert "v1 兼容口径" not in text_default

    settings_v1 = AppSettings(app_root=tmp_path)
    settings_v1.research_pipeline_version = "v1"
    text_v1 = build_discovery_quality(settings_v1, storage)
    assert "v1 兼容口径" in text_v1


def test_compare_pipeline_versions_script_reports_diffs(
    tmp_path: Path,
) -> None:
    """并行比较脚本：只读打开副本，输出 v1/v2 逐活动差异报告。"""

    db = tmp_path / "hotpot.db"
    storage = Storage(db)
    storage.upsert_source_document(_document(COLON_LIST_BODY, "doc-a"), NOW)
    storage.upsert_source_document(
        _document(
            "参与单位名称\n中信证券、中金公司\n时间 2026 年 7 月 9 日\n",
            "doc-b",
        ),
        NOW,
    )
    out = tmp_path / "report.json"
    exit_code = compare_pipeline_versions.main(
        ["--db", str(db), "--out", str(out), "--limit", "10"]
    )
    assert exit_code == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["activities_compared"] == 2
    assert report["participant_totals"]["v2_only"] >= 2
    by_doc = {item["document_id"]: item for item in report["per_activity"]}
    # doc-a（“机构：姓名”）v2 提取而 v1 不提取。
    assert "国新投资" in by_doc["doc-a"]["v2_only"]
    # doc-b（顿号名单）两版都提取。
    assert by_doc["doc-b"]["common"]


def test_v2_parses_attachment_table_and_time_order_note(tmp_path: Path) -> None:
    """v2 名单章节定位补齐：①“附件：”表格（序号 姓名 公司名称）；
    ②“（时间先后顺序排列）”名单说明不是章节边界（矽电/迪普回归）。"""

    storage = Storage(tmp_path / "hotpot.db")
    registry = InstitutionRegistry(storage)
    attachment_table = (
        "参与单位名称\n"
        "本次线上会议在线参会主要人员信息详见附件。\n"
        "时间 2026 年 8 月 6 日\n"
        "附件： （排名不分先后顺序）\n"
        "序号 姓名 公司名称\n"
        "1 尤娜 Admiralty Harbour Capital Limited\n"
        "20 江俊晨 上海光大证券资产管理有限公司\n"
        "29 曹添雨 中信建投证券股份有限公司\n"
    )
    result = parse_research_activity(
        _document(attachment_table, "doc-attach"),
        registry,
        pipeline_version="v2",
    )
    names = _names(storage, result)
    assert "Admiralty Harbour Capital Limited" in names
    assert "上海光大证券资产管理有限公司" in names
    assert "中信建投证券股份有限公司" in names

    storage2 = Storage(tmp_path / "hotpot2.db")
    registry2 = InstitutionRegistry(storage2)
    time_order_note = (
        "参与单位名称\n"
        "（时间先后顺序排列）\n"
        "华泰证券、人保资产、中信证券、方正证券\n"
        "时间 2026 年 7 月 1 日\n"
    )
    result2 = parse_research_activity(
        _document(time_order_note, "doc-note"),
        registry2,
        pipeline_version="v2",
    )
    names2 = _names(storage2, result2)
    assert "华泰证券股份有限公司" in names2
    assert "中国人保资产管理有限公司" in names2
    assert "中信证券股份有限公司" in names2
    assert "方正证券股份有限公司" in names2
