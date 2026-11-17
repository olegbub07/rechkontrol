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
    # Пусто: пароль администратора генерируется при первом запуске.
    admin_password: str = ""
    # Адреса reverse-proxy через запятую. Только от них принимается X-Forwarded-For.
    trusted_proxies: str = ""

    data_dir: Path = DATA_DIR
    # Пусто: SQLite в data/app.db. Например postgresql+psycopg://user:pass@127.0.0.1:5432/rechkontrol
    database_url: str = ""
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "float32"
    max_parallel_jobs: int = 1
    max_upload_mb: int = 80
    max_upload_files: int = 20
    max_duration_sec: int = 900
    jwt_ttl_hours: int = 12
    allow_remote: bool = False
    # Маскирование ПДн в выдаче и экспорте. Исходный текст видит только администратор по явному запросу.
    pii_masking: bool = True
    # Озвучка демо-звонков (Silero TTS, нужен torch из requirements-voice.txt). По умолчанию выключена:
    # без неё программа при старте ничего не скачивает.
    demo_voice_enabled: bool = False
    demo_voice_model_url: str = "https://models.silero.ai/models/tts/ru/v4_ru.pt"
    # SHA-256 файла v4_ru.pt, снят 17.11.2026. Файл с другим хешем не загружается.
    demo_voice_model_sha256: str = "896ab96347d5bd781ab97959d4fd6885620e5aab52405d3445626eb7c1414b00"
    # Стерео-записи: роли по громкости каналов (auto | left | right - канал оператора).
    stereo_diarization: bool = True
    stereo_operator_channel: str = "auto"
    # Необязательная оценка локальной LLM: none | ollama. При ошибке - только правила.
    llm_provider: str = "none"
    llm_model: str = "qwen2.5:7b-instruct"
    ollama_url: str = "http://127.0.0.1:11434"
    llm_timeout_sec: float = 120.0
    llm_weight: float = 0.5
    # Разрешить нелокальный OLLAMA_URL (текст звонка уйдёт с машины). По умолчанию запрещено.
    llm_allow_remote: bool = False
    whisper_beam_size: int = 5
    whisper_batch_size: int = 8
    whisper_cpu_threads: int = 0
    # 152-ФЗ. Мастер-ключ шифрования аудио и расшифровок. Порядок поиска:
    # MASTER_KEY (сам ключ, например из секрета Docker) > MASTER_KEY_FILE (путь к файлу) > data/master.key (старые установки).
    # Ключ лучше держать вне data/: тогда копия data/ или бэкап базы без ключа не читается.
    master_key: str = ""
    master_key_file: str = ""
    # Шифровать текст расшифровок и цитаты в оценке в базе (Fernet, тот же ключ, что у аудио).
    encrypt_transcripts: bool = True
    # Срок хранения звонка (аудио, расшифровка, оценка) в днях от загрузки. 0 - не удалять автоматически.
    retention_days: int = 365
    # Как часто проверять просроченные звонки, в часах.
    retention_check_hours: float = 6.0
    # Часовой пояс для показа времени в журнале аудита (в базе время хранится в UTC).
    display_tz: str = "Europe/Moscow"
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
        if self.master_key_file.strip():
            return Path(self.master_key_file.strip()).expanduser()
        return self.legacy_key_path

    @property
    def legacy_key_path(self) -> Path:
        """Старое место ключа: рядом с данными. Читается для совместимости и переносится."""
        return self.data_dir / "master.key"

    @property
    def key_moved_marker(self) -> Path:
        """Метка «ключ перенесён». Без неё при потере MASTER_KEY_FILE создался бы новый ключ и старые данные не читались бы."""
        return self.data_dir / "master.key.moved"

    @property
    def jwt_secret_path(self) -> Path:
        return self.data_dir / "jwt.secret"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def dataset_dir(self) -> Path:
        return self.data_dir / "datasets" / "minds14_ru"


settings = Settings()
