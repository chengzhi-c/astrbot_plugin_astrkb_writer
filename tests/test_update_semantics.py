"""桥接层语义测试：update 部分成功、delete 行为与返回结构契约。

update 红灯语义：新文档写入成功后旧文档删除失败不得上抛，
须返回部分成功结果并带 old_doc_deleted=False 标记。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from astrbot_plugin_astrkb_writer.core.astrkb_bridge import (
    AstrBotKBBridge,
    NativeKBConfig,
)


class FakeChunker:
    async def chunk(self, content, chunk_size=512, chunk_overlap=50):
        return [content]


class FakeDoc:
    def __init__(self, doc_id: str = "new-id", doc_name: str = "new.txt"):
        self.doc_id = doc_id
        self.doc_name = doc_name
        self.chunk_count = 1

    def model_dump(self):
        return {
            "doc_id": self.doc_id,
            "doc_name": self.doc_name,
            "chunk_count": self.chunk_count,
        }


class FakeHelper:
    def __init__(self, delete_raises: bool = False):
        self.kb = SimpleNamespace(kb_id="kb-1", kb_name="kb")
        self.chunker = FakeChunker()
        self.delete_raises = delete_raises
        self.uploaded: list[str] = []
        self.deleted: list[str] = []

    async def get_document(self, doc_id: str):
        return SimpleNamespace(doc_name="old.txt")

    async def upload_document(self, file_name, file_content, file_type, pre_chunked_text=None):
        self.uploaded.append(file_name)
        return FakeDoc()

    async def delete_document(self, doc_id: str):
        if self.delete_raises:
            raise RuntimeError("vec db locked")
        self.deleted.append(doc_id)


class FakeManager:
    def __init__(self, helper):
        self.helper = helper

    async def get_kb_by_name(self, kb_name: str):
        return self.helper


def _bridge(helper: FakeHelper) -> AstrBotKBBridge:
    return AstrBotKBBridge(
        context=SimpleNamespace(kb_manager=FakeManager(helper)),
        config=NativeKBConfig(default_kb_name="kb"),
        plugin_name="test",
    )


def test_update_old_delete_failure_reports_partial_success() -> None:
    helper = FakeHelper(delete_raises=True)
    result = asyncio.run(_bridge(helper).update_document("old-id", "new content"))
    assert result["action"] == "updated"
    assert result["old_doc_id"] == "old-id"
    assert result["old_doc_deleted"] is False
    assert len(helper.uploaded) == 1
    assert helper.deleted == []


def test_update_happy_path_deletes_old_doc() -> None:
    helper = FakeHelper()
    result = asyncio.run(
        _bridge(helper).update_document("old-id", "new content", title="新标题"),
    )
    assert result["action"] == "updated"
    assert result["old_doc_id"] == "old-id"
    assert result["old_doc_deleted"] is True
    assert helper.deleted == ["old-id"]
    # 新标题须真正透传到上传的文件名
    assert helper.uploaded == ["新标题.txt"]


def test_update_without_title_reuses_old_doc_name() -> None:
    helper = FakeHelper()
    asyncio.run(_bridge(helper).update_document("old-id", "new content"))
    # 旧文档名 old.txt 去扩展名后沿用
    assert helper.uploaded == ["old.txt"]


def test_update_missing_doc_raises() -> None:
    class NoDocHelper(FakeHelper):
        async def get_document(self, doc_id: str):
            return None

    helper = NoDocHelper()
    with pytest.raises(ValueError):
        asyncio.run(_bridge(helper).update_document("ghost", "content"))


def test_write_result_shape_contract() -> None:
    helper = FakeHelper()
    result = asyncio.run(_bridge(helper).write_document("标题", "内容"))
    assert {"action", "kb_id", "kb_name", "doc"} <= set(result)
    assert result["action"] == "created"
    assert result["doc"]["doc_id"] == "new-id"


def test_update_result_shape_contract() -> None:
    helper = FakeHelper()
    result = asyncio.run(_bridge(helper).update_document("old-id", "新内容"))
    assert {"action", "kb_id", "kb_name", "doc", "old_doc_id", "old_doc_deleted"} <= set(result)
    assert result["old_doc_id"] == "old-id"
    assert result["old_doc_deleted"] is True


def test_delete_happy_path() -> None:
    class DocHelper(FakeHelper):
        async def get_document(self, doc_id: str):
            return FakeDoc(doc_id=doc_id)

    helper = DocHelper()
    result = asyncio.run(_bridge(helper).delete_document("old-id"))
    assert result["action"] == "deleted"
    assert result["kb_id"] == "kb-1"
    assert result["kb_name"] == "kb"
    assert result["doc"]["doc_id"] == "old-id"
    assert helper.deleted == ["old-id"]


def test_delete_missing_doc_raises() -> None:
    class NoDocHelper(FakeHelper):
        async def get_document(self, doc_id: str):
            return None

    with pytest.raises(ValueError):
        asyncio.run(_bridge(NoDocHelper()).delete_document("ghost"))


def test_delete_missing_kb_raises() -> None:
    bridge = AstrBotKBBridge(
        context=SimpleNamespace(kb_manager=FakeManager(None)),
        config=NativeKBConfig(default_kb_name="kb"),
        plugin_name="test",
    )
    with pytest.raises(ValueError):
        asyncio.run(bridge.delete_document("d1"))
