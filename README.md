# astrbot_plugin_astrkb_writer

轻量 AstrBot 原生知识库写入插件。它不依赖其他插件，只调用 AstrBot 自带 `KnowledgeBaseManager` / `KBHelper` API。

## 兼容性

- 支持 AstrBot `>= 4.6.0`（实测下限 4.6.0，加载冒烟通过；在 4.26.5 桌面版完整运行验证）。
- 4.5.8 及更早版本不兼容：`KBHelper.upload_document` 尚无 `pre_chunked_text` 参数（写入路径依赖它）。
- 不依赖 `@register` 装饰器（AstrBot 自动识别 `Star` 子类），插件元数据由 `metadata.yaml` 唯一提供。

## 能力

管理行为/指令组：

- `/astrkb help`：查看帮助
- `/astrkb status`：查看插件状态
- `/astrkb list`：列出 AstrBot 原生知识库
- `/astrkb docs [知识库名]`：列出知识库文档
- `/astrkb dups [知识库名]`：列出同名重复文档
- `/astrkb dups-clean [知识库名]`：每组同名只保留最新一篇（需开启 `enable_delete`）

提供给 Bot 的 LLM 工具：

- `astrkb_list_kbs`：列出 AstrBot 原生知识库
- `astrkb_list_documents`：列出知识库文档，获取 `doc_id`
- `astrkb_write_document`：写入新文档（同名是否合并见 `duplicate_policy`，默认 `create`）
- `astrkb_update_document`：按 `doc_id` 更新（先上传新文档，成功后删除旧文档，因此旧 `doc_id` 会失效；若旧文档删除失败会明确提示，不会静默掩盖）。显式更新不理 `duplicate_policy`。
- `astrkb_delete_document`：删除文档，默认关闭

## 配置

- `default_kb_name`：默认写入知识库，默认 `Bot自由知识库`
- `default_embedding_provider_id`：自动创建知识库时使用的 Embedding Provider ID；留空时尝试自动选择
- `allow_create_kb`：知识库不存在时是否自动创建，默认开启
- `enable_write`：是否允许写入/更新，默认开启
- `enable_delete`：是否允许删除，默认关闭
- `admin_only`：是否仅管理员可用，默认开启
- `max_content_chars`：单篇文档最大字符数，默认 20000（100–200000）
- `chunk_size` / `chunk_overlap`：新建知识库时的分块参数；写入已存在的库时优先用该库自己的分块设置
- `duplicate_policy`：写入时遇到同名文档（`标题` → 文件名相同）如何处理。默认 `create`（总是新建，兼容旧行为）。生产环境可改为 `skip`（跳过）或 `update`（覆盖最新一篇同名文档）。去重要生效必须改这项。

## 注意

AstrBot 原生知识库必须有可用的 Embedding Provider。若自动选择失败，请在插件配置里填写 `default_embedding_provider_id`（自动选择读取 Provider 的 `provider_config["id"]`，实测于 AstrBot 4.23.x）。

`chunk_overlap` 会被强制限制为小于 `chunk_size`，避免原生分块器失败。

`enable_delete=false` 只关闭显式删除和 `/astrkb dups-clean`。下面两类删除不受该开关影响：

| 动作 | 要不要 `enable_delete` |
|---|---|
| `duplicate_policy=update` 或 `astrkb_update_document` 删掉被替换的那一篇 | 不要 |
| `/astrkb dups-clean` 删同名旧篇 | 要 |
| `astrkb_delete_document` | 要 |

若新文档已写入但旧文档删除失败，工具会返回部分成功结果并提示手动清理。

同名匹配按文档名精确比较（`_make_file_name(title)` == `doc_name`），区分大小写，不做 Unicode 折叠。历史标题 `CON` 在修保留名后会写成 `_CON.txt`，与旧的 `CON.txt` 视为不同篇。

写锁只保证本进程内同库串行；控制台同时上传仍可能产生同名双份，用 `/astrkb dups-clean` 收。

**错误消息分级**：内容过长/库不存在等语义错误会直接透出；底层内部异常（可能含路径等细节）不落入用户回复，统一提示"内部错误，详情见服务端日志"，细节仅在服务端日志中。

Bot 写入知识库后，内容未来可能被检索回提示词中。不要把不可信来源的指令、密钥、系统路径或敏感信息写入知识库，以降低存储型提示注入风险（LLM 工具的 write/update 描述中也已内置该提示）。
