# 部署指南（让别人也能用）

本项目现已容器化：`docker compose up -d --build` 一条命令起【应用 + MySQL + Redis】全栈。

---

## 一、本地一键起全栈（先在自己机器验证）

前置：装好 Docker Desktop。

```bash
cp .env.example .env          # 复制模板
# 编辑 .env：至少填 OPENAI_API_KEY；要联网搜索再填 WEB_SEARCH_API_KEY(Tavily)
docker compose up -d --build  # 构建应用镜像并启动全栈
docker compose ps             # mysql / redis 为 healthy、app 为 running 即成功
docker compose logs -f app    # 看应用启动日志
curl http://localhost:8000/health   # 确认 llm_mode=live、redis=true
```

打开 http://localhost:8000 使用，http://localhost:8000/docs 查看接口文档。
停止：`docker compose down`（保留数据）；清空重来：`docker compose down -v`。

> 说明：容器内 app 用服务名连库（mysql/redis），已在 compose 里覆盖好，
> 不用改 `.env` 里的 localhost 值——那是你不走 Docker、直接 `uvicorn` 时用的。

---

## 二、放到云服务器长期跑

1. 买一台云主机（阿里云/腾讯云 ECS，2 核 4G 起步；镜像选 Ubuntu 22.04）。
2. 装 Docker + Docker Compose：
   ```bash
   curl -fsSL https://get.docker.com | sh
   ```
3. 把项目传上去（`git clone` 你的仓库，或 scp 上传），进入目录：
   ```bash
   cp .env.example .env      # 填好 key；⚠️ 把 JWT_SECRET 换成强随机、AUTH_ENABLED=true
   docker compose up -d --build
   ```
4. 安全组/防火墙放行 80、443（**不要**直接对公网暴露 8000，走 Nginx 反代）。

---

## 三、Nginx + HTTPS + 域名

域名解析到服务器 IP 后，用 Nginx 反代到 app 的 8000，并用 Let's Encrypt 上 HTTPS。
最简单：用 `nginx-proxy + acme-companion`，或手动配：

```nginx
server {
    listen 80;
    server_name your-domain.com;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # SSE 流式：关闭缓冲，拉长超时
        proxy_buffering off;
        proxy_read_timeout 300s;
    }
}
```

证书：`certbot --nginx -d your-domain.com` 自动签发 + 续期。

> ⚠️ SSE（聊天流式）务必 `proxy_buffering off`，否则回答不会一段段吐出来。

---

## 四、给别人用之前的上线清单（按此推进）

- [x] **容器化**：Dockerfile + 全栈 docker-compose（本文件）
- [x] **鉴权/多用户**：用户表 + 注册/登录 + bcrypt 密码哈希 + JWT；上线时设 `AUTH_ENABLED=true`
- [x] **密钥模板**：`.env.example`；`.env` 已 gitignore，切勿提交真实 key
- [ ] **JWT_SECRET** 换强随机；数据库密码别再用默认 `pyfin`
- [ ] **HTTPS + 域名 + （国内）ICP 备案**：面向公网的网站国内必须备案
- [ ] **成本控制**：每次问答都花 LLM/搜索的钱，公开后要按用户限流/配额，避免账单失控
- [ ] **可观测性/备份**：日志、监控、数据库定期备份
- [ ] **合规（金融，最关键）**：牌照资质、隐私政策/用户协议、风险揭示、数据安全（等保）——
      建议找合规/法务过一遍

> 数据持久化：MySQL/Redis 的数据都在 docker 卷里（mysqldata/redisdata），
> `down` 不会丢，`down -v` 才会清。上线务必做定期备份。
