# WeHouse 云端服务

部署在 Railway（Hobby Plan）上的房源管理云端，由两部分组成：

- **FastAPI 后端**：接收本地推送、做 CRUD、连 Supabase PostgreSQL
- **Streamlit 前端**：多条件筛选 + 卡片列表 + 状态标记 + 手动录入 + CSV/Excel 导出

两者跑在**同一个容器**里，FastAPI 监听 `$PORT`（由 Railway 注入，外部访问），
Streamlit 监听 8501（同容器内部通过 127.0.0.1:8000 调后端）。

## 目录结构

```
wehouse-cloud/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI 入口，/health、CORS、lifespan 自动建表
│   ├── database.py          # SQLAlchemy engine + get_db + init_db
│   ├── models.py            # House ORM 模型
│   ├── schemas.py           # Pydantic 校验模型（含批量导入 BatchCreateRequest）
│   └── routers/
│       ├── __init__.py
│       ├── auth.py          # Token 鉴权依赖（写 token / 只读密码）
│       └── houses.py        # 房源 CRUD + 批量 + 查询筛选
├── frontend/
│   └── streamlit_app.py     # Streamlit 管理界面
├── Dockerfile               # 单容器同时启 uvicorn + streamlit
├── railway.toml             # Railway 部署清单
├── requirements.txt
└── README.md
```

## 本地开发（不依赖 Railway / Supabase）

想在本机先跑通后端 + 前端，最简单的方式是用 SQLite。

### 1. 安装依赖

```bash
cd wehouse-cloud
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 配置环境变量

Linux/macOS:
```bash
export DATABASE_URL="sqlite:///./wehouse_dev.db"
export SECRET_TOKEN="dev-token"
export READONLY_PASSWORD=""
export CORS_ORIGINS="*"
```

Windows (PowerShell):
```powershell
$env:DATABASE_URL="sqlite:///./wehouse_dev.db"
$env:SECRET_TOKEN="dev-token"
$env:READONLY_PASSWORD=""
$env:CORS_ORIGINS="*"
$env:API_BASE="http://127.0.0.1:8000"
$env:WRITE_TOKEN="dev-token"
```

说明：

| 变量 | 是否必填 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | ✅ | PostgreSQL/SQLite 连接串。生产必须是 Supabase PostgreSQL |
| `SECRET_TOKEN` | 写操作必选 | 所有 POST/PATCH/DELETE 的 Bearer Token。**不配置则所有写请求直接被拒** |
| `READONLY_PASSWORD` | 可选 | 为空时 GET 接口公开；配置后 GET 也需要 `Authorization: Bearer <该密码>`（管理员同时可拿 `SECRET_TOKEN` 读） |
| `CORS_ORIGINS` | 可选 | 逗号分隔的允许域名，默认 `*`。生产建议填 Streamlit 所在域名 |
| `API_BASE` | Streamlit 端 | 前端调后端的地址，容器里默认 `http://localhost:8000` 即可 |
| `WRITE_TOKEN` | Streamlit 端 | 前端做写操作（标记已售/删除/添加）的 Token，填 `SECRET_TOKEN` 相同值 |

### 3. 启动两个进程

```bash
# 终端 1：FastAPI
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 终端 2：Streamlit
streamlit run frontend/streamlit_app.py --server.port 8501
```

然后浏览器打开：
- API 文档：http://127.0.0.1:8000/docs
- Streamlit 前端：http://127.0.0.1:8501

### 4. 冒烟测试

用 curl 手动发一条，验证完整链路：

```bash
curl -X POST http://127.0.0.1:8000/api/houses \
  -H "Authorization: Bearer dev-token" \
  -H "Content-Type: application/json" \
  -d '{
    "source": "curl测试",
    "raw_text": "万科城 三室两厅 118.5平 328万 15/28层 王经理 13800138000",
    "area": "万科城",
    "size": "118.5平",
    "price": "328万",
    "layout": "三室两厅",
    "floor": "15/28",
    "contact": "王经理 13800138000",
    "status": "active"
  }'
```

然后：
- `GET http://127.0.0.1:8000/api/houses?area=万科` 能看到它
- Streamlit 刷新也能看到该卡片

## 部署到 Railway + Supabase

### 步骤 1：Supabase 建库

