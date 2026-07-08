"""
Cloud Agent: two-layer architecture using LangGraph.

Outer layer: LangGraph StateGraph manages task lifecycle
  - setup_sandbox → run_agent → collect_result → (quality_check) → END

Inner layer: langchain create_agent handles ReAct tool-calling loop
  - Think → Act (tool call) → Observe → repeat until done
"""

import logging
from datetime import datetime
from functools import partial
from typing import Annotated, Optional, TypedDict

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from app.agents.tools import create_sandbox_tools
from app.config import settings
from app.core.llm_factory import create_agent_llm

if settings.sandbox_mode == "docker":
    from app.sandbox.docker_sandbox import sandbox_manager as sandbox
else:
    from app.sandbox.local_sandbox import local_sandbox as sandbox

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 25

SYSTEM_PROMPT = """You are an autonomous cloud agent. You execute tasks in an isolated sandbox environment.

You have access to these tools:
- execute_command: Run shell commands (bash). Use for installing packages, running scripts, git operations, etc.
- write_file: Create or overwrite files. Use relative paths like "hello.py" or "src/main.py".
- read_file: Read file contents. Use relative paths.
- list_files: List files in a directory. Use "." for current directory.

Guidelines:
1. Break complex tasks into steps. Think before acting.
2. After each tool call, assess progress and decide next action.
3. Install dependencies as needed (pip, apt-get, npm, etc.)
4. If a command fails, analyze the error and try a different approach.
5. When the task is complete, summarize what was accomplished.
6. Always use RELATIVE paths (e.g. "report.md", not "/workspace/report.md").
7. For code tasks: write the code, test it, and verify it works.
8. Commands execute in the workspace root directory by default.

Important: You are in an isolated environment with bash, python3, and common CLI tools available.
You can install additional tools with apt-get or pip as needed."""


# ═══════════════════════════════════════════════════════════════
# State Definition — LangGraph typed state for task lifecycle
# ═══════════════════════════════════════════════════════════════


class TaskState(TypedDict):
    task_id: str
    prompt: str
    sandbox_id: Optional[str]
    messages: Annotated[list, add_messages]
    steps: list[dict]
    result: Optional[str]
    error: Optional[str]
    status: str
    retry_count: int


# ═══════════════════════════════════════════════════════════════
# Graph Nodes — each node is a stage in the task lifecycle
# ═══════════════════════════════════════════════════════════════


async def setup_sandbox_node(state: TaskState) -> dict:
    """Create isolated sandbox environment for this task."""
    try:
        sandbox_id = await sandbox.create_sandbox(state["task_id"])
        return {"sandbox_id": sandbox_id, "status": "sandbox_ready"}
    except Exception as e:
        return {"error": f"Sandbox creation failed: {e}", "status": "failed"}


async def run_agent_node(state: TaskState) -> dict:
    """Execute the ReAct agent loop inside the sandbox (inner layer: create_agent)."""
    llm = create_agent_llm()
    sandbox_id = state["sandbox_id"]

    exec_fn = partial(sandbox.exec_command, sandbox_id)
    write_fn = partial(sandbox.write_file, sandbox_id)
    read_fn = partial(sandbox.read_file, sandbox_id)
    list_fn = partial(sandbox.list_files, sandbox_id)

    tools = create_sandbox_tools(exec_fn, write_fn, read_fn, list_fn)

    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
    )

    try:
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=state["prompt"])]},
            config={"recursion_limit": MAX_ITERATIONS * 2},
        )

        steps = []
        for msg in result["messages"]:
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    steps.append(
                        {
                            "step_id": len(steps) + 1,
                            "action": "tool_call",
                            "tool_name": tc["name"],
                            "tool_input": tc["args"],
                            "timestamp": datetime.utcnow().isoformat(),
                        }
                    )
            if hasattr(msg, "content") and msg.content and hasattr(msg, "tool_call_id"):
                if steps:
                    steps[-1]["tool_output"] = str(msg.content)[:5000]

        final_content = None
        for msg in reversed(result["messages"]):
            if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
                final_content = msg.content
                break

        return {
            "messages": result["messages"],
            "steps": steps,
            "result": final_content or "Task completed (no final summary)",
            "status": "agent_done",
        }
    except Exception as e:
        logger.exception(f"Agent execution failed for task {state['task_id']}")
        return {"error": str(e), "status": "agent_error"}


