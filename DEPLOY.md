# 部署说明（可直接交给 WorkBuddy 执行）

目标：用新底座替换服务器上旧的中医药平台 `/opt/tcm-aigc`。全程用 Docker，不影响服务器上的其他项目。

> 当前服务器只有 Docker Compose v1（`docker-compose`，带连字符）。下文的 `docker compose` 在这台服务器上都写成 `docker-compose`。
> v1（1.29.2）配新版 Docker 有个已知问题：**重建**已有容器时报 `KeyError: 'ContainerConfig'`。所以更新时先删掉 api 容器再新建（见文末「更新代码」），不要直接 `up -d --build`。长期可以考虑安装 Docker Compose v2（`docker-compose-plugin`）。
> `.env` 里必须有 `COMPOSE_PROJECT_NAME=health-aigc`，否则在 `/opt/tcm-aigc` 目录下项目名会变成 `tcm-aigc`，和旧项目的数据卷撞名。

## 第 1 步：盘点并备份旧项目（替换前必须做）

1. 查看旧项目是怎么运行的，记下它占用的端口：
   ```bash
   cd /opt/tcm-aigc && ls -la
   docker ps -a | grep -i tcm ; systemctl list-units --type=service | grep -i tcm ; pm2 list 2>/dev/null
   ```
2. 如果旧项目有数据库或已录入的数据，先导出备份（按实际情况处理），并把情况告诉用户。
3. 整个目录打包备份：
   ```bash
   sudo tar czf /opt/tcm-aigc.backup-$(date +%Y%m%d).tar.gz -C /opt tcm-aigc
   ```
4. 停掉旧项目的服务（`docker compose down` / `systemctl stop ...` / `pm2 stop ...`，按第 1 步查到的方式），然后把旧目录改名保留：
   ```bash
   sudo mv /opt/tcm-aigc /opt/tcm-aigc.old
   ```

## 第 2 步：拉取新代码

```bash
sudo git clone <仓库地址> /opt/tcm-aigc
cd /opt/tcm-aigc
```

## 第 3 步：填写配置

```bash
cp .env.example .env
```

编辑 `.env`：
- `POSTGRES_PASSWORD`：设一个强密码，并把 `DATABASE_URL` 里的 `change-me` 改成同一个密码
- `DASHSCOPE_API_KEY`、`DEEPSEEK_API_KEY`：填入用户已有的 Key（如果旧项目的配置里有，可以从那里取，不要发到聊天里）
- `API_PORT`：可沿用旧项目的端口，或选一个没被占用的端口
- 模型名（`DASHSCOPE_CHAT_MODEL`、`EMBEDDING_MODEL`）：到百炼控制台确认当前可用的型号

## 第 4 步：启动

```bash
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:${API_PORT:-8100}/health
```

看到 `{"ok":true,"product":"sleep"}` 即启动成功。

## 第 5 步：初始化

```bash
# 创建管理员账号（令牌只显示一次，交给用户保存）
docker compose exec api python -m scripts.create_user 管理员 admin

# 导入起步知识卡片（会调用百炼向量化，顺便验证 Key 是否可用）
docker compose exec api python -m scripts.import_knowledge products/sleep/knowledge/starter_cards.jsonl
```

## 第 6 步：试生成一条

```bash
curl -X POST http://127.0.0.1:${API_PORT:-8100}/api/generate \
  -H "Authorization: Bearer <管理员令牌>" -H "Content-Type: application/json" \
  -d '{"channel":"xiaohongshu","audience":"c_local","content_type":"seeding","brief":"下班后肩颈放松"}'
```

返回里有 `output`（生成的文案）和 `compliance`（检测结果）即全部跑通。

## 确认无误后

新底座跑通并使用一段时间后，再删除 `/opt/tcm-aigc.old` 和备份包（删除前先问用户）。

## 日常操作

| 操作 | 命令 |
| --- | --- |
| 更新代码（Compose v1） | `git pull && docker-compose -p health-aigc build api && docker-compose -p health-aigc rm -sf api && docker-compose -p health-aigc up -d --no-deps api` |
| 更新代码（Compose v2） | `git pull && docker compose up -d --build` |
| 查看日志 | `docker compose logs -f api` |
| 重启 | `docker compose restart api` |
| 备份数据库 | `docker compose exec -T db pg_dump -U aigc aigc > backup-$(date +%Y%m%d).sql` |
| 自动抓取一批公开资料入库 | `docker compose exec -T api python -m scripts.ingest_sources sources/batch_01.yaml` |
| 重试失败的资料 | 上面的命令加 `--retry-failed` |
| 导出资料来源登记表（备案用） | `docker compose exec -T api python -m scripts.export_sources > sources-$(date +%Y%m%d).csv` |
| 查看账号 | `docker compose exec -T api python -m scripts.deactivate_user --list` |
| 停用账号（令牌泄露或人员离开） | `docker compose exec -T api python -m scripts.deactivate_user <账号ID>` |

小程序正式上线前，需要给接口配置 HTTPS 域名（微信要求），域名和 ICP 备案由合作方负责。
