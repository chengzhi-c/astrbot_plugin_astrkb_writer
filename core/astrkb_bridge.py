from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from astrbot.core import logger

DEFAULT_KB_NAME = "Bot自由知识库"

_ERR_KB_NOT_FOUND = "知识库不存在：{}"
_ERR_DOC_NOT_FOUND = "文档不存在：{}"


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """把输入钳制到 [minimum, maximum]；无法解析时返回 default。"""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


@dataclass
class NativeKBConfig:
    default_kb_name: str = ""
    default_embedding_provider_id: str = ""
    allow_create_kb: bool = True
    max_content_chars: int = 20000
    chunk_size: int = 512
    chunk_overlap: int = 50

    @classmethod
    def from_dict(cls, config: dict | None, default_kb_name: str = DEFAULT_KB_NAME) -> "NativeKBConfig":
        """从插件配置 dict 解析出配置；缺省/非法值回退默认，范围由本层单点钳制。"""
        cfg = config or {}
        chunk_size = _clamp_int(cfg.get("chunk_size", 512), 512, 100, 8000)
        chunk_overlap = min(_clamp_int(cfg.get("chunk_overlap", 50), 50, 0, 2000), chunk_size - 1)
        return cls(
            default_kb_name=str(cfg.get("default_kb_name", default_kb_name) or default_kb_name).strip() or default_kb_name,
            default_embedding_provider_id=str(cfg.get("default_embedding_provider_id", "") or ""),
            allow_create_kb=bool(cfg.get("allow_create_kb", True)),
            max_content_chars=_clamp_int(cfg.get("max_content_chars", 20000), 20000, 100, 200000),
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )


class AstrBotKBBridge:
    """Thin wrapper around AstrBot native KnowledgeBaseManager/KBHelper APIs."""

    def __init__(self, context: Any, config: NativeKBConfig, plugin_name: str) -> None:
        self.context = context
        self.config = config
        self.plugin_name = plugin_name

    def get_kb_manager(self) -> Any:
        # AstrBot 4.23.x 实测路径：Context.kb_manager 由 core_lifecycle 注入；
        # core_lifecycle.kb_manager 是 AstrBot 自身 dashboard 在用的同一路径。
        manager = getattr(self.context, "kb_manager", None)
        if manager:
            return manager
        lifecycle = getattr(self.context, "core_lifecycle", None)
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
            raise ValueError(_ERR_KB_NOT_FOUND.format(kb_name or self.config.default_kb_name))
        old_doc = await helper.get_document(doc_id)
        if not old_doc:
            raise ValueError(_ERR_DOC_NOT_FOUND.format(doc_id))
        new_title = title.strip() or old_doc.doc_name.rsplit(".", 1)[0]
        result = await self.write_document(new_title, content, helper.kb.kb_name)
        old_doc_deleted = True
        try:
            await helper.delete_document(doc_id)
        except Exception as exc:
            # 新文档已写入成功，整体上视为部分成功；旧文档残留须明确告知调用方。
            old_doc_deleted = False
            logger.warning(
                f"[{self.plugin_name}] 新文档已写入但旧文档删除失败 (doc_id={doc_id}): {exc}",
                exc_info=True,
            )
        result["action"] = "updated"
        result["old_doc_id"] = doc_id
        result["old_doc_deleted"] = old_doc_deleted
        return result

    async def delete_document(self, doc_id: str, kb_name: str = "") -> dict[str, Any]:
        helper = await self.get_existing_kb(kb_name or self.config.default_kb_name)
        if not helper:
            raise ValueError(_ERR_KB_NOT_FOUND.format(kb_name or self.config.default_kb_name))
        doc = await helper.get_document(doc_id)
        if not doc:
            raise ValueError(_ERR_DOC_NOT_FOUND.format(doc_id))
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
            description=f"由 {self.plugin_name} 自动创建，供 Bot 自主写入。",
            emoji="📝",
            embedding_provider_id=embedding_provider_id,
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
        )
        logger.info(f"[{self.plugin_name}] created native KB: {kb_name}")
        return helper

    async def _resolve_embedding_provider_id(self, manager: Any) -> str:
        configured = self.config.default_embedding_provider_id.strip()
        if configured:
            return configured

        provider_manager = getattr(manager, "provider_manager", None)
        if not provider_manager:
            raise ValueError("无法自动选择 embedding provider：provider_manager 不可用，请在插件配置中填写 default_embedding_provider_id。")

        # AstrBot 4.23.x 实测：Provider 实例无 provider_id 属性，id 存于 provider_config["id"]
        # （core/provider/provider.py:50、sources/openai_embedding_source.py:22、
        #  inst_map 以 config["id"] 为 key，manager.py:707）。
        # 必须用 `get("id") or ""` 而非 `get("id", "")`：id 键存在但值为 None 时，
        # str(None) 会得到 "None" 并被误判为有效 provider id。
        for provider in getattr(provider_manager, "embedding_provider_insts", None) or []:
            provider_id = str((getattr(provider, "provider_config", None) or {}).get("id") or "").strip()
            if provider_id:
                logger.debug(f"[{self.plugin_name}] selected embedding provider: {provider_id}")
                return provider_id

        raise ValueError("未找到可用 embedding provider，请在插件配置中填写 default_embedding_provider_id。")

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

    _WIN_RESERVED = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }

    @staticmethod
    def _make_file_name(title: str) -> str:
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip().strip(".")
        if not name:
            name = "bot-note"
        name = name[:100]
        # Windows 保留名（CON/PRN/...）直接写盘会被文件系统拒绝，标题整体加前缀规避；
        # 标题原样保留（含已有扩展名），仅追加 .txt 后缀。
        base = name.partition(".")[0]
        if base.upper() in AstrBotKBBridge._WIN_RESERVED:
            name = "_" + name
        return f"{name}.txt"

    @staticmethod
    def _kb_to_dict(kb: Any) -> dict[str, Any]:
        # 真实 AstrBot 模型（BaseKBModel(SQLModel)）恒有 model_dump()；
        # fallback 仅服务于替换实现/旧版本模型，跨版本韧性所需，勿删。
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
        # 同 _kb_to_dict：model_dump 为真实主路径，fallback 保留以兼容替换实现。
        if hasattr(doc, "model_dump"):
            return doc.model_dump()
        return {
            "doc_id": getattr(doc, "doc_id", ""),
            "doc_name": getattr(doc, "doc_name", ""),
            "file_type": getattr(doc, "file_type", ""),
            "file_size": getattr(doc, "file_size", 0),
            "chunk_count": getattr(doc, "chunk_count", 0),
        }