async def quality_check_node(state: TaskState) -> dict:
    """Verify that the agent actually produced a meaningful result."""
    if state.get("status") == "agent_error":
        return {"status": "needs_retry"}

    result = state.get("result", "")
    steps = state.get("steps", [])

    if not steps:
        return {"status": "needs_retry", "error": "Agent produced no actions"}

    if len(result) < 10 and len(steps) < 2:
        return {"status": "needs_retry", "error": "Result too shallow"}

    return {"status": "completed"}


async def retry_node(state: TaskState) -> dict:
    """Increment retry counter; will re-enter agent if under limit."""
    return {"retry_count": state.get("retry_count", 0) + 1, "status": "retrying"}


# ═══════════════════════════════════════════════════════════════
# Routing Logic — conditional edges for the state graph
# ═══════════════════════════════════════════════════════════════


def route_after_sandbox(state: TaskState) -> str:
    if state.get("error"):
        return "end"
    return "run_agent"


def route_after_agent(state: TaskState) -> str:
    if state.get("status") == "agent_error":
        return "quality_check"
    return "quality_check"


def route_after_check(state: TaskState) -> str:
    if state["status"] == "completed":
        return "end"
    if state.get("retry_count", 0) >= 2:
        return "end"
    return "retry"


def route_after_retry(state: TaskState) -> str:
    return "run_agent"


# ═══════════════════════════════════════════════════════════════
# Graph Construction
# ═══════════════════════════════════════════════════════════════


def build_task_graph():
    """
    Build the full task lifecycle graph:

    setup_sandbox → run_agent → quality_check ──→ END
                       ↑              │
                       └── retry ←────┘ (if needs_retry & retries < 2)
    """
    graph = StateGraph(TaskState)

    graph.add_node("setup_sandbox", setup_sandbox_node)
    graph.add_node("run_agent", run_agent_node)
    graph.add_node("quality_check", quality_check_node)
    graph.add_node("retry", retry_node)

    graph.set_entry_point("setup_sandbox")

    graph.add_conditional_edges(
        "setup_sandbox",
        route_after_sandbox,
        {
            "run_agent": "run_agent",
            "end": END,
        },
    )
    graph.add_conditional_edges(
        "run_agent",
        route_after_agent,
        {
            "quality_check": "quality_check",
        },
    )
    graph.add_conditional_edges(
        "quality_check",
        route_after_check,
        {
            "end": END,
            "retry": "retry",
        },
    )
    graph.add_conditional_edges(
        "retry",
        route_after_retry,
        {
            "run_agent": "run_agent",
        },
    )

    return graph.compile()


# ═══════════════════════════════════════════════════════════════
# Public API — called by task_service
# ═══════════════════════════════════════════════════════════════


async def run_agent(task_id: str, prompt: str, sandbox_id: str, on_step=None) -> dict:
    """
    Execute the full task lifecycle via LangGraph StateGraph.

    Args:
        task_id: Unique task identifier
        prompt: User's natural language task description
        sandbox_id: Pre-created sandbox ID (ignored — graph manages its own sandbox)
        on_step: Optional callback for real-time step notifications

    Returns:
        dict with final result, steps taken, and status
    """
    graph = build_task_graph()

    initial_state: TaskState = {
        "task_id": task_id,
        "prompt": prompt,
        "sandbox_id": sandbox_id,
        "messages": [],
        "steps": [],
        "result": None,
        "error": None,
        "status": "pending",
        "retry_count": 0,
    }

    try:
        final_state = await graph.ainvoke(initial_state)
        return {
            "status": "completed" if final_state["status"] == "completed" else "failed",
            "result": final_state.get("result"),
            "error": final_state.get("error"),
            "steps": final_state.get("steps", []),
            "iterations": len(final_state.get("steps", [])),
        }
    except Exception as e:
        logger.exception(f"Task graph execution failed for {task_id}")
        return {
            "status": "failed",
            "error": str(e),
            "steps": [],
            "iterations": 0,
        }
