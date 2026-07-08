from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    service_host: str = "0.0.0.0"
    service_port: int = 8000
    environment: str = "development"
    log_level: str = "INFO"

    database_url: str = "sqlite+aiosqlite:///./agent_platform.db"
    redis_url: str = "redis://localhost:6379/0"

    llm_provider: str = "openai"
    llm_model: str = "gpt-4o"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_temperature: float = 0.1
    llm_max_tokens: int = 4096

    sandbox_mode: str = "local"  # "local" for subprocess, "docker" for container isolation
    sandbox_image: str = "python:3.12-slim"
    sandbox_network: str = "agent-sandbox-net"
    sandbox_timeout: int = 300
    sandbox_memory_limit: str = "512m"
    sandbox_cpu_limit: float = 1.0

    max_concurrent_tasks: int = 10
    task_timeout: int = 600

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