1. 打开 [supabase.com](https://supabase.com) 登录后 New Project
2. 选 Region（建议离 Railway 近一点），设好 DB 密码
3. 等初始化完成后，进入 **Project Settings → Database**
4. 复制 **Connection string → URI**（形如 `postgresql://postgres:xxxx@db.xxx.supabase.co:5432/postgres`）
5. 打开左侧 **SQL Editor**，执行以下建表语句（首次启动 FastAPI 也会自动建，这里推荐显式执行一次）：

```sql
CREATE TABLE IF NOT EXISTS houses (
    id SERIAL PRIMARY KEY,
    source VARCHAR(255) NOT NULL,
    raw_text TEXT NOT NULL,
    area VARCHAR(255),
    size VARCHAR(50),
    price VARCHAR(50),
    layout VARCHAR(50),
    floor VARCHAR(50),
    contact VARCHAR(255),
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_houses_status ON houses(status);
CREATE INDEX IF NOT EXISTS idx_houses_area ON houses(area);
CREATE INDEX IF NOT EXISTS idx_houses_created_at ON houses(created_at DESC);

CREATE OR REPLACE FUNCTION trigger_set_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS set_timestamp ON houses;
CREATE TRIGGER set_timestamp
BEFORE UPDATE ON houses
FOR EACH ROW EXECUTE FUNCTION trigger_set_timestamp();
```

> ⚠️ Railway 端 `DATABASE_URL` **务必直接粘贴 Supabase 的 Connection string**，
> 不要手改任何字符。密码里如果有 `@/:+` 等特殊符号，Supabase 给出的 URI 已自动转义，直接用即可。

### 步骤 2：把代码推到 GitHub

1. 在 GitHub 新建一个仓库，比如 `you/wehouse-cloud`
2. 把 `wehouse-cloud/` 目录下的所有内容（不是整个 wehouse）提交到仓库根目录：
   ```
   Dockerfile
   railway.toml
   requirements.txt
   app/...
   frontend/...
   ```
3. 推送到 `main` 分支

### 步骤 3：Railway 导入并部署

1. 登录 railway.app，**New Project → Deploy from GitHub repo**
2. 选择刚创建的仓库，`railway.toml` 会被自动识别，Builder 走 Dockerfile
3. 进入 Service → **Variables**，配置以下环境变量：

   | Key | Value 示例 | 说明 |
   | --- | --- | --- |
   | `DATABASE_URL` | `postgresql://postgres:...` | Supabase Connection string |
   | `SECRET_TOKEN` | `换一个随机长字符串` | 写操作 Token，和本地 `config.json → cloud.secret_token` 一致 |
   | `READONLY_PASSWORD` | 留空 / 自己设一个密码 | 空=GET 公开；非空=前端 GET 也需要鉴权 |
   | `CORS_ORIGINS` | `*` 或你的域名 | 建议填 `*` 先跑通 |
   | `API_BASE` | `http://127.0.0.1:8000` | Streamlit 访问同容器 FastAPI，**就写这个值** |
   | `WRITE_TOKEN` | 和 `SECRET_TOKEN` 一样 | Streamlit 端做写操作时会带上 |

4. 回到 **Deployments** 触发一次重新部署，或者 Push 一次代码自动触发
5. 等部署成功后，在 Service → **Settings → Networking** 里点击 **Generate Domain**，拿到 `https://xxxx.up.railway.app` 这样的公网域名
6. 验证：
   - `https://xxxx.up.railway.app/health` → `{"status":"ok","service":"wehouse-cloud"}`
   - `https://xxxx.up.railway.app/docs` → 打开 Swagger UI
   - Streamlit 端口说明：**由于 Railway Hobby 只暴露一个 HTTP 端口（即 $PORT，映射到 FastAPI），Streamlit 在单容器部署模式下不会对外暴露 8501**。生产推荐用以下两种方案二选一：

### ⭐ 生产环境 Streamlit 访问方案

**方案 A（推荐）：把 Streamlit 拆成独立 Railway 服务**

同一个 GitHub 仓库里建两个服务：
1. Service 1 (API)：同样代码，启动命令只跑 uvicorn，暴露 8000（$PORT）
2. Service 2 (UI)：同样代码，环境变量把 `API_BASE` 指向 Service 1 的 `https://<api-domain>`，启动命令覆盖为
   ```
   streamlit run frontend/streamlit_app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true
   ```
3. 分别 Generate Domain，得到 `api.xxx.com` 和 `app.xxx.com` 两个地址；本地采集端填前者，用户浏览器访问后者。
4. 再把 Service 1 的 `CORS_ORIGINS` 改成 `https://app.xxx.com` 即可。

**方案 B（本项目默认）：单容器 + 同一端口用反向代理合并**
可以写个 nginx sidecar，把 `/api/*` 和 `/docs` 反代到 127.0.0.1:8000，其他路径反代到 127.0.0.1:8501。Dockerfile 里加一层 nginx 即可，不再赘述。
开发阶段推荐用方案 A，最清晰。

## API 一览

所有写接口都需要请求头：`Authorization: Bearer <SECRET_TOKEN>`。

| Method | Path | 鉴权 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/health` | - | 健康检查 |
| `POST` | `/api/houses` | 写 Token | 新建房源（本地推送用） |
| `POST` | `/api/houses/batch` | 写 Token | 批量导入 1~500 条（预留扩展） |
| `GET` | `/api/houses` | 无 / READONLY_PASSWORD | 列表查询，见下 |
| `GET` | `/api/houses/{id}` | 无 / READONLY_PASSWORD | 单条详情 |
| `PATCH` | `/api/houses/{id}` | 写 Token | 局部更新（修改状态/字段） |
| `DELETE` | `/api/houses/{id}` | 写 Token | 软删除（status=deleted，不真删） |

### GET /api/houses 查询参数

```
GET /api/houses
  ?area=万科城            # 小区名 ILIKE '%万科城%'
  &min_price=200          # 总价（数字部分，万）下限，做服务端二次过滤
  &max_price=400          # 总价上限
  &layout=三室             # 户型 ILIKE '%三室%'
  &status=active          # 精准匹配 status
  &sort=created_at        # 排序字段：created_at | updated_at | id | area | price
  &order=desc             # asc | desc
  &page=1                 # 页码，从 1 开始
  &page_size=20           # 每页 1-500
```

响应：

```json
{
  "items": [ { "id": 1, "area": "万科城", ... } ],
  "total": 123,
  "page": 1,
  "page_size": 20,
  "total_pages": 7
}
```

### 批量导入示例

```bash
curl -X POST https://<api-domain>/api/houses/batch \
  -H "Authorization: Bearer <SECRET_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "items": [
      {"source":"批量A","raw_text":"...","area":"小区A","price":"200万"},
      {"source":"批量B","raw_text":"...","area":"小区B","price":"300万"}
    ]
  }'
