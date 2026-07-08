import os

import pytest

from app.sandbox.local_sandbox import LocalSandbox


@pytest.fixture
async def sandbox():
    sb = LocalSandbox()
    yield sb
    await sb.cleanup_stale()


class TestLocalSandboxCreate:
    @pytest.mark.asyncio
    async def test_create_sandbox(self, sandbox):
        sandbox_id = await sandbox.create_sandbox("test-task-001")
        assert sandbox_id == "test-task-001"
        assert sandbox_id in sandbox._workspaces
        workspace = sandbox._workspaces[sandbox_id]
        assert os.path.isdir(workspace)

    @pytest.mark.asyncio
    async def test_create_multiple_sandboxes(self, sandbox):
        id1 = await sandbox.create_sandbox("task-1")
        id2 = await sandbox.create_sandbox("task-2")
        assert sandbox._workspaces[id1] != sandbox._workspaces[id2]


class TestLocalSandboxExec:
    @pytest.mark.asyncio
    async def test_exec_simple_command(self, sandbox):
        sid = await sandbox.create_sandbox("test-exec")
        result = await sandbox.exec_command(sid, "echo hello")
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]

    @pytest.mark.asyncio
    async def test_exec_command_failure(self, sandbox):
        sid = await sandbox.create_sandbox("test-fail")
        result = await sandbox.exec_command(sid, "ls /nonexistent_path_xyz")
        assert result["exit_code"] != 0
        assert result["stderr"] != ""

    @pytest.mark.asyncio
    async def test_exec_python(self, sandbox):
        sid = await sandbox.create_sandbox("test-python")
        result = await sandbox.exec_command(sid, "python3 -c 'print(1+1)'")
        assert result["exit_code"] == 0
        assert "2" in result["stdout"]

    @pytest.mark.asyncio
    async def test_exec_timeout(self, sandbox):
        sid = await sandbox.create_sandbox("test-timeout")
        result = await sandbox.exec_command(sid, "sleep 10", timeout=1)
        assert result["exit_code"] == -1
        assert "timed out" in result["stderr"].lower()

    @pytest.mark.asyncio
    async def test_exec_invalid_sandbox(self, sandbox):
        result = await sandbox.exec_command("nonexistent-id", "echo hi")
        assert result["exit_code"] == -1
        assert "not found" in result["stderr"].lower()

    @pytest.mark.asyncio
    async def test_exec_cwd_is_workspace(self, sandbox):
        sid = await sandbox.create_sandbox("test-cwd")
        workspace = sandbox._workspaces[sid]
        result = await sandbox.exec_command(sid, "pwd")
        assert result["exit_code"] == 0
        # macOS resolves /var -> /private/var
        assert workspace.replace("/private", "") in result["stdout"].replace("/private", "")


class TestLocalSandboxFileOps:
    @pytest.mark.asyncio
    async def test_write_and_read_file(self, sandbox):
        sid = await sandbox.create_sandbox("test-file")
        write_result = await sandbox.write_file(sid, "test.txt", "hello world")
        assert write_result["exit_code"] == 0

        read_result = await sandbox.read_file(sid, "test.txt")
        assert read_result["exit_code"] == 0
        assert read_result["stdout"] == "hello world"

    @pytest.mark.asyncio
    async def test_write_nested_path(self, sandbox):
        sid = await sandbox.create_sandbox("test-nested")
        result = await sandbox.write_file(sid, "src/main.py", "print('hi')")
        assert result["exit_code"] == 0

        read_result = await sandbox.read_file(sid, "src/main.py")
        assert read_result["stdout"] == "print('hi')"

    @pytest.mark.asyncio
    async def test_write_strips_workspace_prefix(self, sandbox):
        sid = await sandbox.create_sandbox("test-prefix")
        result = await sandbox.write_file(sid, "/workspace/hello.py", "x=1")
        assert result["exit_code"] == 0

        read_result = await sandbox.read_file(sid, "hello.py")
        assert read_result["exit_code"] == 0
        assert read_result["stdout"] == "x=1"

    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self, sandbox):
        sid = await sandbox.create_sandbox("test-nofile")
        result = await sandbox.read_file(sid, "nope.txt")
        assert result["exit_code"] == -1

    @pytest.mark.asyncio
    async def test_list_files(self, sandbox):
        sid = await sandbox.create_sandbox("test-list")
        await sandbox.write_file(sid, "a.py", "1")
        await sandbox.write_file(sid, "b.py", "2")
        await sandbox.write_file(sid, "sub/c.py", "3")

        result = await sandbox.list_files(sid, ".")
        assert result["exit_code"] == 0
        assert "a.py" in result["stdout"]
        assert "b.py" in result["stdout"]
        assert "c.py" in result["stdout"]

    @pytest.mark.asyncio
    async def test_list_files_strips_workspace_prefix(self, sandbox):
        sid = await sandbox.create_sandbox("test-list-prefix")
        await sandbox.write_file(sid, "file.txt", "data")

        result = await sandbox.list_files(sid, "/workspace")
        assert result["exit_code"] == 0
        assert "file.txt" in result["stdout"]


class TestLocalSandboxDestroy:
    @pytest.mark.asyncio
    async def test_destroy_sandbox(self, sandbox):
        sid = await sandbox.create_sandbox("test-destroy")
        workspace = sandbox._workspaces[sid]
        assert os.path.exists(workspace)

        await sandbox.destroy_sandbox(sid)
        assert not os.path.exists(workspace)
        assert sid not in sandbox._workspaces

    @pytest.mark.asyncio
    async def test_destroy_nonexistent(self, sandbox):
        # Should not raise
        await sandbox.destroy_sandbox("no-such-id")

    @pytest.mark.asyncio
    async def test_cleanup_stale(self, sandbox):
        await sandbox.create_sandbox("s1")
        await sandbox.create_sandbox("s2")
        assert len(sandbox._workspaces) == 2

        await sandbox.cleanup_stale()
        assert len(sandbox._workspaces) == 0
