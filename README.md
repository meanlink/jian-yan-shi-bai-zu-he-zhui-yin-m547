# 检验失败组合追因

关联规则的真实业务分析小型项目。完整模拟客户、痛点、上下游、算法公式与一天工作剧本见 [BUSINESS.md](BUSINESS.md)。

## 模块与业务流程
数据接入（JSON/CSV、字段验证、批次指纹）→ 参数化分析（异步 Python worker）→ 结果复核（指标、图形、明细、解释、批准）→ 定时分发（本地收件箱、CSV/JSON、SHA256）；另有业务总览和审计轨迹。
三个贯穿全栈的实体：Dataset 数据批次、Job 分析任务、Report 分发计划；辅助 User、Audit、Delivery。
样品跨库交接链独立于分析：Handover 交接单、HandoverEvent 事件链、SampleCustody 批号保管归属、BatchGate 批号闸门、HandoverRequest 幂等回放。整单发起即在途，指定接收员核对实物后整单签收；数量不符整单转争议；发起人可撤回未签收单；争议由交接双方之外的负责人裁定并留原因。在途或争议批号禁止新分析，交接与入队同时发生时由同一批号闸门裁定。服务重启自动恢复交接归属，重复操作按 Idempotency-Key 回放，不重复转库。

## 快速启动
```bash
cp .env.example .env
docker compose up -d --build --wait
docker compose exec -T backend python -m scripts.seed_demo
```
访问 http://localhost:18211；后端文档 http://localhost:19211/docs；数据库宿主端口 17711。
`SEED_DEMO=true` 自动幂等 seed；不覆盖用户修改。手动 seed 命令可重复执行。

## 演示账号与权限
|账号|密码|真实权限|
|---|---|---|
|admin@demo.local|Demo123!|接入、执行、批准、分发、暂停、读取与下载；交接争议裁定（不得代签、不得裁定自己参与的单）|
|analyst@demo.local|Demo123!|接入、执行、读取与下载；作为交出经办发起交接、撤回未签收单；不能批准或安排分发|
|receiver@demo.local|Demo123!|分析员权限；作为指定接收员核对实物后整单签收或转争议|
|viewer@demo.local|Demo123!|只读与下载|

交接演示数据：`HP-R001 复检留样批`（在途，待接收员签收）、`HP-R002 复检留样批`（争议，待负责人裁定）、`HP-R003 复检留样批`（已签收）、`HP-R004 复检留样批`（已撤回）。页面「样品交接」可交出、签收、撤回、裁定并按批号追查去向。

## 技术与本地开发
Python 3.12 / FastAPI / SQLAlchemy / NumPy / SciPy / scikit-learn；MySQL 8.0；Redis 7；Nginx；原生 HTML CSS JavaScript ES modules（用户指定）。后端所有校验、持久化和算法在服务端。无前端打包步骤。
后端：安装 backend/requirements.txt，设置 DATABASE_URL、REDIS_URL、JWT_SECRET、ARTIFACT_DIR 后在 backend/ 执行 `python -m migrations.initial`、`python -m scripts.seed_demo`、`uvicorn src.main:app`，另开 `python -m src.worker`。前端使用带 /api 反向代理的 Nginx，直接打开 index.html 不支持 API。

## 目录与工程约定
backend/src/{models,schemas,services,routes,controllers,middlewares,constants,config,utils}；backend/scripts/seed_demo.py；backend/migrations/initial.py。
frontend/src/{api,stores,types,components/common,hooks,pages,router,utils,constants}。
Role 枚举：admin/analyst/viewer；JobStatus：queued/running/completed/failed；HandoverStatus：in_transit/signed/withdrawn/dispute/resolved。后端 backend/src/constants/enums.py，前端 frontend/src/constants/enums.js；路由权限在 middlewares/auth.py；页面按相同角色控制操作入口。交接服务 backend/src/services/handovers.py，路由 backend/src/routes/handovers.py。

## 环境变量与 Docker
COMPOSE_PROJECT_NAME=assay-rules；FRONTEND_PORT=18211；BACKEND_PORT=19211；DB_PORT=17711；DB_PASSWORD、JWT_SECRET 见 .env.example；SEED_DEMO 默认 true。共享命名产物卷供后端下载与 worker 写入，数据库/Redis 各持久卷。
恰好五服务均有健康检查。Redis 队列加 SQL 排队状态兜底；worker 按 next_run 生成本地收件箱文件。周期 60 秒至 30 天。分发结果为批准分析的固定快照；若需新数据重新计算并建立新计划。当前不连接邮件或企业聊天。
错误响应、请求编号、Redis 每 IP 每分钟 240 次限流、JWT 和数据库操作审计。演示密钥与密码仅供本机模拟；生产部署前配置独立密钥与账号。

## 数据与验收
所有数据为固定随机种子生成的模拟业务数据，不能作为实际生产决策证据。项目独立算法、输入边界及业务局限详见 BUSINESS.md。测试与截图统一存放 ../../test-artifacts/gb-1012/，源码不含日志/截图/测试报告。
清理本项目演示数据：`docker compose down -v --remove-orphans`（会删除本项目命名卷）。

## License
MIT，见 LICENSE。
