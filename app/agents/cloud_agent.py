"""
Cloud Agent: uses LangGraph's built-in create_react_agent for the ReAct loop.

create_react_agent handles the Think → Act → Observe cycle internally,
we wrap it with step tracking and sandbox lifecycle management.
"""

import logging
from datetime import datetime
from functools import partial

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

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


async def run_agent(task_id: str, prompt: str, sandbox_id: str, on_step=None) -> dict:
    """
    Execute the agent loop using LangGraph's create_react_agent.

    Args:
        task_id: Unique task identifier
        prompt: User's natural language task description
        sandbox_id: Sandbox identifier for isolated execution
        on_step: Optional callback for real-time step notifications

    Returns:
        dict with final result, steps taken, and status
    """
    llm = create_agent_llm()

    exec_fn = partial(sandbox.exec_command, sandbox_id)
    write_fn = partial(sandbox.write_file, sandbox_id)
    read_fn = partial(sandbox.read_file, sandbox_id)
    list_fn = partial(sandbox.list_files, sandbox_id)

    tools = create_sandbox_tools(exec_fn, write_fn, read_fn, list_fn)

    agent = create_react_agent(
        model=llm,
        tools=tools,
        prompt=SystemMessage(content=SYSTEM_PROMPT),
    )

    try:
        steps = []
        final_content = None

        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=prompt)]},
            config={"recursion_limit": MAX_ITERATIONS * 2},
        )

        for msg in result["messages"]:
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    steps.append({
                        "step_id": len(steps) + 1,
                        "action": "tool_call",
                        "tool_name": tc["name"],
                        "tool_input": tc["args"],
                        "timestamp": datetime.utcnow().isoformat(),
                    })
            if hasattr(msg, "content") and msg.content and hasattr(msg, "tool_call_id"):
                if steps:
                    steps[-1]["tool_output"] = str(msg.content)[:5000]

        for msg in reversed(result["messages"]):
            if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
                final_content = msg.content
                break

        return {
            "status": "completed",
            "result": final_content or "Task completed (no final summary)",
            "steps": steps,
            "iterations": len(steps),
        }

    except Exception as e:
        logger.exception(f"Agent execution failed for task {task_id}")
        return {
            "status": "failed",
            "error": str(e),
            "steps": [],
            "iterations": 0,
        }
