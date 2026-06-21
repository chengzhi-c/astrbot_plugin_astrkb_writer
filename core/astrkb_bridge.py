from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from astrbot.core import logger

try:
    from astrbot.core.provider.provider import EmbeddingProvider
except Exception:  # pragma: no cover
    EmbeddingProvider = None  # type: ignore


@dataclass
class NativeKBConfig:
    default_kb_name: str = "Bot自由知识库"
    default_embedding_provider_id: str = ""
    allow_create_kb: bool = True
    max_content_chars: int = 20000
    chunk_size: int = 512
    chunk_overlap: int = 50


class AstrBotKBBridge:
    """Thin wrapper around AstrBot native KnowledgeBaseManager/KBHelper APIs."""

    def __init__(self, context: Any, config: NativeKBConfig) -> None:
        self.context = context
        self.config = config

    def get_kb_manager(self) -> Any:
        manager = self._from_context("kb_manager")
        if manager:
            return manager

        lifecycle = self._from_context("core_lifecycle") or self._from_context("_core_lifecycle")
        if lifecycle:
            manager = getattr(lifecycle, "kb_manager", None)
            if manager:
                return manager

        raise RuntimeError("无法获取 AstrBot 原生知识库管理器，请确认 AstrBot 知识库模块已启用。")

    async def list_kbs(self) -> list[dict[str, Any]]:
        manager = self.get_kb_manager()
        kbs = await manager.list_kbs()
        return [self._kb_to_dict(kb) for kb in kbs]

    async def list_documents(self, kb_name: str = "", limit: int = 20) -> list[dict[str, Any]]:
        helper = await self.get_existing_kb(kb_name or self.config.default_kb_name)
        if not helper:
            return []
        docs = await helper.list_documents(offset=0, limit=max(1, min(limit, 100)))
        return [self._doc_to_dict(doc) for doc in docs]

    async def write_document(self, title: str, content: str, kb_name: str = "") -> dict[str, Any]:
        title = self._normalize_title(title)
        content = self._normalize_content(content)
        if len(content) > self.config.max_content_chars:
            raise ValueError(f"内容过长：{len(content)} > {self.config.max_content_chars}")

        helper = await self.get_or_create_kb(kb_name or self.config.default_kb_name)

        chunks = await helper.chunker.chunk(
            content,
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
        )
        chunks = [chunk.strip() for chunk in chunks if chunk and chunk.strip()]
        if not chunks:
            raise ValueError("内容切分后为空，无法写入知识库。")

        file_name = self._make_file_name(title)
        doc = await helper.upload_document(
            file_name=file_name,
            file_content=None,
            file_type="txt",
            pre_chunked_text=chunks,
        )
        return {
            "action": "created",
            "kb_id": helper.kb.kb_id,
            "kb_name": helper.kb.kb_name,
            "doc": self._doc_to_dict(doc),
        }

    async def update_document(self, doc_id: str, content: str, title: str = "", kb_name: str = "") -> dict[str, Any]:
        helper = await self.get_existing_kb(kb_name or self.config.default_kb_name)
        if not helper:
            raise ValueError(f"知识库不存在：{kb_name or self.config.default_kb_name}")
        old_doc = await helper.get_document(doc_id)
        if not old_doc:
            raise ValueError(f"文档不存在：{doc_id}")
        new_title = title.strip() or old_doc.doc_name.rsplit(".", 1)[0]
        result = await self.write_document(new_title, content, helper.kb.kb_name)
        await helper.delete_document(doc_id)
        result["action"] = "updated"
        result["old_doc_id"] = doc_id
        return result

    async def delete_document(self, doc_id: str, kb_name: str = "") -> dict[str, Any]:
        helper = await self.get_existing_kb(kb_name or self.config.default_kb_name)
        if not helper:
            raise ValueError(f"知识库不存在：{kb_name or self.config.default_kb_name}")
        doc = await helper.get_document(doc_id)
        if not doc:
            raise ValueError(f"文档不存在：{doc_id}")
        await helper.delete_document(doc_id)
        return {
            "action": "deleted",
            "kb_id": helper.kb.kb_id,
            "kb_name": helper.kb.kb_name,
            "doc": self._doc_to_dict(doc),
        }

    async def get_existing_kb(self, kb_name: str) -> Any | None:
        manager = self.get_kb_manager()
        return await manager.get_kb_by_name(kb_name)

    async def get_or_create_kb(self, kb_name: str) -> Any:
        manager = self.get_kb_manager()
        helper = await manager.get_kb_by_name(kb_name)
        if helper:
            return helper

        if not self.config.allow_create_kb:
            raise ValueError(f"知识库不存在且未允许自动创建：{kb_name}")

        embedding_provider_id = await self._resolve_embedding_provider_id(manager)
        helper = await manager.create_kb(
            kb_name=kb_name,
            description="由 astrbot_plugin_astrkb_writer 自动创建，供 Bot 自主写入。",
            emoji="📝",
            embedding_provider_id=embedding_provider_id,
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
        )
        logger.info(f"[astrbot_plugin_astrkb_writer] created native KB: {kb_name}")
        return helper

    async def _resolve_embedding_provider_id(self, manager: Any) -> str:
        configured = self.config.default_embedding_provider_id.strip()
        if configured:
            return configured

        provider_manager = getattr(manager, "provider_manager", None)
        if not provider_manager:
            raise ValueError("无法自动选择 embedding provider：provider_manager 不可用，请在插件配置中填写 default_embedding_provider_id。")

        candidates = []
        inst_map = getattr(provider_manager, "inst_map", None)
        if isinstance(inst_map, dict):
            candidates.extend([(provider_id, provider) for provider_id, provider in inst_map.items()])

        for attr in ("embedding_provider_insts", "provider_insts", "providers", "provider_map", "curr_provider_inst_map"):
            value = getattr(provider_manager, attr, None)
            if isinstance(value, dict):
                candidates.extend(value.items())
            elif isinstance(value, list):
                candidates.extend([(None, provider) for provider in value])

        for maybe_id, provider in candidates:
            if EmbeddingProvider is not None and not isinstance(provider, EmbeddingProvider):
                continue
            provider_id = maybe_id or getattr(provider, "provider_id", None) or getattr(provider, "id", None)
            if provider_id:
                logger.info(f"[astrbot_plugin_astrkb_writer] selected embedding provider: {provider_id}")
                return str(provider_id)

        raise ValueError("未找到可用 embedding provider，请在插件配置中填写 default_embedding_provider_id。")

    def _from_context(self, name: str) -> Any:
        value = getattr(self.context, name, None)
        if value:
            return value
        getter = getattr(self.context, f"get_{name}", None)
        if callable(getter):
            try:
                return getter()
            except Exception:
                return None
        return None

    @staticmethod
    def _normalize_title(title: str) -> str:
        title = (title or "").strip()
        if not title:
            raise ValueError("标题不能为空")
        return title[:120]

    @staticmethod
    def _normalize_content(content: str) -> str:
        content = (content or "").strip()
        if not content:
            raise ValueError("内容不能为空")
        return content

    @staticmethod
    def _make_file_name(title: str) -> str:
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip().strip(".")
        if not name:
            name = "bot-note"
        return f"{name[:100]}.txt"

    @staticmethod
    def _kb_to_dict(kb: Any) -> dict[str, Any]:
        if hasattr(kb, "model_dump"):
            return kb.model_dump()
        return {
            "kb_id": getattr(kb, "kb_id", ""),
            "kb_name": getattr(kb, "kb_name", ""),
            "description": getattr(kb, "description", ""),
            "doc_count": getattr(kb, "doc_count", 0),
            "chunk_count": getattr(kb, "chunk_count", 0),
        }

    @staticmethod
    def _doc_to_dict(doc: Any) -> dict[str, Any]:
        if hasattr(doc, "model_dump"):
            return doc.model_dump()
        return {
            "doc_id": getattr(doc, "doc_id", ""),
            "doc_name": getattr(doc, "doc_name", ""),
            "file_type": getattr(doc, "file_type", ""),
            "file_size": getattr(doc, "file_size", 0),
            "chunk_count": getattr(doc, "chunk_count", 0),
        }
