from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


MAX_AGENT_ITERATIONS = 200
MAX_CONTEXT_TOKENS = 128000
MAX_OUTPUT_TOKENS = 16000


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'sqlite:///.runtime/doctor.db'
    deepseek_api_key: str = ''
    deepseek_base_url: str = 'https://api.deepseek.com'
    default_diagnosis_model: str = ''
    default_fix_model: str = ''
    max_context_tokens: int = 18000
    max_output_tokens: int = 3000
    max_agent_iterations: int = 12
    max_llm_concurrency: int = 1
    max_diagnosis_queue: int = 100
    diagnosis_cooldown_seconds: int = 900
    warn_threshold: int = 3
    warn_window_seconds: int = 60
    multiline_idle_seconds: float = 3.0
    retention_days: int = 30
    allowed_services: str = 'spring'
    compose_project: str = 'day35-aurelspa'
    runner_socket: str = '/run/doctor/runner.sock'
    runner_token: str = ''
    api_url: str = 'http://127.0.0.1:8050'
    source_repo: str = '/home/slava/ai/aurelSpa-day35'
    workspace_root: str = '/home/slava/ai/ai-doctor-workspaces'
    target_compose_file: str = '/home/slava/ai/aurelSpa-day35/compose.yaml'
    deployment_compose_file: str = ''
    target_health_url: str = 'http://127.0.0.1:8085/health'
    target_smoke_url: str = 'http://127.0.0.1:8085/'
    deployment_health_timeout_seconds: int = 120
    demo_token: str = ''
    target_demo_url: str = 'http://127.0.0.1:8085/doctor-demo/normalize'


@lru_cache
def get_settings() -> Settings:
    return Settings()
