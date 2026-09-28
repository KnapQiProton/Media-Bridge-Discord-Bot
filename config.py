"""
Configuration loader for Televid Streamer Bot.
Loads environment variables from .env file.
"""

import os
import sys
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

class Config:
    """Konfigurasi utama Televid Bot (Direct Telegram Streamer)."""

    # 1. Discord Bot Credentials
    DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "").strip()
    DISCORD_GUILD_ID_RAW: str = os.getenv("DISCORD_GUILD_ID", "").strip()
    DISCORD_GUILD_ID: Optional[int] = int(DISCORD_GUILD_ID_RAW) if DISCORD_GUILD_ID_RAW.isdigit() else None

    # 2. Telegram API Credentials (dari my.telegram.org)
    TG_API_ID_RAW: str = os.getenv("TG_API_ID", "").strip()
    TG_API_ID: int = int(TG_API_ID_RAW) if TG_API_ID_RAW.isdigit() else 0
    TG_API_HASH: str = os.getenv("TG_API_HASH", "").strip()

    # 3. Telegram Bot Token (dari @BotFather) atau User Session String
    TG_BOT_TOKEN: str = os.getenv("TG_BOT_TOKEN", "").strip()
    TG_SESSION_STRING: str = os.getenv("TG_SESSION_STRING", "").strip()

    # 4. Web Stream Server Configuration
    WEB_HOST: str = os.getenv("WEB_HOST", "0.0.0.0").strip()
    WEB_PORT_RAW: str = os.getenv("WEB_PORT", os.getenv("PORT", "8080")).strip()
    WEB_PORT: int = int(WEB_PORT_RAW) if WEB_PORT_RAW.isdigit() else 8080

    # 5. Public Streaming Base URL
    # Otomatis mendeteksi domain publik Railway jika dideploy di Railway
    _custom_url = os.getenv("STREAM_BASE_URL", "").strip().rstrip("/")
    _railway_domain = (os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip() or os.getenv("RAILWAY_STATIC_URL", "").strip())
    
    if _custom_url:
        STREAM_BASE_URL = _custom_url
    elif _railway_domain:
        STREAM_BASE_URL = f"https://{_railway_domain}" if not _railway_domain.startswith("http") else _railway_domain.rstrip("/")
    else:
        STREAM_BASE_URL = ""

    @classmethod
    def validate(cls) -> None:
        """Validasi variabel penting sebelum bot dijalankan."""
        errors = []
        if not cls.DISCORD_TOKEN:
            errors.append("DISCORD_TOKEN wajib diisi di .env")
        if not cls.TG_API_ID or not cls.TG_API_HASH:
            errors.append("TG_API_ID dan TG_API_HASH wajib diisi di .env (dari my.telegram.org)")
        if not cls.TG_BOT_TOKEN and not cls.TG_SESSION_STRING:
            errors.append("TG_BOT_TOKEN wajib diisi di .env (dibuat via @BotFather di Telegram)")

        if errors:
            print("❌ Konfigurasi belum lengkap:", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
            sys.exit(1)
