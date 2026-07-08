from unittest.mock import AsyncMock, patch

import pytest

from app.agents.cloud_agent import (
    SYSTEM_PROMPT,
    build_task_graph,
    quality_check_node,
    run_agent,
    setup_sandbox_node,
)


class TestRunAgent:
    """Integration tests for the full LangGraph task pipeline."""

    @pytest.mark.asyncio
    @patch("app.agents.cloud_agent.sandbox")
    @patch("app.agents.cloud_agent.create_agent")
    @patch("app.agents.cloud_agent.create_agent_llm")
    async def test_successful_execution(
        self, mock_llm_factory, mock_create_agent, mock_sandbox
    ):
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        mock_sandbox.create_sandbox = AsyncMock(return_value="sb-1")
        mock_sandbox.exec_command = AsyncMock(
            return_value={"exit_code": 0, "stdout": "ok", "stderr": ""}
        )
        mock_sandbox.write_file = AsyncMock(
            return_value={"exit_code": 0, "stdout": "Written", "stderr": ""}
        )
        mock_sandbox.read_file = AsyncMock(
            return_value={"exit_code": 0, "stdout": "", "stderr": ""}
        )
        mock_sandbox.list_files = AsyncMock(
            return_value={"exit_code": 0, "stdout": "", "stderr": ""}
        )

        mock_agent = AsyncMock()
        mock_create_agent.return_value = mock_agent
        mock_agent.ainvoke.return_value = {
            "messages": [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content="write hello.py"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {"path": "hello.py", "content": "print('hi')"},
                            "id": "tc1",
                        }
                    ],
                ),
                AIMessage(content="Done! Created hello.py successfully."),
            ]
        }

        result = await run_agent("task-1", "write hello.py", "sandbox-1")
        assert result["status"] == "completed"
        assert "Done" in result["result"]
        assert len(result["steps"]) >= 1

    @pytest.mark.asyncio
    @patch("app.agents.cloud_agent.sandbox")
    @patch("app.agents.cloud_agent.create_agent")
    @patch("app.agents.cloud_agent.create_agent_llm")
    async def test_agent_exception(
        self, mock_llm_factory, mock_create_agent, mock_sandbox
    ):
        mock_sandbox.create_sandbox = AsyncMock(return_value="sb-2")
        mock_sandbox.exec_command = AsyncMock()
        mock_sandbox.write_file = AsyncMock()
        mock_sandbox.read_file = AsyncMock()
        mock_sandbox.list_files = AsyncMock()

        mock_agent = AsyncMock()
        mock_create_agent.return_value = mock_agent
        mock_agent.ainvoke.side_effect = Exception("LLM API error")

        result = await run_agent("task-2", "do stuff", "sandbox-2")
        assert result["status"] == "failed"
        assert result["error"] is not None

    @pytest.mark.asyncio
    @patch("app.agents.cloud_agent.sandbox")
    @patch("app.agents.cloud_agent.create_agent")
    @patch("app.agents.cloud_agent.create_agent_llm")
    async def test_no_final_message(
        self, mock_llm_factory, mock_create_agent, mock_sandbox
    ):
        from langchain_core.messages import AIMessage, HumanMessage

        mock_sandbox.create_sandbox = AsyncMock(return_value="sb-3")
        mock_sandbox.exec_command = AsyncMock(
            return_value={"exit_code": 0, "stdout": "ok", "stderr": ""}
        )
        mock_sandbox.write_file = AsyncMock(
            return_value={"exit_code": 0, "stdout": "Written", "stderr": ""}
        )
        mock_sandbox.read_file = AsyncMock(
            return_value={"exit_code": 0, "stdout": "", "stderr": ""}
        )
        mock_sandbox.list_files = AsyncMock(
            return_value={"exit_code": 0, "stdout": "", "stderr": ""}
        )

        mock_agent = AsyncMock()
        mock_create_agent.return_value = mock_agent
        mock_agent.ainvoke.return_value = {
            "messages": [
                HumanMessage(content="test"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "execute_command",
                            "args": {"command": "ls"},
                            "id": "tc1",
                        }
                    ],
                ),
            ]
        }

        result = await run_agent("task-3", "test", "sandbox-3")
        assert "no final summary" in result["result"].lower()


class TestQualityCheckNode:
    """Unit tests for the quality_check gate."""

    @pytest.mark.asyncio
    async def test_passes_with_good_result(self):
        state = {
            "result": "Successfully created the file and ran tests.",
            "steps": [{"step_id": 1, "action": "tool_call"}],
        }
        out = await quality_check_node(state)
        assert out["status"] == "completed"

    @pytest.mark.asyncio
    async def test_fails_with_no_steps(self):
        state = {"result": "ok", "steps": []}
        out = await quality_check_node(state)
        assert out["status"] == "needs_retry"

    @pytest.mark.asyncio
    async def test_fails_with_shallow_result(self):
        state = {"result": "ok", "steps": [{"step_id": 1}]}
        out = await quality_check_node(state)
        assert out["status"] == "needs_retry"


class TestSetupSandboxNode:
    """Unit tests for sandbox creation node."""

    @pytest.mark.asyncio
    @patch("app.agents.cloud_agent.sandbox")
    async def test_success(self, mock_sandbox):
        mock_sandbox.create_sandbox = AsyncMock(return_value="sb-99")
        state = {"task_id": "task-99"}
        out = await setup_sandbox_node(state)
        assert out["sandbox_id"] == "sb-99"
        assert out["status"] == "sandbox_ready"

    @pytest.mark.asyncio
    @patch("app.agents.cloud_agent.sandbox")
    async def test_failure(self, mock_sandbox):
        mock_sandbox.create_sandbox = AsyncMock(side_effect=RuntimeError("no docker"))
        state = {"task_id": "task-100"}
        out = await setup_sandbox_node(state)
        assert out["status"] == "failed"
        assert "no docker" in out["error"]


class TestBuildTaskGraph:
    """Verify graph structure compiles correctly."""

    def test_graph_compiles(self):
        graph = build_task_graph()
        assert graph is not None


class TestSystemPrompt:
    def test_system_prompt_content(self):
        assert "execute_command" in SYSTEM_PROMPT
        assert "write_file" in SYSTEM_PROMPT
        assert "read_file" in SYSTEM_PROMPT
        assert "RELATIVE paths" in SYSTEM_PROMPT
