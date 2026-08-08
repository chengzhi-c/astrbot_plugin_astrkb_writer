"""bridge 层纯逻辑测试：归一化、文件名清洗、limit 单点钳制。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from astrbot_plugin_astrkb_writer.core.astrkb_bridge import (
    AstrBotKBBridge,
    NativeKBConfig,
)


class FakeDoc:
    def __init__(self, doc_id: str = "d1", doc_name: str = "doc.txt"):
        self.doc_id = doc_id
        self.doc_name = doc_name
        self.file_type = "txt"
        self.file_size = 10
        self.chunk_count = 2

    def model_dump(self):
        return {
            "doc_id": self.doc_id,
            "doc_name": self.doc_name,
            "chunk_count": self.chunk_count,
        }


class FakeHelper:
    def __init__(self, docs):
        self._docs = docs
        self.received_limit = None

    async def list_documents(self, offset: int = 0, limit: int = 100):
        self.received_limit = limit
        return self._docs[:limit]


class FakeManager:
    def __init__(self, helper):
        self._helper = helper

    async def get_kb_by_name(self, kb_name: str):
        return self._helper


def _bridge(helper) -> AstrBotKBBridge:
    return AstrBotKBBridge(
        context=SimpleNamespace(kb_manager=FakeManager(helper)),
        config=NativeKBConfig(default_kb_name="kb"),
        plugin_name="test",
    )


def test_normalize_title_rejects_empty() -> None:
    with pytest.raises(ValueError):
        AstrBotKBBridge._normalize_title("   ")


def test_normalize_title_truncates_to_120() -> None:
    assert len(AstrBotKBBridge._normalize_title("t" * 200)) == 120


def test_normalize_content_rejects_blank() -> None:
    with pytest.raises(ValueError):
        AstrBotKBBridge._normalize_content("\n ")


def test_make_file_name_replaces_illegal_chars() -> None:
    assert (
        AstrBotKBBridge._make_file_name('a<b>c:d"e/f\\g|h?i*j')
        == "a_b_c_d_e_f_g_h_i_j.txt"
    )


def test_make_file_name_falls_back_for_dots_only() -> None:
    # strip(".") 后为空名时才走兜底
    assert AstrBotKBBridge._make_file_name("...") == "bot-note.txt"


def test_make_file_name_replaces_each_illegal_char() -> None:
    assert AstrBotKBBridge._make_file_name("///") == "___.txt"


def test_make_file_name_truncates_to_100() -> None:
    assert AstrBotKBBridge._make_file_name("x" * 300) == "x" * 100 + ".txt"


def test_make_file_name_avoids_windows_reserved_names() -> None:
    # Windows 保留名直接写盘会被文件系统拒绝，须加前缀规避
    assert AstrBotKBBridge._make_file_name("CON") == "_CON.txt"
    # 带扩展名的标题原样保留（标题本身非文件名），仅追加 .txt 后缀
    assert AstrBotKBBridge._make_file_name("con.txt") == "_con.txt.txt"


def test_make_file_name_reserved_variant() -> None:
    assert AstrBotKBBridge._make_file_name("NUL") == "_NUL.txt"
    assert AstrBotKBBridge._make_file_name("COM1") == "_COM1.txt"


def test_list_documents_clamps_limit_to_100() -> None:
    helper = FakeHelper([FakeDoc(doc_id=f"d{i}") for i in range(5)])
    docs = asyncio.run(_bridge(helper).list_documents(limit=999))
    assert helper.received_limit == 100
    assert len(docs) == 5


def test_list_documents_clamps_limit_floor_to_1() -> None:
    helper = FakeHelper([FakeDoc()])
    asyncio.run(_bridge(helper).list_documents(limit=0))
    assert helper.received_limit == 1


def test_list_documents_missing_kb_returns_empty() -> None:
    bridge = AstrBotKBBridge(
        context=SimpleNamespace(kb_manager=FakeManager(None)),
        config=NativeKBConfig(default_kb_name="kb"),
        plugin_name="test",
    )
    assert asyncio.run(bridge.list_documents()) == []


class FakeProvider:
    """模拟真实 AstrBot Provider：无 provider_id 属性，id 存于 provider_config["id"]。"""

    def __init__(self, provider_id: str | None = None):
        self.provider_config = {"id": provider_id} if provider_id is not None else {}


class FakeProviderManager:
    def __init__(self, providers=None):
        self.embedding_provider_insts = providers or []


class FakeManagerWithCreate:
    """支持 get_kb_by_name / create_kb 的 manager，记录创建参数。"""

    def __init__(self, existing_helper=None, providers=None):
        self._existing = existing_helper
        self.created: list[dict] = []
        self.provider_manager = FakeProviderManager(providers)

    async def get_kb_by_name(self, kb_name: str):
        return self._existing

    async def create_kb(self, kb_name, description=None, emoji=None, embedding_provider_id=None, **kwargs):
        self.created.append(
            {"kb_name": kb_name, "embedding_provider_id": embedding_provider_id},
        )
        helper = SimpleNamespace(kb=SimpleNamespace(kb_id="new-kb", kb_name=kb_name))
        self._existing = helper
        return helper


def _bridge_with(manager) -> AstrBotKBBridge:
    return AstrBotKBBridge(
        context=SimpleNamespace(kb_manager=manager),
        config=NativeKBConfig(default_kb_name="kb"),
        plugin_name="test",
    )


def test_resolve_embedding_auto_selects_first_available() -> None:
    """未配置时自动选择第一个带有效 id 的 provider，跳过 id 缺失的实例。"""
    manager = FakeManagerWithCreate(
        providers=[FakeProvider(None), FakeProvider("prov-1"), FakeProvider("prov-2")],
    )
    assert asyncio.run(_bridge_with(manager)._resolve_embedding_provider_id(manager)) == "prov-1"


def test_resolve_embedding_configured_id_wins() -> None:
    """显式配置的 provider id 优先于自动选择。"""
    manager = FakeManagerWithCreate(providers=[FakeProvider("prov-auto")])
    bridge = _bridge_with(manager)
    bridge.config.default_embedding_provider_id = "prov-manual"
    assert asyncio.run(bridge._resolve_embedding_provider_id(manager)) == "prov-manual"


def test_resolve_embedding_no_provider_raises() -> None:
    manager = FakeManagerWithCreate(providers=[FakeProvider(None)])
    with pytest.raises(ValueError, match="未找到可用 embedding provider"):
        asyncio.run(_bridge_with(manager)._resolve_embedding_provider_id(manager))


def test_get_or_create_kb_creates_with_resolved_provider() -> None:
    """KB 不存在时走创建分支，解析出的 provider_id 必须透传到 create_kb。"""
    manager = FakeManagerWithCreate(existing_helper=None, providers=[FakeProvider("prov-1")])
    helper = asyncio.run(_bridge_with(manager).get_or_create_kb("newkb"))
    assert manager.created == [{"kb_name": "newkb", "embedding_provider_id": "prov-1"}]
    assert helper.kb.kb_id == "new-kb"


def test_get_or_create_kb_reuses_existing() -> None:
    """KB 已存在时不得调用 create_kb。"""
    existing = SimpleNamespace(kb=SimpleNamespace(kb_id="old-kb", kb_name="kb"))
    manager = FakeManagerWithCreate(existing_helper=existing, providers=[FakeProvider("prov-1")])
    helper = asyncio.run(_bridge_with(manager).get_or_create_kb("kb"))
    assert helper.kb.kb_id == "old-kb"
    assert manager.created == []
