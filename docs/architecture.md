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

### 3.1 Agent 编排与调度（双层架构）

采用 **两层分离架构**，外层用 LangGraph StateGraph 管理任务生命周期，内层用 `langchain.agents.create_agent` 驱动 ReAct 工具调用循环：

| 层级 | 技术 | 职责 |
|------|------|------|
| 外层：任务编排 | LangGraph StateGraph | 状态管理、条件路由、质量检查、重试机制 |
| 内层：Agent 推理 | create_agent (ReAct) | LLM 推理、工具调用、思考-行动-观察循环 |

**为什么分两层？**
- **状态可持久化**：LangGraph 的 TypedDict 状态可序列化，支持断点恢复
- **关注点分离**：任务生命周期管理与 Agent 推理逻辑互不耦合
- **质量把关**：外层可以对内层 Agent 的产出做质量检查，不满足则自动重试
- **可观测性**：每个 Graph 节点的进出状态天然可追踪
- **可扩展**：后续可新增 planning 节点、reflection 节点、human-in-the-loop 节点

**外层 StateGraph（任务生命周期）：**

```
┌────────────────┐
│  setup_sandbox │  ← 创建隔离执行环境
└───────┬────────┘
        │ (成功)
┌───────▼────────┐
│   run_agent    │  ← 内层 ReAct 循环 (create_agent)
└───────┬────────┘
        │
┌───────▼────────┐          ┌──────────┐
│ quality_check  │──(重试)──►│  retry   │
└───────┬────────┘          └─────┬────┘
        │ (通过)                   │
   ┌────▼───┐                     │
   │  END   │       ◄─────────────┘ (回到 run_agent)
   └────────┘
```

**内层 ReAct Loop（create_agent 封装）：**

```
              ┌────►│  Reasoning  │ ◄─── LLM + System Prompt + Tools
              │     │   (Think)   │
              │     └──────┬──────┘
              │            │
              │     ┌──────▼──────┐
              │     │  Has Tool   │
              │     │   Calls?    │
              │     └──┬──────┬───┘
              │        │Yes   │No
              │  ┌─────▼───┐  │
              │  │ Sandbox  │  │
              │  │  Tool    │  │
              │  └─────┬───┘  │
              └────────┘  ┌───▼───┐
                          │Return │
                          └───────┘
```

**调度策略：**
- **并发控制**：Semaphore 限制同时运行的 Agent 数量（默认 10）
- **超时保护**：每个任务有最大执行时间（默认 600s）
- **迭代上限**：Agent 最多循环 25 次（recursion_limit），防止无限循环
- **质量检查**：执行完后自动校验是否有实际工具调用和有效输出
- **自动重试**：质量不达标时最多重试 2 次
- **优雅降级**：超时/异常时强制终止并清理资源

### 3.2 沙箱与隔离执行

**设计原则：统一接口，双模实现**

沙箱层对上层（Agent 工具）暴露统一的 5 个方法接口，底层提供两套实现，通过环境变量 `SANDBOX_MODE` 切换：

```
SANDBOX_MODE=docker  → DockerSandbox（生产环境，真正隔离）
SANDBOX_MODE=local   → LocalSandbox（开发调试，无真实隔离）
```

#### 3.2.1 生产模式：DockerSandbox（容器隔离）

**生产环境必须使用 Docker 模式。** 每个任务独占一个 Docker 容器，提供多维度安全隔离：

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
- tmpfs 限制临时文件空间（100MB）
- 定时清理孤儿容器（label 标记 `managed-by=cloud-agent-platform`）

**生产部署配置：**
```bash
# .env
SANDBOX_MODE=docker
SANDBOX_IMAGE=python:3.11-slim    # 基础沙箱镜像
SANDBOX_MEMORY_LIMIT=512m
SANDBOX_CPU_LIMIT=1.0
SANDBOX_NETWORK=agent-sandbox-net
```

#### 3.2.2 开发模式：LocalSandbox（仅限本地调试）

> ⚠️ **LocalSandbox 是 dev-only stub，不提供真实安全隔离，严禁用于生产环境。**

LocalSandbox 使用 `tempfile.mkdtemp` + `asyncio.create_subprocess_shell` 实现，目的是让开发者在没有 Docker daemon 的环境下也能完整调试 Agent 工具调用流程。

| 能力 | LocalSandbox | DockerSandbox |
|------|:---:|:---:|
| 文件路径隔离 | ✅ 临时目录 | ✅ 容器文件系统 |
| 进程隔离 | ❌ 同一宿主机 | ✅ 独立容器 |
| 网络隔离 | ❌ 无 | ✅ internal network |
| 资源限制 | ❌ 无 | ✅ cgroup |
| 权限控制 | ❌ 继承宿主 | ✅ cap_drop ALL |
| 超时保护 | ✅ wait_for + kill | ✅ wait_for + container stop |
| 输出截断 | ✅ 10KB/5KB | ✅ 10KB/5KB |
| 用完即毁 | ✅ rmtree | ✅ container remove |

**本地开发配置：**
```bash
# .env
SANDBOX_MODE=local   # 默认值，无需 Docker
```

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
