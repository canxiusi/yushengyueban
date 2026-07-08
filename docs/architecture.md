# Cloud Agent Platform — 架构设计文档

## 1. 项目概述

Cloud Agent Platform 是一个自主 Agent 运行平台。用户提交自然语言任务，平台在云端隔离环境中启动 Agent 自主执行：调用 LLM 进行推理、调用工具（执行命令、读写文件）、循环迭代直到完成，最后返回结果。

**核心能力：**
- 接收自然语言任务描述
- 自动分解并执行复杂任务
- 在隔离沙箱中安全执行代码
- 实时流式返回执行过程
- 支持任务取消与超时保护

---

## 2. 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        Client Layer                               │
│   REST API (submit/query/cancel)    WebSocket (real-time stream) │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                      API Gateway (FastAPI)                        │
│   Route Handling │ Request Validation │ Auth │ Rate Limiting      │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                    Task Scheduler Service                         │
│   Semaphore Concurrency Control │ Task Lifecycle │ Event Bus      │
└───────┬──────────────────────────────────────────┬──────────────┘
        │                                          │
┌───────▼───────────┐                    ┌────────▼────────────┐
│  Agent Orchestrator│                    │   Sandbox Manager    │
│  (LangGraph)       │◄──── tools ──────►│   (Docker)           │
│                    │                    │                      │
│  ReAct Loop:       │                    │  - Container Create  │
│  Think → Act →     │                    │  - Exec Command      │
│  Observe → Repeat  │                    │  - File R/W          │
│                    │                    │  - Resource Limits   │
└───────┬────────────┘                    │  - Network Isolation │
        │                                 └──────────────────────┘
┌───────▼────────────┐
│    LLM Provider     │
│  (OpenAI / Claude / │
│   Qwen / Local)     │
└─────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                     Persistence Layer                             │
│        PostgreSQL (tasks)  │  Redis (sessions, events)           │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. 核心模块设计

### 3.1 Agent 编排与调度

**技术选型：LangGraph StateGraph**

选择 LangGraph 而非简单的 Chain/Prompt Loop，原因：
- **状态可持久化**：每个节点执行后状态可序列化，支持断点恢复
- **条件路由**：根据 LLM 输出动态决定下一步（继续工具调用 or 结束）
- **可观测性**：每个节点的输入输出天然可追踪
- **可扩展**：后续可加入 planning 节点、reflection 节点、human-in-the-loop

**Agent 执行流程：**

```
                    ┌─────────────┐
                    │   START     │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
              ┌────►│  Reasoning  │ ◄─── LLM + Tools binding
              │     │   (Think)   │
              │     └──────┬──────┘
              │            │
              │     ┌──────▼──────┐
              │     │  Has Tool   │
              │     │   Calls?    │
              │     └──┬──────┬───┘
              │        │Yes   │No
              │  ┌─────▼───┐  │
              │  │  Tool    │  │
              │  │ Executor │  │
              │  └─────┬───┘  │
              │        │      │
              └────────┘  ┌───▼───┐
                          │  END  │
                          └───────┘
```

**调度策略：**
- **并发控制**：Semaphore 限制同时运行的 Agent 数量（默认 10）
- **超时保护**：每个任务有最大执行时间（默认 600s）
- **迭代上限**：Agent 最多循环 25 次，防止无限循环
- **优雅降级**：超时/异常时强制终止并清理资源

### 3.2 沙箱与隔离执行

**技术选型：Docker Container**

每个任务独占一个 Docker 容器，提供多维度隔离：

| 维度 | 策略 | 说明 |
|------|------|------|
| 进程隔离 | 独立容器 | 每个 task 一个容器，互不影响 |
| 网络隔离 | internal network | 容器间不可互通，无公网访问 |
| 资源限制 | cgroup | CPU 1核、内存 512MB |
| 文件系统 | ephemeral | 容器销毁后所有数据清除 |
| 权限控制 | cap_drop ALL | 移除所有 Linux capabilities |
| 时间限制 | timeout | 超时强制 kill |

**沙箱生命周期：**
```
Create → [Execute Commands / R/W Files] → Destroy
  │                                          │
  │  task_id label for tracking              │  force remove
  │  sleep infinity keeps alive              │  cleanup on timeout/error
```

**安全考量：**
- 容器无特权运行
- 不挂载宿主机敏感路径
- 网络隔离防止数据泄露
- tmpfs 限制临时文件空间
- 定时清理孤儿容器

### 3.3 LLM 集成与工具调用

**Tool Calling 机制：**

采用 LangChain Tool 协议 + OpenAI Function Calling 格式：

```python
# Agent 可用工具
execute_command(command: str)   # 在沙箱中执行 shell 命令
write_file(path: str, content: str)  # 写文件
read_file(path: str)           # 读文件
list_files(path: str)          # 列出目录
```

**工具绑定策略：**
- 运行时通过闭包将 `sandbox_id` 注入工具函数
- LLM 看到的是通用接口，实际执行发生在对应容器内
- 工具输出自动截断（stdout 10KB, stderr 5KB），防止上下文溢出

**LLM 可替换设计：**
```python
# 通过配置切换 LLM provider
LLM_PROVIDER=openai    # GPT-4o
LLM_PROVIDER=anthropic # Claude
LLM_PROVIDER=dashscope # Qwen
LLM_BASE_URL=...       # 兼容 OpenAI 协议的任意 endpoint
```

