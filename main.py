from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import FunctionTool
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core import logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext

from .core.astrkb_bridge import AstrBotKBBridge, DEFAULT_KB_NAME, NativeKBConfig

PLUGIN_NAME = "astrbot_plugin_astrkb_writer"
_PERMISSION_DENIED = "没有权限使用 AstrBot 原生知识库写入工具。"


def _schema(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    """构造 JSON Schema 参数对象（FunctionTool.parameters 契约）。"""
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
    }


def _str_arg(kwargs: dict[str, Any], key: str) -> str:
    """取字符串参数，缺省/None 统一归 ""。"""
    return str(kwargs.get(key, "") or "")


def _int_arg(kwargs: dict[str, Any], key: str, default: int) -> int:
    """取整型参数并做类型转换；范围钳制由 bridge 层单点负责。"""
    value = kwargs.get(key, default)
    if isinstance(value, bool):  # bool 是 int 子类，显式拦截防 1/0 误转换
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _failure(action: str, exc: Exception) -> str:
    """统一失败出口：固定前缀日志 + 文案分级（ValueError 为面向用户的语义错误，其余隐藏细节）。"""
    logger.warning(f"[{PLUGIN_NAME}] {action} failed: {exc}", exc_info=True)
    if isinstance(exc, ValueError):
        return f"{action}失败：{exc}"
    return f"{action}失败：内部错误，详情见服务端日志。"


@dataclass
class AstrKBTool(FunctionTool):
    plugin: AstrKBWriterPlugin | None = field(repr=False, default=None)  # 前向引用，future annotations 下合法
    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=_schema)
    operation: str = ""

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs: Any) -> str:
        event = context.context.event
        if self.operation == "list_kbs":
            return await self.plugin.astrkb_list_kbs(event)
        if self.operation == "list_documents":
            return await self.plugin.astrkb_list_documents(
                event,
                kb_name=_str_arg(kwargs, "kb_name"),
                limit=_int_arg(kwargs, "limit", 20),
            )
        if self.operation == "write_document":
            return await self.plugin.astrkb_write_document(
                event,
                title=_str_arg(kwargs, "title"),
                content=_str_arg(kwargs, "content"),
                kb_name=_str_arg(kwargs, "kb_name"),
            )
        if self.operation == "update_document":
            return await self.plugin.astrkb_update_document(
                event,
                doc_id=_str_arg(kwargs, "doc_id"),
                content=_str_arg(kwargs, "content"),
                title=_str_arg(kwargs, "title"),
                kb_name=_str_arg(kwargs, "kb_name"),
            )
        if self.operation == "delete_document":
            return await self.plugin.astrkb_delete_document(
                event,
                doc_id=_str_arg(kwargs, "doc_id"),
                kb_name=_str_arg(kwargs, "kb_name"),
            )
        return "未知 AstrBot 原生知识库工具操作。"


LIST_DOCUMENTS_SCHEMA = _schema(
    {
        "kb_name": {"type": "string", "description": "选填。目标 AstrBot 原生知识库名称，留空使用默认知识库。", "default": ""},
        "limit": {"type": "integer", "description": "选填。最多返回多少篇文档。", "default": 20},
    },
)
WRITE_DOCUMENT_SCHEMA = _schema(
    {
        "title": {"type": "string", "description": "必填。要写入的文档标题。"},
        "content": {"type": "string", "description": "必填。要写入知识库的正文内容。"},
        "kb_name": {"type": "string", "description": "选填。目标 AstrBot 原生知识库名称，留空使用默认知识库。", "default": ""},
    },
    ["title", "content"],
)
UPDATE_DOCUMENT_SCHEMA = _schema(
    {
        "doc_id": {"type": "string", "description": "必填。要更新的旧文档 doc_id。"},
        "content": {"type": "string", "description": "必填。更新后的完整正文内容。"},
        "title": {"type": "string", "description": "选填。新标题，留空沿用旧标题。", "default": ""},
        "kb_name": {"type": "string", "description": "选填。目标 AstrBot 原生知识库名称，留空使用默认知识库。", "default": ""},
    },
    ["doc_id", "content"],
)
DELETE_DOCUMENT_SCHEMA = _schema(
    {
        "doc_id": {"type": "string", "description": "必填。要删除的文档 doc_id。"},
        "kb_name": {"type": "string", "description": "选填。目标 AstrBot 原生知识库名称，留空使用默认知识库。", "default": ""},
    },
    ["doc_id"],
)


class AstrKBWriterPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config or {}
        self.settings = NativeKBConfig.from_dict(config, default_kb_name=DEFAULT_KB_NAME)
        self.enable_write = bool(self.config.get("enable_write", True))
        self.enable_delete = bool(self.config.get("enable_delete", False))
        self.admin_only = bool(self.config.get("admin_only", True))
        self.bridge = AstrBotKBBridge(context, self.settings, PLUGIN_NAME)
        self._write_locks: dict[str, asyncio.Lock] = {}
        self.tools = [
            AstrKBTool(
                plugin=self,
                name="astrkb_list_kbs",
                description="列出 AstrBot 原生知识库。需要确认有哪些知识库时调用。",
                parameters=_schema(),
                operation="list_kbs",
            ),
            AstrKBTool(
                plugin=self,
                name="astrkb_list_documents",
                description="列出 AstrBot 原生知识库中的文档，用于更新或删除前确认 doc_id。",
                parameters=LIST_DOCUMENTS_SCHEMA,
                operation="list_documents",
            ),
            AstrKBTool(
                plugin=self,
                name="astrkb_write_document",
                description="向 AstrBot 原生知识库写入一篇新文档。必须提供 title 和 content。注意：内容会被检索回会话上下文，勿写入密钥/口令/个人敏感信息。",
                parameters=WRITE_DOCUMENT_SCHEMA,
                operation="write_document",
            ),
            AstrKBTool(
                plugin=self,
                name="astrkb_update_document",
                description="更新 AstrBot 原生知识库中的文档。必须提供 doc_id 和更新后的完整 content。注意：内容会被检索回会话上下文，勿写入密钥/口令/个人敏感信息。",
                parameters=UPDATE_DOCUMENT_SCHEMA,
                operation="update_document",
            ),
            AstrKBTool(
                plugin=self,
                name="astrkb_delete_document",
                description="删除 AstrBot 原生知识库中的文档。默认配置关闭删除能力。",
                parameters=DELETE_DOCUMENT_SCHEMA,
                operation="delete_document",
            ),
        ]
        self._tools_registered = False

    async def initialize(self):
        try:
            self._register_llm_tools()
        except Exception as exc:
            logger.warning(f"[{PLUGIN_NAME}] register LLM tools failed: {exc}", exc_info=True)
        try:
            kbs = await self.bridge.list_kbs()
            logger.info(f"[{PLUGIN_NAME}] loaded, native kbs={len(kbs)}")
        except Exception as exc:
            logger.warning(f"[{PLUGIN_NAME}] loaded but native KB manager is not ready: {exc}")

    async def terminate(self):
        self._remove_llm_tools()
        logger.info(f"[{PLUGIN_NAME}] terminated")

    def _llm_tools_removal(self):
        """返回 LLM 工具注册表的 remove_func，不可用时返回 None。"""
        llm_tools = getattr(getattr(self.context, "provider_manager", None), "llm_tools", None)
        remove_func = getattr(llm_tools, "remove_func", None)
        return remove_func if callable(remove_func) else None

    def _register_llm_tools(self) -> None:
        if self._tools_registered:
            return
        remove_func = self._llm_tools_removal()
        if remove_func:
            for tool in self.tools:
                try:
                    remove_func(tool.name)
                except Exception:
                    pass
        self.context.add_llm_tools(*self.tools)
        self._tools_registered = True
        logger.info(f"[{PLUGIN_NAME}] registered LLM tools with explicit parameter schemas")

    def _remove_llm_tools(self) -> None:
        remove_func = self._llm_tools_removal()
        if remove_func:
            for tool in self.tools:
                try:
                    remove_func(tool.name)
                except Exception as exc:
                    logger.debug(f"[{PLUGIN_NAME}] remove tool {tool.name} skipped: {exc}")
        self._tools_registered = False

    @filter.command_group("astrkb", alias={"原生知识库", "知识库写入"})
    async def astrkb(self, event: AstrMessageEvent):
        """AstrBot原生知识库写入：查看帮助。"""
        if not await self._allowed(event):
            yield event.plain_result(_PERMISSION_DENIED)
            return
        yield event.plain_result(self._help_text())

    @astrkb.command("help", alias={"h", "帮助"})
    async def astrkb_help(self, event: AstrMessageEvent):
        """帮助：显示 AstrBot 原生知识库写入插件说明。"""
        if not await self._allowed(event):
            yield event.plain_result(_PERMISSION_DENIED)
            return
        yield event.plain_result(self._help_text())

    @astrkb.command("status", alias={"stat", "状态"})
    async def astrkb_status(self, event: AstrMessageEvent):
        """状态：查看写入插件配置和原生知识库可用性。"""
        if not await self._allowed(event):
            yield event.plain_result(_PERMISSION_DENIED)
            return
        try:
            kbs = await self.bridge.list_kbs()
            manager_status = f"可用，当前知识库 {len(kbs)} 个"
        except Exception as exc:
            manager_status = f"不可用：{exc}"
        text = (
            "AstrBot 原生知识库写入插件状态\n"
            f"- 原生知识库管理器：{manager_status}\n"
            f"- 默认知识库：{self.settings.default_kb_name}\n"
            f"- 自动创建知识库：{'开启' if self.settings.allow_create_kb else '关闭'}\n"
            f"- 写入/更新：{'开启' if self.enable_write else '关闭'}\n"
            f"- 删除：{'开启' if self.enable_delete else '关闭'}\n"
            f"- 仅管理员：{'开启' if self.admin_only else '关闭'}\n"
            f"- 单篇上限：{self.settings.max_content_chars} 字符\n"
            f"- 分块参数：chunk_size={self.settings.chunk_size}, chunk_overlap={self.settings.chunk_overlap}"
        )
        yield event.plain_result(text)

    @astrkb.command("list", alias={"ls", "知识库"})
    async def astrkb_list(self, event: AstrMessageEvent):
        """列表：列出 AstrBot 原生知识库。"""
        yield event.plain_result(await self.astrkb_list_kbs(event))

    @astrkb.command("docs", alias={"documents", "文档"})
    async def astrkb_docs(self, event: AstrMessageEvent, kb_name: str = ""):
        """文档：列出默认或指定知识库的文档。"""
        yield event.plain_result(await self.astrkb_list_documents(event, kb_name=kb_name, limit=20))

    def _help_text(self) -> str:
        return (
            "AstrBot 原生知识库写入插件\n"
            "可见指令：\n"
            "- /astrkb status：查看状态\n"
            "- /astrkb list：列出原生知识库\n"
            "- /astrkb docs [知识库名]：列出文档\n"
            "LLM 工具：\n"
            "- astrkb_write_document：写入新文档\n"
            "- astrkb_update_document：更新文档\n"
            "- astrkb_delete_document：删除文档（默认关闭）"
        )

    def _lock_for(self, kb_name: str) -> asyncio.Lock:
        """按知识库名取写锁：同库写串行、跨库写并发。

        惰性创建：避免 Python 3.9 的 asyncio.Lock 在构造时绑定事件循环的问题；
        锁数量有界（= 实际出现过的知识库数），无泄漏风险。
        update 持本锁调用 bridge（其内部写新删旧不再取锁），无重入死锁。
        """
        key = kb_name or self.settings.default_kb_name
        lock = self._write_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._write_locks[key] = lock
        return lock

    async def astrkb_list_kbs(self, event: AstrMessageEvent) -> str:
        """列出 AstrBot 原生知识库。"""
        if not await self._allowed(event):
            return _PERMISSION_DENIED
        try:
            kbs = await self.bridge.list_kbs()
            if not kbs:
                return "当前没有 AstrBot 原生知识库。"
            lines = ["AstrBot 原生知识库："]
            for kb in kbs:
                lines.append(
                    f"- {kb.get('kb_name')} ({kb.get('kb_id')}): 文档 {kb.get('doc_count', 0)}，分块 {kb.get('chunk_count', 0)}"
                )
            return "\n".join(lines)
        except Exception as exc:
            return _failure("列出知识库", exc)

    async def astrkb_list_documents(self, event: AstrMessageEvent, kb_name: str = "", limit: int = 20) -> str:
        """列出指定 AstrBot 原生知识库里的文档，用于更新/删除前确认 doc_id。"""
        if not await self._allowed(event):
            return _PERMISSION_DENIED
        try:
            docs = await self.bridge.list_documents(kb_name=kb_name, limit=limit)
            if not docs:
                return "该知识库当前没有文档。"
            lines = ["知识库文档："]
            for doc in docs:
                lines.append(
                    f"- {doc.get('doc_name')} | doc_id={doc.get('doc_id')} | chunks={doc.get('chunk_count', 0)}"
                )
            return "\n".join(lines)
        except Exception as exc:
            return _failure("列出文档", exc)

    async def astrkb_write_document(self, event: AstrMessageEvent, title: str, content: str, kb_name: str = "") -> str:
        """向 AstrBot 原生知识库写入一篇新文档。kb_name 为空时写入默认知识库。"""
        if not await self._allowed(event):
            return _PERMISSION_DENIED
        if not self.enable_write:
            return "AstrBot 原生知识库写入功能已关闭。"
        try:
            async with self._lock_for(kb_name):
                result = await self.bridge.write_document(title=title, content=content, kb_name=kb_name)
            doc = result.get("doc", {})
            return (
                f"已写入 AstrBot 原生知识库：{result.get('kb_name', '')}\n"
                f"文档：{doc.get('doc_name')}\n"
                f"doc_id：{doc.get('doc_id')}\n"
                f"分块数：{doc.get('chunk_count', 0)}"
            )
        except Exception as exc:
            return _failure("写入 AstrBot 原生知识库", exc)

    async def astrkb_update_document(
        self,
        event: AstrMessageEvent,
        doc_id: str,
        content: str,
        title: str = "",
        kb_name: str = "",
    ) -> str:
        """更新 AstrBot 原生知识库中的文档。实现方式为删除旧文档并重新上传新内容。"""
        if not await self._allowed(event):
            return _PERMISSION_DENIED
        if not self.enable_write:
            return "AstrBot 原生知识库写入功能已关闭。"
        try:
            async with self._lock_for(kb_name):
                result = await self.bridge.update_document(doc_id=doc_id, content=content, title=title, kb_name=kb_name)
            doc = result.get("doc", {})
            text = (
                f"已更新 AstrBot 原生知识库：{result.get('kb_name', '')}\n"
                f"新文档：{doc.get('doc_name')}\n"
                f"新 doc_id：{doc.get('doc_id')}\n"
                f"旧 doc_id：{result.get('old_doc_id', '')}\n"
                f"分块数：{doc.get('chunk_count', 0)}"
            )
            if not result.get("old_doc_deleted", True):
                text += f"\n⚠ 旧文档 {result.get('old_doc_id', '')} 删除失败，库内暂存新旧两篇，请手动清理。"
            return text
        except Exception as exc:
            return _failure("更新 AstrBot 原生知识库", exc)

    async def astrkb_delete_document(self, event: AstrMessageEvent, doc_id: str, kb_name: str = "") -> str:
        """删除 AstrBot 原生知识库中的文档。默认配置关闭删除能力。"""
        if not await self._allowed(event):
            return _PERMISSION_DENIED
        if not self.enable_delete:
            return "AstrBot 原生知识库删除功能默认关闭。如确需删除，请在插件配置中开启 enable_delete。"
        try:
            async with self._lock_for(kb_name):
                result = await self.bridge.delete_document(doc_id=doc_id, kb_name=kb_name)
            doc = result.get("doc", {})
            return f"已删除 AstrBot 原生知识库文档：{doc.get('doc_name')} ({doc.get('doc_id')})"
        except Exception as exc:
            return _failure("删除 AstrBot 原生知识库文档", exc)

    async def _allowed(self, event: AstrMessageEvent) -> bool:
        if not self.admin_only:
            return True
        checker = getattr(event, "is_admin", None)
        if callable(checker):
            value = checker()
            if hasattr(value, "__await__"):
                value = await value
            return bool(value)
        return False
