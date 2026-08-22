from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

# 必须在任何 astrbot import 之前：否则 AstrBot 会把 cmd_config.json 写进插件目录。
_TEST_ROOT = Path(tempfile.mkdtemp(prefix="astrkb-test-root-"))
os.environ["ASTRBOT_ROOT"] = str(_TEST_ROOT)

PKG_NAME = "astrbot_plugin_astrkb_writer"

_ROOT = Path(__file__).resolve().parents[1]

# Locate the actual package directory (works even when the folder name is
# not the package name, e.g. "<name>-main").
_PKG_DIR = _ROOT
if not (_ROOT / "main.py").exists():
    for child in _ROOT.parent.iterdir():
        if (
            child.is_dir()
            and child.name.startswith(PKG_NAME)
            and (child / "main.py").exists()
        ):
            _PKG_DIR = child
            break

if str(_PKG_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR.parent))

# If the folder name differs from the package name, register an alias so
# relative imports inside the package resolve correctly.
if _PKG_DIR.name != PKG_NAME and PKG_NAME not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        PKG_NAME,
        _PKG_DIR / "__init__.py",
        submodule_search_locations=[str(_PKG_DIR)],
    )
    if spec is not None and spec.loader is not None:
        module = importlib.util.module_from_spec(spec)
        sys.modules[PKG_NAME] = module
        spec.loader.exec_module(module)
