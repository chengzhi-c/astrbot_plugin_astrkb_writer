"""写入去重：标题键、分页扫描、create/skip/update、显式 update 不理政策。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from astrbot_plugin_astrkb_writer.core.astrkb_bridge import (
    AstrBotKBBridge,
    NativeKBConfig,
)


class FakeDoc:
    def __init__(
        self,
        doc_id: str,
        doc_name: str,
        created_at: int = 0,
        chunk_count: int = 1,
    ):
        self.doc_id = doc_id
        self.doc_name = doc_name
        self.created_at = created_at
        self.chunk_count = chunk_count
        self.file_type = "txt"
        self.file_size = 10

    def model_dump(self):
        return {
            "doc_id": self.doc_id,
            "doc_name": self.doc_name,
            "chunk_count": self.chunk_count,
            "created_at": self.created_at,
        }


class FakeChunker:
    async def chunk(self, content, chunk_size=512, chunk_overlap=50):
        self.last = (chunk_size, chunk_overlap)
        return [content]


class FakeHelper:
    def __init__(self, docs=None, kb_name: str = "kb"):
        self.kb = SimpleNamespace(
            kb_id="kb-1",
            kb_name=kb_name,
            chunk_size=512,
            chunk_overlap=50,
        )
        self.chunker = FakeChunker()
        self.docs = list(docs or [])
        self.uploaded: list[str] = []
        self.deleted: list[str] = []
        self._seq = 0
        self.received_calls: list[tuple[int, int]] = []

    async def list_documents(self, offset: int = 0, limit: int = 100):
        self.received_calls.append((offset, limit))
        ordered = sorted(self.docs, key=lambda d: d.created_at, reverse=True)
        return ordered[offset : offset + limit]

    async def get_document(self, doc_id: str):
        for doc in self.docs:
            if doc.doc_id == doc_id:
                return doc
        return None

    async def upload_document(self, file_name, file_content, file_type, pre_chunked_text=None):
        self._seq += 1
        doc = FakeDoc(f"new-{self._seq}", file_name, created_at=10_000 + self._seq)
        self.docs.append(doc)
        self.uploaded.append(file_name)
        return doc

    async def delete_document(self, doc_id: str):
        self.deleted.append(doc_id)
        self.docs = [doc for doc in self.docs if doc.doc_id != doc_id]


class FakeManager:
    def __init__(self, helper):
        self.helper = helper

    async def get_kb_by_name(self, kb_name: str):
        return self.helper


def _cfg(**kwargs) -> NativeKBConfig:
    values = {"default_kb_name": "kb"}
    values.update(kwargs)
    cfg = NativeKBConfig(default_kb_name=values["default_kb_name"])
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


def _bridge(helper, **cfg) -> AstrBotKBBridge:
    return AstrBotKBBridge(
        context=SimpleNamespace(kb_manager=FakeManager(helper)),
        config=_cfg(**cfg),
        plugin_name="test",
    )


def test_create_policy_always_uploads_even_if_same_name() -> None:
    helper = FakeHelper([FakeDoc("old", "标题.txt", created_at=1)])
    result = asyncio.run(_bridge(helper, duplicate_policy="create").write_document("标题", "内容"))
    assert result["action"] == "created"
    assert len(helper.uploaded) == 1
    assert len(helper.docs) == 2


def test_skip_policy_returns_latest_same_name_without_upload() -> None:
    helper = FakeHelper([
        FakeDoc("old", "标题.txt", created_at=1),
        FakeDoc("newer", "标题.txt", created_at=9),
    ])
    result = asyncio.run(_bridge(helper, duplicate_policy="skip").write_document("标题", "新内容"))
    assert result["action"] == "skipped"
    assert result["doc"]["doc_id"] == "newer"
    assert helper.uploaded == []
    assert helper.deleted == []


def test_skip_policy_creates_when_no_same_name() -> None:
    helper = FakeHelper([FakeDoc("other", "别的.txt", created_at=1)])
    result = asyncio.run(_bridge(helper, duplicate_policy="skip").write_document("标题", "内容"))
    assert result["action"] == "created"
    assert helper.uploaded == ["标题.txt"]


def test_update_policy_replaces_latest_same_name_only() -> None:
    helper = FakeHelper([
        FakeDoc("oldest", "标题.txt", created_at=1),
        FakeDoc("mid", "标题.txt", created_at=5),
        FakeDoc("latest", "标题.txt", created_at=9),
    ])
    result = asyncio.run(_bridge(helper, duplicate_policy="update").write_document("标题", "新内容"))
    assert result["action"] == "updated"
    assert result["old_doc_id"] == "latest"
    assert helper.deleted == ["latest"]
    assert len(helper.uploaded) == 1
    remaining = {doc.doc_id for doc in helper.docs}
    assert "oldest" in remaining and "mid" in remaining
    assert "latest" not in remaining


def test_update_via_write_uploads_once() -> None:
    helper = FakeHelper([FakeDoc("old", "标题.txt", created_at=1)])
    asyncio.run(_bridge(helper, duplicate_policy="update").write_document("标题", "新内容"))
    assert helper.uploaded == ["标题.txt"]
    assert helper.deleted == ["old"]


def test_skip_finds_same_name_on_second_page() -> None:
    others = [FakeDoc(f"d{i}", f"other-{i}.txt", created_at=100 + i) for i in range(100)]
    target = FakeDoc("hit", "标题.txt", created_at=1)
    helper = FakeHelper(others + [target])
    result = asyncio.run(_bridge(helper, duplicate_policy="skip").write_document("标题", "内容"))
    assert result["action"] == "skipped"
    assert result["doc"]["doc_id"] == "hit"
    assert helper.uploaded == []
    assert any(offset >= 100 for offset, _ in helper.received_calls)


def test_title_key_uses_make_file_name() -> None:
    helper = FakeHelper([FakeDoc("old", "a_b.txt", created_at=1)])
    result = asyncio.run(
        _bridge(helper, duplicate_policy="skip").write_document("a<b", "内容"),
    )
    assert result["action"] == "skipped"
    assert result["doc"]["doc_id"] == "old"


def test_skip_is_case_sensitive() -> None:
    helper = FakeHelper([FakeDoc("old", "Note.txt", created_at=1)])
    result = asyncio.run(_bridge(helper, duplicate_policy="skip").write_document("note", "内容"))
    assert result["action"] == "created"
    assert helper.uploaded == ["note.txt"]


def test_explicit_update_ignores_skip_policy() -> None:
    helper = FakeHelper([FakeDoc("old", "标题.txt", created_at=1)])
    result = asyncio.run(
        _bridge(helper, duplicate_policy="skip").update_document("old", "新内容", title="标题"),
    )
    assert result["action"] == "updated"
    assert helper.deleted == ["old"]
    assert helper.uploaded == ["标题.txt"]


def test_write_marks_truncated_title_on_update_policy() -> None:
    from astrbot_plugin_astrkb_writer.core.astrkb_bridge import TITLE_MAX_CHARS

    long_title = "标" * (TITLE_MAX_CHARS + 1)
    file_name = AstrBotKBBridge._make_file_name(long_title[:TITLE_MAX_CHARS])
    helper = FakeHelper([FakeDoc("old", file_name, created_at=1)])
    result = asyncio.run(
        _bridge(helper, duplicate_policy="update").write_document(long_title, "内容"),
    )
    assert result["action"] == "updated"
    assert result.get("title_truncated") is True


def test_skip_blank_content_still_rejected() -> None:
    helper = FakeHelper([FakeDoc("old", "标题.txt", created_at=1)])
    with pytest.raises(ValueError, match="内容不能为空"):
        asyncio.run(_bridge(helper, duplicate_policy="skip").write_document("标题", "  "))


def test_duplicate_groups_lists_twins() -> None:
    helper = FakeHelper([
        FakeDoc("a1", "A.txt", created_at=2),
        FakeDoc("a0", "A.txt", created_at=1),
        FakeDoc("b1", "B.txt", created_at=3),
        FakeDoc("b0", "B.txt", created_at=1),
        FakeDoc("c", "C.txt", created_at=1),
    ])
    groups = asyncio.run(_bridge(helper).list_duplicate_groups())
    names = {name for name, _ in groups}
    assert names == {"A.txt", "B.txt"}
    by_name = {name: docs for name, docs in groups}
    assert [d.doc_id for d in by_name["A.txt"]] == ["a1", "a0"]


def test_duplicate_groups_none() -> None:
    helper = FakeHelper([FakeDoc("a", "A.txt", created_at=1)])
    assert asyncio.run(_bridge(helper).list_duplicate_groups()) == []


def test_create_uses_kb_chunk_params() -> None:
    helper = FakeHelper()
    helper.kb.chunk_size = 200
    helper.kb.chunk_overlap = 20
    asyncio.run(_bridge(helper).write_document("标题", "内容"))
    assert helper.chunker.last == (200, 20)


def test_write_blank_chunks_raises() -> None:
    helper = FakeHelper()

    async def _blank(content, chunk_size=512, chunk_overlap=50):
        return ["  ", ""]

    helper.chunker.chunk = _blank
    with pytest.raises(ValueError, match="切分后为空"):
        asyncio.run(_bridge(helper).write_document("标题", "内容"))
