# 资料来源清单

每个 `batch_*.yaml` 是一批待抓取的公开资料，由 `scripts.ingest_sources` 自动抓取、改写成知识卡片入库。

字段说明：

| 字段 | 说明 |
| --- | --- |
| url | 资料链接（网页或 PDF） |
| title | 资料名称 |
| publisher | 发布机构 |
| source_type | gov 政府机构 / association 学会协会 / classic 古籍 / international 国际权威机构 / paper 开放获取论文 |
| license_note | 使用依据，如“政府公开科普”“公有领域古籍” |
| domain | 建议领域（模型会按内容再判断） |
| pool | 入哪个知识库：common 大健康通用库（默认），或产品线 id 如 sleep |

只收录公开资料：政府和学会的公开科普与指南、公有领域古籍、国际权威机构公开资料、开放获取论文。版权期内的书籍不收录。
