"""
Local process-based sandbox for development/testing without Docker.

Uses subprocess to execute commands in a temporary directory.
NOT suitable for production (no real isolation), but allows full agent loop testing.
"""

import asyncio
import logging
import os
import shutil
import tempfile

logger = logging.getLogger(__name__)


class LocalSandbox:
    def __init__(self):
        self._workspaces: dict[str, str] = {}

    async def create_sandbox(self, task_id: str) -> str:
        workspace = tempfile.mkdtemp(prefix=f"agent-{task_id[:8]}-")
        self._workspaces[task_id] = workspace
        logger.info(f"Local sandbox created: {workspace}")
        return task_id

    async def exec_command(self, sandbox_id: str, command: str, timeout: int = 30) -> dict:
        workspace = self._workspaces.get(sandbox_id)
        if not workspace:
            return {"exit_code": -1, "stdout": "", "stderr": "Sandbox not found"}

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=workspace,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "HOME": workspace, "TASK_ID": sandbox_id},
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return {
                "exit_code": proc.returncode,
                "stdout": stdout.decode("utf-8", errors="replace")[:10000],
                "stderr": stderr.decode("utf-8", errors="replace")[:5000],
            }
        except asyncio.TimeoutError:
            proc.kill()
            return {"exit_code": -1, "stdout": "", "stderr": "Command timed out"}
        except Exception as e:
            return {"exit_code": -1, "stdout": "", "stderr": str(e)}

    async def write_file(self, sandbox_id: str, path: str, content: str) -> dict:
        workspace = self._workspaces.get(sandbox_id)
        if not workspace:
            return {"exit_code": -1, "stdout": "", "stderr": "Sandbox not found"}

        # Normalize: strip /workspace prefix since our workspace IS the root
        path = path.removeprefix("/workspace").lstrip("/")
        full_path = os.path.join(workspace, path) if path else workspace

        try:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w") as f:
                f.write(content)
            return {"exit_code": 0, "stdout": f"Written to {path}", "stderr": ""}
        except Exception as e:
            return {"exit_code": -1, "stdout": "", "stderr": str(e)}

    async def read_file(self, sandbox_id: str, path: str) -> dict:
        workspace = self._workspaces.get(sandbox_id)
        if not workspace:
            return {"exit_code": -1, "stdout": "", "stderr": "Sandbox not found"}

        path = path.removeprefix("/workspace").lstrip("/")
        full_path = os.path.join(workspace, path) if path else workspace

        try:
            with open(full_path, "r") as f:
                content = f.read()
            return {"exit_code": 0, "stdout": content[:10000], "stderr": ""}
        except Exception as e:
            return {"exit_code": -1, "stdout": "", "stderr": str(e)}

    async def list_files(self, sandbox_id: str, path: str = ".") -> dict:
        workspace = self._workspaces.get(sandbox_id)
        if not workspace:
            return {"exit_code": -1, "stdout": "", "stderr": "Sandbox not found"}

        path = path.removeprefix("/workspace").lstrip("/") or "."
        target = os.path.join(workspace, path) if path != "." else workspace
        try:
            files = []
            for root, dirs, filenames in os.walk(target):
                for fn in filenames:
                    rel = os.path.relpath(os.path.join(root, fn), workspace)
                    files.append(rel)
            return {"exit_code": 0, "stdout": "\n".join(files[:100]), "stderr": ""}
        except Exception as e:
            return {"exit_code": -1, "stdout": "", "stderr": str(e)}

    async def destroy_sandbox(self, sandbox_id: str):
        workspace = self._workspaces.pop(sandbox_id, None)
        if workspace and os.path.exists(workspace):
            shutil.rmtree(workspace, ignore_errors=True)
            logger.info(f"Local sandbox destroyed: {workspace}")

    async def cleanup_stale(self):
        for task_id in list(self._workspaces.keys()):
            await self.destroy_sandbox(task_id)


local_sandbox = LocalSandbox()
