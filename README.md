# astrbot_plugin_astrkb_writer

轻量 AstrBot 原生知识库写入插件。它不依赖其他插件，只调用 AstrBot 自带 `KnowledgeBaseManager` / `KBHelper` API。

## 能力

管理行为/指令组：

- `/astrkb help`：查看帮助
- `/astrkb status`：查看插件状态
- `/astrkb list`：列出 AstrBot 原生知识库
- `/astrkb docs [知识库名]`：列出知识库文档

提供给 Bot 的 LLM 工具：

- `astrkb_list_kbs`：列出 AstrBot 原生知识库
- `astrkb_list_documents`：列出知识库文档，获取 `doc_id`
- `astrkb_write_document`：写入新文档
- `astrkb_update_document`：更新文档（先上传新文档，成功后删除旧文档，因此旧 `doc_id` 会失效）
- `astrkb_delete_document`：删除文档，默认关闭

## 配置

- `default_kb_name`：默认写入知识库，默认 `Bot自由知识库`
- `default_embedding_provider_id`：自动创建知识库时使用的 Embedding Provider ID；留空时尝试自动选择
- `allow_create_kb`：知识库不存在时是否自动创建，默认开启
- `enable_write`：是否允许写入/更新，默认开启
- `enable_delete`：是否允许删除，默认关闭
- `admin_only`：是否仅管理员可用，默认开启
- `max_content_chars`：单篇文档最大字符数，默认 20000
- `chunk_size` / `chunk_overlap`：写入原生知识库时的分块参数

## 注意

AstrBot 原生知识库必须有可用的 Embedding Provider。若自动选择失败，请在插件配置里填写 `default_embedding_provider_id`。

`chunk_overlap` 会被强制限制为小于 `chunk_size`，避免原生分块器失败。

`enable_delete=false` 只关闭显式删除工具；更新文档仍然会在新文档写入成功后删除旧文档，这是更新语义的一部分。

Bot 写入知识库后，内容未来可能被检索回提示词中。不要把不可信来源的指令、密钥、系统路径或敏感信息写入知识库，以降低存储型提示注入风险。
