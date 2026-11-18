from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    mysql_host: str
    mysql_port: int
    mysql_user: str
    mysql_password: str
    mysql_database: str
    google_spreadsheet_id: str
    gcp_project_id: str
    bigquery_dataset: str
    telegram_bot_token: str
    telegram_chat_id: str
    telegram_call_user: str
    sleep_start_hour: int
    sleep_end_hour: int
    alert_min_score: int
    alert_cooldown_hours: int
    alert_extra_drop_rs: float


def load_settings() -> Settings:
    """Recarrega variáveis do arquivo .env dinamicamente a cada chamada crítica."""
    load_dotenv(BASE_DIR / ".env", override=True)
    raw_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip().strip('"').strip("'")
    raw_chat = os.getenv("TELEGRAM_CHAT_ID", "").strip().strip('"').strip("'")
    raw_call_user = os.getenv("TELEGRAM_CALL_USER", "").strip().strip('"').strip("'")
    return Settings(
        mysql_host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        mysql_port=int(os.getenv("MYSQL_PORT", "3306")),
        mysql_user=os.getenv("MYSQL_USER", "root"),
        mysql_password=os.getenv("MYSQL_PASSWORD", ""),
        mysql_database=os.getenv("MYSQL_DATABASE", "monitoramento_precos"),
        google_spreadsheet_id=os.getenv("GOOGLE_SPREADSHEET_ID", "").strip(),
        gcp_project_id=os.getenv("GCP_PROJECT_ID", "").strip(),
        bigquery_dataset=os.getenv("BIGQUERY_DATASET", "radar_precos").strip(),
        telegram_bot_token=raw_token,
        telegram_chat_id=raw_chat,
        telegram_call_user=raw_call_user,
        sleep_start_hour=int(os.getenv("SLEEP_START_HOUR", "2")),
        sleep_end_hour=int(os.getenv("SLEEP_END_HOUR", "6")),
        alert_min_score=int(os.getenv("ALERT_MIN_SCORE", "70")),
        alert_cooldown_hours=int(os.getenv("ALERT_COOLDOWN_HOURS", "12")),
        alert_extra_drop_rs=float(os.getenv("ALERT_EXTRA_DROP_RS", "30.0")),
    )


# Compatibilidade com imports diretos
settings = load_settings()
