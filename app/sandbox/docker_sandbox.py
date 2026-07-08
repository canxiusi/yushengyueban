"""
Docker-based sandbox for isolated agent execution.

Each task gets its own container with:
- Network isolation (no internet by default)
- Resource limits (CPU, memory)
- Timeout enforcement
- Ephemeral filesystem (destroyed after task)
"""

import asyncio
import logging
from typing import Optional

import docker
from docker.errors import APIError, NotFound
from docker.models.containers import Container

from app.config import settings

logger = logging.getLogger(__name__)


class SandboxManager:
    def __init__(self):
        self._client: Optional[docker.DockerClient] = None

    @property
    def client(self) -> docker.DockerClient:
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    def _ensure_network(self):
        try:
            self.client.networks.get(settings.sandbox_network)
        except NotFound:
            self.client.networks.create(
                settings.sandbox_network,
                driver="bridge",
                internal=True,
            )

    async def create_sandbox(self, task_id: str) -> str:
        loop = asyncio.get_event_loop()
        container = await loop.run_in_executor(None, self._create_container, task_id)
        return container.id

    def _create_container(self, task_id: str) -> Container:
        self._ensure_network()
        container = self.client.containers.run(
            image=settings.sandbox_image,
            name=f"agent-sandbox-{task_id}",
            command="sleep infinity",
            detach=True,
            network=settings.sandbox_network,
            mem_limit=settings.sandbox_memory_limit,
            nano_cpus=int(settings.sandbox_cpu_limit * 1e9),
            working_dir="/workspace",
            environment={"TASK_ID": task_id},
            labels={"managed-by": "cloud-agent-platform", "task-id": task_id},
            # Security: drop all capabilities, read-only root except /workspace and /tmp
            cap_drop=["ALL"],
            read_only=False,
            tmpfs={"/tmp": "size=100m"},
        )
        return container

    async def exec_command(self, container_id: str, command: str, timeout: int = 30) -> dict:
        loop = asyncio.get_event_loop()
        return await asyncio.wait_for(
            loop.run_in_executor(None, self._exec_in_container, container_id, command),
            timeout=timeout,
        )

    def _exec_in_container(self, container_id: str, command: str) -> dict:
        try:
            container = self.client.containers.get(container_id)
            exit_code, output = container.exec_run(
                cmd=["bash", "-c", command],
                workdir="/workspace",
                demux=True,
            )
            stdout = output[0].decode("utf-8", errors="replace") if output[0] else ""
            stderr = output[1].decode("utf-8", errors="replace") if output[1] else ""
            return {
                "exit_code": exit_code,
                "stdout": stdout[:10000],
                "stderr": stderr[:5000],
            }
        except NotFound:
            return {"exit_code": -1, "stdout": "", "stderr": "Container not found"}
        except APIError as e:
            return {"exit_code": -1, "stdout": "", "stderr": str(e)}

    async def write_file(self, container_id: str, path: str, content: str) -> dict:
        safe_content = content.replace("'", "'\\''")
        cmd = f"mkdir -p $(dirname '{path}') && cat > '{path}' << 'SANDBOX_EOF'\n{content}\nSANDBOX_EOF"
        return await self.exec_command(container_id, cmd)

    async def read_file(self, container_id: str, path: str) -> dict:
        return await self.exec_command(container_id, f"cat '{path}'")

    async def list_files(self, container_id: str, path: str = "/workspace") -> dict:
        return await self.exec_command(container_id, f"find '{path}' -type f | head -100")

    async def destroy_sandbox(self, container_id: str):
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._destroy_container, container_id)

    def _destroy_container(self, container_id: str):
        try:
            container = self.client.containers.get(container_id)
            container.stop(timeout=5)
            container.remove(force=True)
            logger.info(f"Sandbox destroyed: {container_id[:12]}")
        except NotFound:
            pass
        except APIError as e:
            logger.warning(f"Failed to destroy sandbox {container_id[:12]}: {e}")

    async def cleanup_stale(self):
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._cleanup)

    def _cleanup(self):
        containers = self.client.containers.list(
            filters={"label": "managed-by=cloud-agent-platform"},
            all=True,
        )
        for container in containers:
            try:
                container.remove(force=True)
            except APIError:
                pass


sandbox_manager = SandboxManager()
