"""
Tools available to the agent for execution inside the Docker sandbox.

Each tool is a LangChain-compatible tool that delegates execution to the sandbox container.
The sandbox_id is bound at runtime via closure.
"""

from typing import Callable

from langchain_core.tools import tool


def create_sandbox_tools(exec_fn: Callable, write_fn: Callable, read_fn: Callable, list_fn: Callable) -> list:
    """Create tools bound to a specific sandbox execution context."""

    @tool
    async def execute_command(command: str) -> str:
        """Execute a shell command in the sandbox. Use for running scripts, installing packages, compiling code, etc.
        Returns stdout and stderr. Commands run in /workspace directory."""
        result = await exec_fn(command)
        output = ""
        if result["stdout"]:
            output += result["stdout"]
        if result["stderr"]:
            output += f"\n[STDERR]: {result['stderr']}"
        if result["exit_code"] != 0:
            output += f"\n[EXIT CODE]: {result['exit_code']}"
        return output or "[No output]"

    @tool
    async def write_file(path: str, content: str) -> str:
        """Write content to a file in the workspace.
        Use relative paths like 'hello.py' or 'src/main.py'.
        Creates parent directories automatically."""
        result = await write_fn(path, content)
        if result["exit_code"] == 0:
            return f"File written: {path}"
        return f"Error writing file: {result['stderr']}"

    @tool
    async def read_file(path: str) -> str:
        """Read the contents of a file in the workspace.
        Use relative paths like 'hello.py' or 'src/main.py'."""
        result = await read_fn(path)
        if result["exit_code"] == 0:
            return result["stdout"] or "[Empty file]"
        return f"Error reading file: {result['stderr']}"

    @tool
    async def list_files(path: str = ".") -> str:
        """List all files in a directory within the workspace.
        Use '.' for current directory or relative paths like 'src/'."""
        result = await list_fn(path)
        if result["exit_code"] == 0:
            return result["stdout"] or "[Empty directory]"
        return f"Error listing files: {result['stderr']}"

    return [execute_command, write_file, read_file, list_files]