```

响应：
```json
{ "created": 1, "failed": 1, "errors": ["第2条: ..."] }
```

## Streamlit 前端操作手册

打开 Streamlit 页面后按从上到下顺序：

1. **侧边栏筛选**
   - 小区：支持模糊匹配，输入「万科」能同时命中「万科城」「万科翡翠」
   - 价格区间：填纯数字，单位默认「万」
   - 户型多选：一室 / 二室 / 三室 / 四室 / 其他（选「其他」会匹配不含这四个关键词的户型）
   - 状态多选：默认隐藏「已删除」
   - 时间范围：按 `created_at` 的日期筛选

2. **顶部统计卡片**：总房源 / 在售 / 已售 / 过期，基于**筛选后**的数据

3. **卡片列表**
   - 每张卡片显示：小区、户型、面积、总价 / 面积 / 楼层 / 状态四指标、来源 + 联系人 + 时间
   - 右上角 `📝` 按钮展开原始消息文本
   - 操作按钮：`✅ 标记已售` / `⏰ 标记过期` / `↩️ 恢复在售` / `🗑️ 删除`（软删除）

4. **➕ 手动添加房源**（展开器）：录入散客/非微信来源的房源

5. **导出**：
   - `📥 导出 CSV`：UTF-8 BOM，Excel 直接打开不乱码
   - `📊 导出 Excel`：xlsx，使用 openpyxl

## 安全加固建议

1. `SECRET_TOKEN` 请用至少 32 位随机字符串，例如 `openssl rand -hex 32` 的输出
2. 生产一定配置 `READONLY_PASSWORD`，避免数据库被爬虫全量扫走
3. `CORS_ORIGINS` 不要留 `*`，改成 Streamlit 域名，逗号分隔支持多个（如 `https://app.xxx.com,http://localhost:8501`）
4. 建议在 Railway 里开启 **Private Networking**，把 Supabase 的 IP 白名单限制为 Railway 出口（或直接用 Supabase SSL，默认就是开启的）
5. 定期备份 Supabase：Project Settings → Backups（Pro Plan 自动 PITR，Free Plan 自己用 pg_dump）

## 故障排查

| 现象 | 排查方向 |
| --- | --- |
| 本地推送 401 Unauthorized | 本地 `cloud.secret_token` 与 Railway `SECRET_TOKEN` 是否一字不差；请求头格式是不是 `Bearer xxx` |
| 本地推送 500 `服务端未配置 SECRET_TOKEN` | Railway Variables 里漏加了 `SECRET_TOKEN`，补加后 **Redeploy** |
| FastAPI 启动 500，日志 `DATABASE_URL 未设置` | Railway Variables 漏加 `DATABASE_URL`，或拼写错误 |
| Supabase 连不上 `password authentication failed` | 复制 Supabase 连接串时不要改密码，若改过 DB 密码在 Supabase Dashboard 里重置后重新复制 |
| Streamlit 打开但列表是空，且无报错 | 检查 `API_BASE` 变量；或 `READONLY_PASSWORD` 已设置但前端没传对（当前前端是通过环境变量注入 header 的，所以前后端密码要一致） |
| 删除后还能搜到 | DELETE 是软删除，`GET /api/houses` 默认排除 `status=deleted`；Streamlit 筛选里不勾选「已删除」也看不到 |
