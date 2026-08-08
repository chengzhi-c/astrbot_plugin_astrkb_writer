"""main.py 纯函数、AstrKBTool 分发与 _allowed 权限逻辑测试。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("astrbot")

from astrbot_plugin_astrkb_writer.core.astrkb_bridge import _clamp_int  # noqa: E402
from astrbot_plugin_astrkb_writer.main import (  # noqa: E402
    AstrKBWriterPlugin,
    AstrKBTool,
    _int_arg,
    _schema,
    _str_arg,
)


class FakeContext:
    def add_llm_tools(self, *tools):
        pass


class FakePlugin:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def astrkb_list_kbs(self, event):
        self.calls.append(("list_kbs", {}))
        return "kbs"

    async def astrkb_list_documents(self, event, kb_name="", limit=20):
        self.calls.append(("list_documents", {"kb_name": kb_name, "limit": limit}))
        return "docs"

    async def astrkb_write_document(self, event, title, content, kb_name=""):
        self.calls.append(
            ("write_document", {"title": title, "content": content, "kb_name": kb_name}),
        )
        return "written"

    async def astrkb_update_document(self, event, doc_id, content, title="", kb_name=""):
        self.calls.append(
            (
                "update_document",
                {"doc_id": doc_id, "content": content, "title": title, "kb_name": kb_name},
            ),
        )
        return "updated"

    async def astrkb_delete_document(self, event, doc_id, kb_name=""):
        self.calls.append(("delete_document", {"doc_id": doc_id, "kb_name": kb_name}))
        return "deleted"


def _tool(plugin: FakePlugin, operation: str) -> AstrKBTool:
    return AstrKBTool(plugin=plugin, name=f"t_{operation}", operation=operation)


def _run(tool: AstrKBTool, **kwargs) -> str:
    wrapper = SimpleNamespace(context=SimpleNamespace(event=None))
    return asyncio.run(tool.call(wrapper, **kwargs))


def test_clamp_int_bounds() -> None:
    assert _clamp_int(999, 20, 1, 100) == 100
    assert _clamp_int(-5, 20, 1, 100) == 1
    assert _clamp_int("junk", 20, 1, 100) == 20
    assert _clamp_int(None, 20, 1, 100) == 20


def test_int_arg_converts_without_clamping() -> None:
    assert _int_arg({"limit": "42"}, "limit", 20) == 42
    assert _int_arg({"limit": "abc"}, "limit", 20) == 20
    assert _int_arg({}, "limit", 20) == 20


def test_int_arg_rejects_bool() -> None:
    # bool 是 int 子类，显式拦截防 int(True) -> 1 静默截断
    assert _int_arg({"limit": True}, "limit", 20) == 20
    assert _int_arg({"limit": False}, "limit", 20) == 20


def test_int_arg_still_converts_str_number() -> None:
    assert _int_arg({"limit": "5"}, "limit", 20) == 5
    assert _int_arg({"limit": 5}, "limit", 20) == 5


def test_str_arg_handles_missing_none_and_value() -> None:
    assert _str_arg({}, "kb_name") == ""
    assert _str_arg({"kb_name": None}, "kb_name") == ""
    assert _str_arg({"kb_name": "wiki"}, "kb_name") == "wiki"


def test_schema_defaults_and_required() -> None:
    assert _schema() == {"type": "object", "properties": {}, "required": []}
    s = _schema({"a": {"type": "string"}}, ["a"])
    assert s["required"] == ["a"]


def test_tool_dispatch_all_operations() -> None:
    plugin = FakePlugin()
    assert _run(_tool(plugin, "list_kbs")) == "kbs"
    assert _run(_tool(plugin, "list_documents"), kb_name="wiki", limit="5") == "docs"
    assert plugin.calls[-1] == ("list_documents", {"kb_name": "wiki", "limit": 5})
    assert _run(_tool(plugin, "write_document"), title="t", content="c") == "written"
    assert _run(
        _tool(plugin, "update_document"),
        doc_id="d",
        content="c",
        title="nt",
        kb_name="wiki",
    ) == "updated"
    assert plugin.calls[-1] == (
        "update_document",
        {"doc_id": "d", "content": "c", "title": "nt", "kb_name": "wiki"},
    )
    assert _run(_tool(plugin, "delete_document"), doc_id="d", kb_name="wiki") == "deleted"
    assert plugin.calls[-1] == ("delete_document", {"doc_id": "d", "kb_name": "wiki"})
    assert len(plugin.calls) == 5


def test_tool_dispatch_none_kwargs_become_empty_strings() -> None:
    plugin = FakePlugin()
    _run(_tool(plugin, "write_document"), title=None, content="c", kb_name=None)
    _, args = plugin.calls[-1]
    assert args == {"title": "", "content": "c", "kb_name": ""}


def test_tool_dispatch_unknown_operation() -> None:
    assert _run(_tool(FakePlugin(), "nope")) == "未知 AstrBot 原生知识库工具操作。"


class SyncAdminEvent:
    def __init__(self, value: bool):
        self._value = value

    def is_admin(self) -> bool:
        return self._value


class AsyncAdminEvent:
    def __init__(self, value: bool):
        self._value = value

    async def is_admin(self) -> bool:
        return self._value


class NoAdminEvent:
    pass


def _plugin(admin_only: bool) -> AstrKBWriterPlugin:
    return AstrKBWriterPlugin(FakeContext(), config={"admin_only": admin_only})


def test_allowed_bypass_when_admin_only_off() -> None:
    plugin = _plugin(admin_only=False)
    assert asyncio.run(plugin._allowed(NoAdminEvent())) is True


def test_allowed_sync_admin_checks() -> None:
    plugin = _plugin(admin_only=True)
    assert asyncio.run(plugin._allowed(SyncAdminEvent(True))) is True
    assert asyncio.run(plugin._allowed(SyncAdminEvent(False))) is False


def test_allowed_async_admin_checks() -> None:
    plugin = _plugin(admin_only=True)
    assert asyncio.run(plugin._allowed(AsyncAdminEvent(True))) is True
    assert asyncio.run(plugin._allowed(AsyncAdminEvent(False))) is False


def test_allowed_missing_is_admin_denied() -> None:
    plugin = _plugin(admin_only=True)
    assert asyncio.run(plugin._allowed(NoAdminEvent())) is False


class InternalErrorBridge:
    """模拟非 ValueError 的内部错误（真实场景：向量库/文件系统层异常）。"""

    async def write_document(self, **kwargs):
        raise RuntimeError("vec db locked at C:\\secret\\internal\\path")


class UserErrorBridge:
    """模拟 bridge 层面向用户的语义错误。"""

    async def write_document(self, **kwargs):
        raise ValueError("内容过长：30000 > 20000")


def test_write_internal_error_hides_detail() -> None:
    """内部错误只给通用文案，底层异常细节（含路径）不落入回复。"""
    plugin = _plugin(admin_only=False)
    plugin.bridge = InternalErrorBridge()
    msg = asyncio.run(plugin.astrkb_write_document(None, "t", "c"))
    assert msg.startswith("写入 AstrBot 原生知识库失败：")
    assert "内部错误" in msg
    assert "secret" not in msg and "path" not in msg


def test_write_valueerror_keeps_user_facing_message() -> None:
    """bridge 层语义错误（ValueError）保持透出，用户可据此修正。"""
    plugin = _plugin(admin_only=False)
    plugin.bridge = UserErrorBridge()
    msg = asyncio.run(plugin.astrkb_write_document(None, "t", "c"))
    assert "内容过长" in msg
    assert "内部错误" not in msg


def test_per_kb_lock_isolates_different_kbs() -> None:
    """不同知识库持有不同锁对象 → 跨库写不互相阻塞。"""
    plugin = _plugin(admin_only=False)
    assert plugin._lock_for("kb-a") is not plugin._lock_for("kb-b")


def test_per_kb_lock_reuses_same_kb() -> None:
    """同一知识库复用同一锁对象 → 同库写串行。"""
    plugin = _plugin(admin_only=False)
    assert plugin._lock_for("kb-a") is plugin._lock_for("kb-a")


def test_per_kb_lock_defaults_to_configured_kb() -> None:
    """未指定 kb_name 时落到默认知识库的锁。"""
    plugin = _plugin(admin_only=False)
    assert plugin._lock_for("") is plugin._lock_for(plugin.settings.default_kb_name)
