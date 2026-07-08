# Cloud Agent Platform

> 笔试编号：d6b826d2-b978-4c35-90c3-904a2872e1ec
> 
> 岗位：全栈工程师（AI Native）

用户提交自然语言任务，平台启动自主 Agent 在云端隔离环境中执行——调用 LLM 推理、调用工具（执行命令、读写文件）、循环迭代直到完成，最后返回结果。

## 核心设计

| 模块 | 实现 | 说明 |
|------|------|------|
| Agent 编排 | LangGraph `create_react_agent` | ReAct 循环，支持迭代上限、超时保护 |
| 沙箱隔离 | Docker 容器 / Local subprocess | 网络隔离、资源限制、执行后自动销毁 |
| LLM 集成 | LangChain OpenAI Protocol | 多模型切换（GPT-4o / Claude / Qwen） |
| 工具调用 | LangChain Tool + Function Calling | 运行时闭包绑定沙箱上下文 |
| 实时通信 | WebSocket 事件流 | 逐步推送 Agent 执行过程 |
| 任务调度 | Semaphore 并发控制 | 可水平扩展至队列模式 |

## Quick Start

```bash
# 1. 创建虚拟环境并安装依赖
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填写 LLM_API_KEY

# 3. 启动服务
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 10020 --reload

# 4. 运行测试
.venv/bin/python -m pytest tests/ -v
```

## API 使用

```bash
# 提交任务
curl -X POST http://localhost:10020/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"prompt": "写一个 Python 脚本统计当前目录文件行数，生成 markdown 报告"}'

# 查询任务结果
curl http://localhost:10020/api/tasks/{task_id}

# 任务列表
curl http://localhost:10020/api/tasks

# 取消任务
curl -X POST http://localhost:10020/api/tasks/{task_id}/cancel

# WebSocket 实时监听
wscat -c ws://localhost:10020/ws/tasks/{task_id}
```

## 项目结构

```
├── app/
│   ├── main.py              # FastAPI 入口，生命周期管理
│   ├── config.py            # pydantic-settings 配置
│   ├── agents/
│   │   ├── cloud_agent.py   # LangGraph create_react_agent 编排
│   │   └── tools.py         # 沙箱工具定义（exec/read/write/list）
│   ├── core/
│   │   ├── database.py      # SQLAlchemy Async + 数据模型
│   │   └── llm_factory.py   # LLM 实例工厂（多 Provider）
│   ├── sandbox/
│   │   ├── docker_sandbox.py  # Docker 容器隔离（生产）
│   │   └── local_sandbox.py   # subprocess 隔离（开发）
│   ├── services/
│   │   └── task_service.py  # 任务生命周期、并发调度、事件总线
│   ├── routers/
│   │   ├── tasks.py         # REST API
│   │   └── ws.py            # WebSocket 实时流
│   └── schemas/
│       └── task.py          # Pydantic 请求/响应模型
├── tests/                   # 63 个测试用例，全部通过
├── deploy/k8s/              # Kubernetes 部署配置
├── docs/architecture.md     # 详细架构设计文档
├── docker-compose.yml       # 一键启动（Platform + PG + Redis）
├── Dockerfile
└── requirements.txt
```

## 测试报告

```
tests/test_schemas.py       - Schema 模型验证（合法/非法输入边界）
tests/test_sandbox.py       - 沙箱全流程（创建/执行/文件IO/超时/销毁）
tests/test_tools.py         - Agent 工具函数（正常/异常/空输出）
tests/test_agent.py         - Agent 编排（成功/失败/无结果）
tests/test_api.py           - REST API 端点（CRUD/分页/404/422）
tests/test_task_service.py  - 任务服务（创建/查询/取消/超时/降级）

运行结果: 63 passed, 0 failed
```

## 部署

```bash
# Docker Compose（含 PostgreSQL + Redis）
docker-compose up -d

# Kubernetes
kubectl apply -f deploy/k8s/deployment.yaml
```

## 架构文档

详见 [docs/architecture.md](docs/architecture.md)，包含：
- 系统架构图
- Agent 编排流程设计
- 沙箱安全隔离策略
- LLM 工具调用机制
- 水平扩展方案
- Multi-Agent 演进路线
