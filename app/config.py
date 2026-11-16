from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    app_name: str = "РечьКонтроль"
    app_host: str = "127.0.0.1"
    app_port: int = 8080
    app_debug: bool = False

    admin_username: str = "admin"
    admin_password: str = "CenterInvest2026!"

    data_dir: Path = DATA_DIR
    whisper_model: str = "base"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    max_parallel_jobs: int = 1
    max_upload_mb: int = 80
    max_upload_files: int = 20
    max_duration_sec: int = 900
    jwt_ttl_hours: int = 12
    allowed_audio_ext: tuple[str, ...] = (".wav", ".mp3", ".ogg", ".oga", ".flac", ".m4a")

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def key_path(self) -> Path:
        return self.data_dir / "master.key"

    @property
    def jwt_secret_path(self) -> Path:
        return self.data_dir / "jwt.secret"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"


settings = Settings()
