from unittest.mock import AsyncMock, patch

import pytest

from app.agents.cloud_agent import SYSTEM_PROMPT, run_agent


class TestRunAgent:
    @pytest.mark.asyncio
    @patch("app.agents.cloud_agent.create_react_agent")
    @patch("app.agents.cloud_agent.create_agent_llm")
    async def test_successful_execution(self, mock_llm_factory, mock_create_agent):
        from langchain_core.messages import (AIMessage, HumanMessage,
                                             SystemMessage)

        mock_llm = mock_llm_factory.return_value
        mock_agent = AsyncMock()
        mock_create_agent.return_value = mock_agent
        mock_agent.ainvoke.return_value = {
            "messages": [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content="write hello.py"),
                AIMessage(content="", tool_calls=[{"name": "write_file", "args": {"path": "hello.py", "content": "print('hi')"}, "id": "tc1"}]),
                AIMessage(content="Done! Created hello.py successfully."),
            ]
        }

        result = await run_agent("task-1", "write hello.py", "sandbox-1")
        assert result["status"] == "completed"
        assert "Done" in result["result"]

    @pytest.mark.asyncio
    @patch("app.agents.cloud_agent.create_react_agent")
    @patch("app.agents.cloud_agent.create_agent_llm")
    async def test_agent_exception(self, mock_llm_factory, mock_create_agent):
        mock_agent = AsyncMock()
        mock_create_agent.return_value = mock_agent
        mock_agent.ainvoke.side_effect = Exception("LLM API error")

        result = await run_agent("task-2", "do stuff", "sandbox-2")
        assert result["status"] == "failed"
        assert "LLM API error" in result["error"]

    @pytest.mark.asyncio
    @patch("app.agents.cloud_agent.create_react_agent")
    @patch("app.agents.cloud_agent.create_agent_llm")
    async def test_no_final_message(self, mock_llm_factory, mock_create_agent):
        from langchain_core.messages import HumanMessage

        mock_agent = AsyncMock()
        mock_create_agent.return_value = mock_agent
        mock_agent.ainvoke.return_value = {
            "messages": [HumanMessage(content="test")]
        }

        result = await run_agent("task-3", "test", "sandbox-3")
        assert result["status"] == "completed"
        assert "no final summary" in result["result"].lower()

    def test_system_prompt_content(self):
        assert "execute_command" in SYSTEM_PROMPT
        assert "write_file" in SYSTEM_PROMPT
        assert "read_file" in SYSTEM_PROMPT
        assert "RELATIVE paths" in SYSTEM_PROMPT