### 3.4 实时通信

**WebSocket 事件流：**

客户端连接 `ws://host/ws/tasks/{task_id}` 后实时接收：

```json
{"event_type": "step", "data": {"message": "Creating sandbox..."}}
{"event_type": "tool_call", "data": {"tool": "execute_command", "args": {"command": "pip install pandas"}}}
{"event_type": "tool_result", "data": {"output": "Successfully installed pandas-2.2.0"}}
{"event_type": "thinking", "data": {"thought": "Now I need to write the analysis script..."}}
{"event_type": "done", "data": {"status": "completed", "result": "Report generated."}}
```

---

## 4. 数据模型

```sql
CREATE TABLE tasks (
    task_id     VARCHAR(26) PRIMARY KEY,  -- ULID, 时间有序
    status      ENUM('pending','running','completed','failed','cancelled'),
    prompt      TEXT NOT NULL,
    context     JSON,
    result      TEXT,
    error       TEXT,
    steps       JSON DEFAULT '[]',       -- 执行步骤记录
    sandbox_id  VARCHAR(64),
    timeout     INTEGER DEFAULT 300,
    created_at  TIMESTAMP DEFAULT NOW(),
    started_at  TIMESTAMP,
    completed_at TIMESTAMP
);
```

---

## 5. API 设计

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/tasks` | 提交新任务 |
| GET | `/api/tasks` | 列出任务（分页） |
| GET | `/api/tasks/{id}` | 查询任务详情 |
| POST | `/api/tasks/{id}/cancel` | 取消任务 |
| WS | `/ws/tasks/{id}` | 实时事件流 |
| GET | `/health` | 健康检查 |

**提交任务示例：**
```bash
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"prompt": "读取当前目录，找出所有 TODO 注释，生成一份 markdown 报告"}'
```

---

## 6. 可扩展性设计

### 6.1 水平扩展

```
                    ┌──────────────┐
                    │  Load Balancer│
                    └───┬──────┬───┘
                        │      │
              ┌─────────▼┐  ┌──▼─────────┐
              │ Platform-1│  │ Platform-2  │
              └─────┬─────┘  └──────┬─────┘
                    │               │
              ┌─────▼───────────────▼─────┐
              │    Shared Redis (Queue)    │
              │    Shared PostgreSQL       │
              └───────────────────────────┘
              │    Docker Host Pool         │
              └───────────────────────────┘
```

**扩展方向：**
- **任务队列化**：用 Redis/RabbitMQ 替代内存 Semaphore，多实例竞争消费
- **Docker 集群**：接入 Docker Swarm / K8s，容器调度到不同节点
- **Agent 池化**：预热容器，减少冷启动时间
- **分级执行**：简单任务用轻量容器，复杂任务用 GPU 容器

### 6.2 插件式工具扩展

```python
# 注册自定义工具 — 只需实现接口
@tool
async def web_search(query: str) -> str:
    """Search the web for information."""
    ...

@tool
async def clone_repo(url: str) -> str:
    """Clone a git repository into the workspace."""
    ...
```

### 6.3 Multi-Agent 编排（未来）

```
            ┌─────────────┐
            │  Supervisor  │
            └──┬───┬───┬──┘
               │   │   │
         ┌─────▼┐ ┌▼────┐ ┌▼─────┐
         │Coder │ │Tester│ │Writer│
         └──────┘ └─────┘ └──────┘
```

---

## 7. 技术栈总结

| 层级 | 技术 | 选型理由 |
|------|------|----------|
| Web 框架 | FastAPI | 异步、高性能、自动文档 |
| Agent 编排 | LangGraph | 状态图、可持久化、条件路由 |
| LLM 接入 | LangChain + OpenAI Protocol | 多模型统一接口 |
| 沙箱 | Docker | 成熟隔离方案、资源控制精确 |
| 数据库 | PostgreSQL + SQLAlchemy Async | 生产级可靠性 |
| 缓存/队列 | Redis | 事件发布、会话缓存 |
| 实时通信 | WebSocket | 低延迟双向通信 |
| 部署 | Docker Compose / K8s | 一键启动、云原生 |

---

## 8. 部署与运行

```bash
# 开发环境
cp .env.example .env  # 填写 LLM_API_KEY
pip install -r requirements.txt
uvicorn app.main:app --reload

# Docker Compose 一键启动
docker-compose up -d

# 提交任务
curl -X POST http://localhost:8000/api/tasks \
  -d '{"prompt": "写一个 Python 脚本统计当前目录下所有文件的行数"}'
```

---

## 9. 与同类产品对比

| 特性 | 本平台 | Claude Code Cloud | Devin |
|------|--------|-------------------|-------|
| Agent 循环 | ReAct (LangGraph) | 内置 | 多阶段 |
| 沙箱 | Docker 容器 | 云端 VM | 云端沙箱 |
| 工具 | 可插拔 | 固定集合 | 固定集合 |
| 实时反馈 | WebSocket | Streaming | Web UI |
| 开源 | 是 | 否 | 否 |
| 多模型 | 是（OpenAI协议） | Claude only | GPT-4 |
