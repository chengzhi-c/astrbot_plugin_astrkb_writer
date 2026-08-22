# Changelog

## 0.4.0

### 修复

- 缺库时 `list_documents` 改为抛出「知识库不存在」，不再返回空列表装成没有文档。
- 写锁按知识库名 `setdefault`，并对 `kb_name` 去空白，避免同库第一把锁重复创建、带空格的库名写成另一套锁。
- `update_document` 改为调用内部创建路径，不再经过带同名政策的 `write_document`。

### 变更

- 新增配置 `duplicate_policy`（`create` / `skip` / `update`），默认 `create`。
- 新增指令 `/astrkb dups`、`/astrkb dups-clean`（后者需要 `enable_delete`）。
- 写入已存在的知识库时使用该库的 `chunk_size` / `chunk_overlap`。
- 标题超过 120 字时回复中提示已截断。
- 配置项 `description` 改为短标题，详细说明放到 `hint`，避免控制台截断。

### 测试

- 覆盖同名政策、分页命中、命令层、缺库 list、写锁去空白。

## 0.3.0

### 修复

- **embedding provider 自动选择死代码**：`_resolve_embedding_provider_id` 原先读取 Provider 实例的 `provider_id` 属性，但真实 AstrBot（4.23.x 实测）所有 Provider 均无此属性，id 存于 `provider_config["id"]`。未配置 `default_embedding_provider_id` 时"自动创建知识库"必败；现改读真实来源，自动选择能力恢复。
- **异常消息分级**：bridge 层语义错误（ValueError，如内容过长/库不存在）保持透出；非 ValueError 内部错误不再把底层异常细节（可能含路径/向量库信息）落入用户回复，统一为"内部错误，详情见服务端日志"，细节保留在服务端日志。
- **Windows 保留文件名防护**：标题基名为 CON/PRN/AUX/NUL/COM1-9/LPT1-9 时自动加 `_` 前缀，避免写盘失败。

### 变更

- 配置解析单点化：`NativeKBConfig.from_dict()` 收敛全部默认值/钳制/bool 转换逻辑（含 `_clamp_int` 迁移至 bridge 数据层）；`default_kb_name` 现在会去除首尾空白，纯空白配置回退默认值。
- `_int_arg` 显式拦截 bool 参数（bool 是 int 子类，防止 `true/false` 被静默转成 1/0 截断 limit）。
- 写锁粒度细化：全局单锁 → per-KB 锁（同库写串行、跨库写并发），锁按知识库名惰性创建。
- LLM 工具 write/update 的 description 增加"内容会被检索回会话上下文，勿写入敏感信息"提示。
- 统一失败出口 `_failure()`：日志前缀与文案分级单点化，5 处重复 try/except 模板收敛。
- 重复文案常量化（知识库不存在/文档不存在模板）；`DEFAULT_KB_NAME` 常量为唯一默认值来源。
- 类型收敛：`AstrKBTool.plugin` 前向引用类型化、`_allowed` 参数标注 `AstrMessageEvent`。

### 测试

- 新增 16 个用例（41 → 57）：embedding provider 自动选择/显式配置优先/无 provider 抛错、create_kb 分支透传、Windows 保留名、`from_dict` 默认值与钳制、错误分级（内部错误隐藏/语义错误透出）、bool 拦截、per-KB 锁分配。全部在真实 AstrBot 4.23.3 环境通过。

## 0.2.1

### 变更

- 声明支持版本范围：`metadata.yaml` 新增 `astrbot_version: ">=4.6.0"`（PEP 440 specifier）。
- 兼容性实测（PyPI 源码 + 桌面版运行时逐版本加载冒烟）：下限 **4.6.0**（加载、工具注册、KB API 签名全通过）；4.5.8 不兼容（`upload_document` 无 `pre_chunked_text`）；4.0.0 不兼容（`FunctionTool` 无 `call` 方法）；3.x 不兼容（无 `FunctionTool` / `core.agent`）。
- README 新增「兼容性」章节。

## 0.2.0

### 变更

- 移除已弃用的 `@register` 装饰器（AstrBot v3.5.19+ 继承 `Star` 自动识别插件类，元数据由 `metadata.yaml` 唯一提供），消除 DeprecationWarning，版本/作者/描述收敛为单一来源。
- 删除 `data/` 目录（`cmd_config.json` 与 `t2i_templates/` 为无引用的残留资产），包体减少约 37KB。
- LLM 工具注册/注销的 provider_manager 探测链收敛为 `_llm_tools_removal()` 单点。
- bridge 返回字典消费改为防御式（`.get()`），消除跨层 KeyError 风险。
- `.gitignore` 补 `.pytest_cache/`。

### 测试

- 新增 `tests/test_conf_schema.py`：`_conf_schema.json` 默认值与代码兜底值一致性钉死，防双源漂移。
- 补齐 `_allowed` 权限逻辑、`bridge.delete_document`、返回结构契约、插件错误消息模板、`initialize()` 正常路径测试。测试总数 28 → 41。

## 0.1.1

### 修复

- `astrkb_update_document`：新文档写入成功后若旧文档删除失败，不再上抛导致整体报"更新失败"，改为返回部分成功结果（`old_doc_deleted=False`）并在回复中明确提示库内暂存新旧两篇、需手动清理。
- `initialize()` 中 LLM 工具注册失败不再连坐插件加载，降级为 warning，指令能力保持可用。

### 变更

- 知识库管理器获取收敛为 AstrBot 4.23.x 实测的 2 条真实路径（`context.kb_manager` → `core_lifecycle.kb_manager`），移除投机性 getter/私有属性探测。
- Embedding provider 自动选择收敛为 `provider_manager.embedding_provider_insts` 单一真实路径，移除 5 个猜测属性名。
- `limit` 范围钳制单点化：仅由 bridge 层负责，工具层只做类型转换。
- 日志降噪：选中 embedding provider 由 INFO 降为 DEBUG；工具注销异常由静默吞掉改为 DEBUG。
- 消除重复字面量：权限拒绝文案、默认知识库名提取为常量；bridge 日志前缀改为构造注入的插件名。

### 测试

- 新增 tests/（conftest + bridge 纯逻辑、update 失败语义、工具分发、插件加载冒烟），对真实 AstrBot 4.23.3 环境运行。

## 0.1.0

- 首个版本：5 个 LLM 工具 + `/astrkb` 指令组，支持写入/更新/删除 AstrBot 原生知识库文档。
