"""插件加载冒烟：真实 AstrBot 环境下工具注册、优雅降级与配置钳制。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("astrbot")

from astrbot_plugin_astrkb_writer.main import (  # noqa: E402
    AstrKBWriterPlugin,
)

EXPECTED_TOOLS = {
    "astrkb_list_kbs",
    "astrkb_list_documents",
    "astrkb_write_document",
    "astrkb_update_document",
    "astrkb_delete_document",
}


class FakeContext:
    def __init__(self):
        self.added_tools = []

    def add_llm_tools(self, *tools):
        self.added_tools.extend(tools)


class BrokenContext(FakeContext):
    def add_llm_tools(self, *tools):
        raise RuntimeError("provider manager not ready")


class FakeLLMTools:
    def __init__(self, remove_raises: bool = False):
        self.remove_raises = remove_raises
        self.removed: list[str] = []

    def remove_func(self, name: str):
        if self.remove_raises:
            raise RuntimeError("tool registry locked")
        self.removed.append(name)


def _plugin(config=None) -> AstrKBWriterPlugin:
    return AstrKBWriterPlugin(FakeContext(), config=config)


def test_plugin_declares_five_tools() -> None:
    plugin = _plugin()
    assert {tool.name for tool in plugin.tools} == EXPECTED_TOOLS


def test_initialize_registers_tools_and_degrades_without_kb_manager() -> None:
    plugin = _plugin()
    asyncio.run(plugin.initialize())
    assert plugin._tools_registered is True
    assert {tool.name for tool in plugin.context.added_tools} == EXPECTED_TOOLS


def test_terminate_unregisters_tools_via_provider_manager() -> None:
    plugin = _plugin()
    asyncio.run(plugin.initialize())
    llm_tools = FakeLLMTools()
    plugin.context.provider_manager = SimpleNamespace(llm_tools=llm_tools)
    asyncio.run(plugin.terminate())
    assert plugin._tools_registered is False
    assert set(llm_tools.removed) == EXPECTED_TOOLS


def test_terminate_swallows_remove_errors() -> None:
    plugin = _plugin()
    asyncio.run(plugin.initialize())
    plugin.context.provider_manager = SimpleNamespace(llm_tools=FakeLLMTools(remove_raises=True))
    asyncio.run(plugin.terminate())
    assert plugin._tools_registered is False


def test_initialize_survives_tool_registration_failure() -> None:
    plugin = AstrKBWriterPlugin(BrokenContext(), config={})
    asyncio.run(plugin.initialize())
    assert plugin._tools_registered is False
    # 指令能力不依赖工具注册，插件整体仍可用
    assert plugin.bridge is not None


def test_config_clamps_chunk_overlap_below_chunk_size() -> None:
    plugin = _plugin({"chunk_size": 100, "chunk_overlap": 500})
    assert plugin.settings.chunk_size == 100
    assert plugin.settings.chunk_overlap == 99


def test_config_defaults_default_kb_name() -> None:
    plugin = _plugin({})
    assert plugin.settings.default_kb_name == "Bot自由知识库"


class FakeKBManager:
    def __init__(self):
        self.list_calls = 0

    async def list_kbs(self):
        self.list_calls += 1
        return []


def test_initialize_happy_path_with_kb_manager() -> None:
    context = FakeContext()
    context.kb_manager = FakeKBManager()
    plugin = AstrKBWriterPlugin(context, config={})
    asyncio.run(plugin.initialize())
    assert plugin._tools_registered is True
    assert {tool.name for tool in context.added_tools} == EXPECTED_TOOLS
    assert context.kb_manager.list_calls == 1


class ThrowingBridge:
    async def write_document(self, **kwargs):
        raise RuntimeError("boom")
    async def update_document(self, **kwargs):
        raise RuntimeError("boom")
    async def delete_document(self, **kwargs):
        raise RuntimeError("boom")


def test_plugin_methods_convert_bridge_errors_to_messages() -> None:
    plugin = _plugin({"admin_only": False})
    plugin.bridge = ThrowingBridge()
    assert asyncio.run(plugin.astrkb_write_document(None, "t", "c")).startswith("写入 AstrBot 原生知识库失败：")
    assert asyncio.run(plugin.astrkb_update_document(None, "d", "c")).startswith("更新 AstrBot 原生知识库失败：")
    plugin.enable_delete = True
    assert asyncio.run(plugin.astrkb_delete_document(None, "d")).startswith("删除 AstrBot 原生知识库文档失败：")
