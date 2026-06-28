from dataclasses import dataclass


@dataclass
class AppConfig:
    env: str = "dev"
    step_limit: int = 2
    default_model: str = ""
    default_provider: str = ""
    enable_telemetry: bool = False
