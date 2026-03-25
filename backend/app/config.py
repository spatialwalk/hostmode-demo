from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv


load_dotenv()


def _to_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    server_host: str
    server_port: int
    cors_allow_origins: list[str]
    public_environment: str
    avatar_app_id: str
    avatar_api_key: str
    avatar_id: str
    avatar_console_endpoint: str
    avatar_ingress_endpoint: str
    avatar_output_sample_rate: int
    user_input_sample_rate: int
    doubao_app_id: str
    doubao_access_token: str
    doubao_ws_host: str
    doubao_ws_path: str
    doubao_resource_id: str
    doubao_app_key: str
    doubao_speaker: str
    doubao_bot_name: str
    doubao_system_role: str
    doubao_speaking_style: str
    doubao_model: str
    doubao_end_smooth_window_ms: int
    doubao_enable_web_search: bool
    doubao_enable_music: bool

    @property
    def public_avatar_config(self) -> dict[str, object]:
        return {
            "appId": self.avatar_app_id,
            "avatarId": self.avatar_id,
            "environment": self.public_environment,
            "outputSampleRate": self.avatar_output_sample_rate,
            "inputSampleRate": self.user_input_sample_rate,
        }


def _split_origins(raw: str | None) -> list[str]:
    if not raw:
        return ["http://127.0.0.1:5173", "http://localhost:5173"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    public_environment = os.getenv("SPATIALREAL_PUBLIC_ENV", "cn").strip() or "cn"
    return Settings(
        server_host=os.getenv("HOSTMODE_SERVER_HOST", "127.0.0.1"),
        server_port=int(os.getenv("HOSTMODE_SERVER_PORT", "8765")),
        cors_allow_origins=_split_origins(os.getenv("HOSTMODE_CORS_ALLOW_ORIGINS")),
        public_environment=public_environment,
        avatar_app_id=os.getenv("SPATIALREAL_AVATAR_APP_ID", "").strip(),
        avatar_api_key=os.getenv("SPATIALREAL_AVATAR_API_KEY", "").strip(),
        avatar_id=os.getenv("SPATIALREAL_AVATAR_ID", "").strip(),
        avatar_console_endpoint=os.getenv(
            "SPATIALREAL_AVATAR_CONSOLE_ENDPOINT",
            "",
        ).strip(),
        avatar_ingress_endpoint=os.getenv(
            "SPATIALREAL_AVATAR_INGRESS_ENDPOINT",
            "",
        ).strip(),
        avatar_output_sample_rate=int(
            os.getenv("SPATIALREAL_AVATAR_OUTPUT_SAMPLE_RATE", "24000")
        ),
        user_input_sample_rate=int(
            os.getenv("HOSTMODE_USER_INPUT_SAMPLE_RATE", "16000")
        ),
        doubao_app_id=os.getenv("DOUBAO_E2E_APP_ID", "").strip(),
        doubao_access_token=os.getenv("DOUBAO_E2E_ACCESS_TOKEN", "").strip(),
        doubao_ws_host=os.getenv("DOUBAO_E2E_WS_HOST", "openspeech.bytedance.com"),
        doubao_ws_path=os.getenv("DOUBAO_E2E_WS_PATH", "/api/v3/realtime/dialogue"),
        doubao_resource_id=os.getenv(
            "DOUBAO_E2E_RESOURCE_ID",
            "volc.speech.dialog",
        ).strip(),
        doubao_app_key=os.getenv("DOUBAO_E2E_WS_APP_KEY", "PlgvMymc7f3tQnJ6").strip(),
        doubao_speaker=os.getenv(
            "DOUBAO_E2E_SPEAKER",
            "zh_female_vv_jupiter_bigtts",
        ).strip(),
        doubao_bot_name=os.getenv("DOUBAO_E2E_BOT_NAME", "SpatialReal Host").strip(),
        doubao_system_role=os.getenv(
            "DOUBAO_E2E_SYSTEM_ROLE",
            "You are a concise and friendly voice assistant.",
        ).strip(),
        doubao_speaking_style=os.getenv(
            "DOUBAO_E2E_SPEAKING_STYLE",
            "Clear, concise, and steady-paced.",
        ).strip(),
        doubao_model=os.getenv("DOUBAO_E2E_MODEL", "O").strip(),
        doubao_end_smooth_window_ms=int(
            os.getenv("DOUBAO_E2E_END_SMOOTH_WINDOW_MS", "1500")
        ),
        doubao_enable_web_search=_to_bool(
            os.getenv("DOUBAO_E2E_ENABLE_WEB_SEARCH"),
        ),
        doubao_enable_music=_to_bool(os.getenv("DOUBAO_E2E_ENABLE_MUSIC")),
    )


def validate_settings(settings: Settings) -> list[str]:
    required = {
        "SPATIALREAL_AVATAR_APP_ID": settings.avatar_app_id,
        "SPATIALREAL_AVATAR_API_KEY": settings.avatar_api_key,
        "SPATIALREAL_AVATAR_ID": settings.avatar_id,
        "SPATIALREAL_AVATAR_CONSOLE_ENDPOINT": settings.avatar_console_endpoint,
        "SPATIALREAL_AVATAR_INGRESS_ENDPOINT": settings.avatar_ingress_endpoint,
        "DOUBAO_E2E_APP_ID": settings.doubao_app_id,
        "DOUBAO_E2E_ACCESS_TOKEN": settings.doubao_access_token,
    }
    return [name for name, value in required.items() if not value]
