"""配置一致性测试：_conf_schema.json 默认值/键集合必须与代码侧一致，防止双源漂移。

改动任一侧（schema 默认值、代码兜底值、插件消费的配置键）而未同步另一侧时红灯。
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

pytest.importorskip("astrbot")

from astrbot_plugin_astrkb_writer.core.astrkb_bridge import (  # noqa: E402
    DEFAULT_KB_NAME,
    NativeKBConfig,
)
from astrbot_plugin_astrkb_writer.main import AstrKBWriterPlugin  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]

# main.py 消费但不属于 NativeKBConfig 的开关
_PLUGIN_SWITCHES = {"enable_write", "enable_delete", "admin_only"}


def _load_schema() -> dict:
    with open(_ROOT / "_conf_schema.json", encoding="utf-8") as f:
        return json.load(f)


def test_schema_defaults_match_code() -> None:
    schema = _load_schema()
    assert schema["default_kb_name"]["default"] == DEFAULT_KB_NAME
    assert schema["default_embedding_provider_id"]["default"] == ""
    assert schema["max_content_chars"]["default"] == 20000
    assert schema["chunk_size"]["default"] == 512
    assert schema["chunk_overlap"]["default"] == 50
    assert schema["enable_write"]["default"] is True
    assert schema["enable_delete"]["default"] is False
    assert schema["allow_create_kb"]["default"] is True
    assert schema["admin_only"]["default"] is True


def test_schema_keys_cover_all_plugin_config_keys() -> None:
    schema = _load_schema()
    config_keys = {f.name for f in dataclasses.fields(NativeKBConfig)}
    expected = config_keys | _PLUGIN_SWITCHES
    assert set(schema) == expected


def test_config_from_dict_defaults() -> None:
    cfg = NativeKBConfig.from_dict(None)
    assert cfg.default_kb_name == DEFAULT_KB_NAME
    assert cfg.default_embedding_provider_id == ""
    assert cfg.allow_create_kb is True
    assert cfg.max_content_chars == 20000
    assert cfg.chunk_size == 512
    assert cfg.chunk_overlap == 50


def test_config_from_dict_clamps_and_coerces() -> None:
    cfg = NativeKBConfig.from_dict(
        {
            "chunk_size": 99999,
            "chunk_overlap": 9999,
            "max_content_chars": "abc",
            "allow_create_kb": False,
            "default_kb_name": "   ",
        },
    )
    assert cfg.chunk_size == 8000
    assert cfg.chunk_overlap == 2000  # 超上界钳制到 2000，且 < chunk_size-1
    assert cfg.max_content_chars == 20000
    assert cfg.allow_create_kb is False
    assert cfg.default_kb_name == DEFAULT_KB_NAME  # 空白名回退默认
