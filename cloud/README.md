# Kairos 云上长程 Agent（C-S1 骨架）

Cloudflare Workers + Durable Objects 承载工单制领域模型（详见 `docs/Kairos云上长程Agent部署设计.md`）。

## 已实现

- **Agent DO**：内置 SQLite 三表（triggers / mandates / tickets），工单状态机与本机同构
- **人直接开单** `POST /tickets`（explicit 授权，Mandate 落库）
- **Source 抽象第一档**：Cron 巡检（`scheduled` → `/patrol`）+ webhook 收 Trigger（`POST /triggers`，observe 档只记录不自动开单）
- **Watchdog**：DO Alarm 唤醒排队工单
- **极简面板** `GET /`：开单 + 工单列表（5 秒自刷）

## 部署（需要你的 Cloudflare 账号，5 分钟）

```bash
cd cloud/worker
npm install
npx wrangler login          # 浏览器授权一次
npx wrangler secret put DEEPSEEK_API_KEY   # 粘 DeepSeek key（评估用，可选）
npx wrangler deploy         # 发布，输出形如 https://kairos-agent.<你的子域>.workers.dev
```

部署完打开输出的 URL 就是云端面板。国内直连 workers.dev 不稳的话，绑定自定义域（dashboard → Workers → 域名）。

## 未实现（后续里程碑）

- C-S2：真实盯页 Source（fetch 目标官网 diff）+ LLM assessment（DeepSeek API 已留 env）
- C-S3：本机 Kairos sidecar 作为 Executor 经出站 WebSocket 中继接单
- C-S4：桌面端右栏「动态」直连云端 DO

## 与本机的关系

云端 DO 的工单与本机 `kairos.db` 的 tickets **目前是两套独立存储**（C-S3 中继后合流）。先部署体验云端骨架，不影响本地使用。
