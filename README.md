# 大健康 AIGC 平台 · 底座

调用已备案的第三方大模型（通义千问为主，DeepSeek 备用），结合自建健康知识库，按「产品线 × 渠道 × 受众 × 内容类型」生成营销文字内容，经合规检测和人工审核后导出。

## 目录结构

```
app/                 底座代码（所有产品线共用，不写死任何产品）
  config.py            配置（读取 .env）
  llm.py               模型调用层：千问 / DeepSeek 自动切换，百炼向量化
  knowledge.py         知识库：卡片入库、向量检索
  generation.py        生成引擎：检索 → 提示词 → 生成 → 合规检测 → 自动改写
  compliance.py        合规检测：禁用词、医疗用语、照搬原文
  auth.py              内部账号与权限（运营 / 审核 / 管理员）
  main.py              接口
channels/            渠道规范（小红书、点评团购、抖音口播、线下物料、朋友圈），所有产品线共用
compliance/rules.yaml 通用合规词表
products/<产品线>/    每条产品线一套：product.yaml 配置、knowledge/ 知识卡片、examples.yaml 风格样例
scripts/             创建账号、导入知识库
tests/               自动化测试
miniprogram/         内部小程序（用微信开发者工具打开，说明见 miniprogram/README.md）
```

## 新增一条产品线

1. 复制 `products/sleep/` 为 `products/<新产品线>/`，改 `product.yaml`（名称、受众、内容类型、启用的渠道、专属合规词）
2. 在 `knowledge/` 放入该产品线的知识卡片，在 `examples.yaml` 放风格样例
3. 新开一套部署，`.env` 里写 `PRODUCT=<新产品线>`

底座代码不用改。

## 角色与流程

| 角色 | 能做什么 |
| --- | --- |
| operator 运营 | 生成内容、修改自己的稿件、导出已通过的稿件 |
| reviewer 审核 | 以上全部，加上审核通过/打回、查看知识库 |
| admin 管理员 | 以上全部，加上维护知识库、创建账号 |

生成 → 自动合规检测（命中禁用词或照搬原文时自动改写一次）→ 人工修改（会重新检测）→ 审核通过 → 导出（自动附 AI 生成标识）。仍含禁用表述的稿件不能被审核通过。

## 主要接口

所有接口都需要请求头 `Authorization: Bearer <令牌>`。启动后访问 `/docs` 可以看到完整的在线接口文档。

| 接口 | 说明 |
| --- | --- |
| `GET /api/meta` | 当前产品线的渠道、受众、内容类型（小程序表单用） |
| `POST /api/generate` | 生成：`{channel, audience, content_type, brief}` |
| `GET /api/generations` | 稿件列表，可按 `status` 筛选 |
| `PATCH /api/generations/{id}` | 修改稿件（自动重新检测） |
| `POST /api/generations/{id}/review` | 审核：`{action: approve/reject, note}` |
| `GET /api/generations/{id}/export` | 导出已通过的稿件 |
| `GET/POST/DELETE /api/knowledge` | 知识库查询、新增、下架 |
| `POST /api/users` | 创建账号 |

## 本地测试

```bash
pip install -r requirements.txt pytest httpx
python -m pytest -q tests
```

部署见 [DEPLOY.md](DEPLOY.md)。
