"""
Configuration loader for Televid Discord Bot.
Loads environment variables from .env file.
"""

import os
import sys
from typing import Optional
from dotenv import load_dotenv

# Muat variabel dari .env
load_dotenv()

class Config:
    """Konfigurasi utama aplikasi bot televid."""

    # Discord Bot Credentials
    DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "").strip()
    DISCORD_GUILD_ID_RAW: str = os.getenv("DISCORD_GUILD_ID", "").strip()
    DISCORD_GUILD_ID: Optional[int] = int(DISCORD_GUILD_ID_RAW) if DISCORD_GUILD_ID_RAW.isdigit() else None

    # Teldrive Configuration
    TELDRIVE_API_HOST: str = os.getenv("TELDRIVE_API_HOST", "").strip().rstrip("/")
    TELDRIVE_ACCESS_TOKEN: str = os.getenv("TELDRIVE_ACCESS_TOKEN", "").strip()
    TELDRIVE_CHANNEL_ID_RAW: str = os.getenv("TELDRIVE_CHANNEL_ID", "").strip()
    TELDRIVE_CHANNEL_ID: Optional[int] = (
        int(TELDRIVE_CHANNEL_ID_RAW) if TELDRIVE_CHANNEL_ID_RAW.lstrip("-").isdigit() else None
    )

    # Optional: Direct PostgreSQL Connection to Teldrive Database
    # Sangat direkomendasikan untuk query instan O(1) by message_id
    DATABASE_URL: Optional[str] = os.getenv("DATABASE_URL", "").strip() or None

    # Custom public streaming URL template (opsional jika menggunakan Reverse Proxy / CDN)
    # Contoh: "{host}/api/files/{file_id}/content" atau "{host}/stream/{file_id}/{filename}"
    PUBLIC_STREAM_URL_TEMPLATE: str = os.getenv(
        "PUBLIC_STREAM_URL_TEMPLATE",
        "{host}/api/files/{file_id}/content"
    ).strip()

    @classmethod
    def validate(cls) -> None:
        """Validasi variabel penting sebelum bot dijalankan."""
        errors = []
        if not cls.DISCORD_TOKEN:
            errors.append("DISCORD_TOKEN wajib diisi di .env")
        if not cls.TELDRIVE_API_HOST and not cls.DATABASE_URL:
            errors.append("TELDRIVE_API_HOST atau DATABASE_URL wajib diisi di .env")
        
        if errors:
            print("❌ Konfigurasi tidak lengkap:", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
            sys.exit(1)
