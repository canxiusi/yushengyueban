from unittest.mock import AsyncMock

import pytest

from app.agents.tools import create_sandbox_tools


@pytest.fixture
def mock_sandbox():
    return {
        "exec_fn": AsyncMock(return_value={"exit_code": 0, "stdout": "output", "stderr": ""}),
        "write_fn": AsyncMock(return_value={"exit_code": 0, "stdout": "Written", "stderr": ""}),
        "read_fn": AsyncMock(return_value={"exit_code": 0, "stdout": "file content", "stderr": ""}),
        "list_fn": AsyncMock(return_value={"exit_code": 0, "stdout": "a.py\nb.py", "stderr": ""}),
    }


@pytest.fixture
def tools(mock_sandbox):
    return create_sandbox_tools(
        mock_sandbox["exec_fn"],
        mock_sandbox["write_fn"],
        mock_sandbox["read_fn"],
        mock_sandbox["list_fn"],
    )


class TestExecuteCommandTool:
    @pytest.mark.asyncio
    async def test_success(self, tools, mock_sandbox):
        tool = next(t for t in tools if t.name == "execute_command")
        result = await tool.ainvoke({"command": "echo hi"})
        assert "output" in result
        mock_sandbox["exec_fn"].assert_called_once_with("echo hi")

    @pytest.mark.asyncio
    async def test_with_stderr(self, tools, mock_sandbox):
        mock_sandbox["exec_fn"].return_value = {
            "exit_code": 1, "stdout": "", "stderr": "error msg"
        }
        tool = next(t for t in tools if t.name == "execute_command")
        result = await tool.ainvoke({"command": "bad_cmd"})
        assert "[STDERR]: error msg" in result
        assert "[EXIT CODE]: 1" in result

    @pytest.mark.asyncio
    async def test_no_output(self, tools, mock_sandbox):
        mock_sandbox["exec_fn"].return_value = {"exit_code": 0, "stdout": "", "stderr": ""}
        tool = next(t for t in tools if t.name == "execute_command")
        result = await tool.ainvoke({"command": "true"})
        assert result == "[No output]"


class TestWriteFileTool:
    @pytest.mark.asyncio
    async def test_write_success(self, tools, mock_sandbox):
        tool = next(t for t in tools if t.name == "write_file")
        result = await tool.ainvoke({"path": "hello.py", "content": "print(1)"})
        assert "File written" in result
        mock_sandbox["write_fn"].assert_called_once_with("hello.py", "print(1)")

    @pytest.mark.asyncio
    async def test_write_error(self, tools, mock_sandbox):
        mock_sandbox["write_fn"].return_value = {"exit_code": -1, "stdout": "", "stderr": "Permission denied"}
        tool = next(t for t in tools if t.name == "write_file")
        result = await tool.ainvoke({"path": "x.py", "content": ""})
        assert "Error writing file" in result


class TestReadFileTool:
    @pytest.mark.asyncio
    async def test_read_success(self, tools, mock_sandbox):
        tool = next(t for t in tools if t.name == "read_file")
        result = await tool.ainvoke({"path": "test.py"})
        assert result == "file content"

    @pytest.mark.asyncio
    async def test_read_empty_file(self, tools, mock_sandbox):
        mock_sandbox["read_fn"].return_value = {"exit_code": 0, "stdout": "", "stderr": ""}
        tool = next(t for t in tools if t.name == "read_file")
        result = await tool.ainvoke({"path": "empty.txt"})
        assert result == "[Empty file]"

    @pytest.mark.asyncio
    async def test_read_not_found(self, tools, mock_sandbox):
        mock_sandbox["read_fn"].return_value = {"exit_code": -1, "stdout": "", "stderr": "No such file"}
        tool = next(t for t in tools if t.name == "read_file")
        result = await tool.ainvoke({"path": "nope.txt"})
        assert "Error reading file" in result


class TestListFilesTool:
    @pytest.mark.asyncio
    async def test_list_success(self, tools, mock_sandbox):
        tool = next(t for t in tools if t.name == "list_files")
        result = await tool.ainvoke({"path": "."})
        assert "a.py" in result
        assert "b.py" in result

    @pytest.mark.asyncio
    async def test_list_empty(self, tools, mock_sandbox):
        mock_sandbox["list_fn"].return_value = {"exit_code": 0, "stdout": "", "stderr": ""}
        tool = next(t for t in tools if t.name == "list_files")
        result = await tool.ainvoke({"path": "."})
        assert result == "[Empty directory]"
