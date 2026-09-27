"""
Teldrive API and Database Client.
Menangani query metadata file Teldrive berdasarkan Telegram message_id
dan meng-generate direct URL untuk streaming.
"""

import logging
import json
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
import aiohttp

try:
    import asyncpg
    HAS_ASYNCPG = True
except ImportError:
    HAS_ASYNCPG = False

from config import Config

logger = logging.getLogger("televid.teldrive")


class TeldriveError(Exception):
    """Base exception untuk Teldrive client."""
    pass


class TeldriveOfflineError(TeldriveError):
    """Dilempar saat Teldrive tidak dapat dihubungi atau server error."""
    pass


class FileNotFoundInTeldriveError(TeldriveError):
    """Dilempar saat file dengan message_id tersebut belum terdaftar di Teldrive."""
    pass


class TeldriveAuthError(TeldriveError):
    """Dilempar saat kredensial/token Teldrive tidak valid (401/403)."""
    pass


@dataclass
class TeldriveFile:
    id: str
    name: str
    size: int
    mime_type: str
    channel_id: Optional[int] = None
    parts: Optional[List[Dict[str, Any]]] = None

    @property
    def formatted_size(self) -> str:
        """Mengubah ukuran byte menjadi format human-readable (MB, GB, dll)."""
        bytes_val = float(self.size)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if bytes_val < 1024.0:
                return f"{bytes_val:.2f} {unit}"
            bytes_val /= 1024.0
        return f"{bytes_val:.2f} PB"


class TeldriveClient:
    """Client untuk berinteraksi dengan Teldrive via Database Postgres atau REST API."""

    def __init__(self):
        self.api_host = Config.TELDRIVE_API_HOST
        self.access_token = Config.TELDRIVE_ACCESS_TOKEN
        self.db_url = Config.DATABASE_URL
        self._db_pool: Optional[Any] = None

    async def init(self) -> None:
        """Inisialisasi koneksi pool database jika DATABASE_URL tersedia."""
        if self.db_url and HAS_ASYNCPG:
            try:
                self._db_pool = await asyncpg.create_pool(
                    self.db_url,
                    min_size=1,
                    max_size=5,
                    command_timeout=10
                )
                logger.info("✅ Terhubung ke database PostgreSQL Teldrive.")
            except Exception as e:
                logger.warning(
                    f"⚠️ Gagal menghubungkan ke PostgreSQL ({e}). "
                    "Fallback ke REST API Teldrive."
                )
                self._db_pool = None
        elif self.db_url and not HAS_ASYNCPG:
            logger.warning("⚠️ DATABASE_URL diisi tetapi 'asyncpg' belum terinstall. Menggunakan HTTP API.")

    async def close(self) -> None:
        """Tutup koneksi database pool."""
        if self._db_pool:
            await self._db_pool.close()
            self._db_pool = None

    async def get_file_by_message_id(
        self,
        message_id: int,
        channel_id: Optional[int] = None
    ) -> TeldriveFile:
        """
        Cari file di Teldrive berdasarkan Telegram message_id.
        Mencoba lewat Direct PostgreSQL terlebih dahulu (jika aktif),
        kemudian fallback ke Teldrive REST API.
        """
        if self._db_pool:
            try:
                return await self._query_from_postgres(message_id, channel_id)
            except FileNotFoundInTeldriveError:
                # Coba juga via REST API barangkali ada file baru yang belum commit
                logger.debug("File tidak ditemukan di DB, mencoba fallback REST API...")
            except Exception as e:
                logger.error(f"Error query database: {e}. Fallback ke REST API.")

        # Gunakan REST API
        return await self._query_from_api(message_id, channel_id)

    async def _query_from_postgres(
        self,
        message_id: int,
        channel_id: Optional[int] = None
    ) -> TeldriveFile:
        """
        Query langsung ke tabel 'files' PostgreSQL Teldrive.
        Teldrive menyimpan parts dalam format JSONB: [{'id': message_id, ...}]
        """
        if not self._db_pool:
            raise TeldriveOfflineError("Teldrive sedang down, coba lagi nanti")

        msg_id_str = str(message_id)
        # Query toleran terhadap variasi schema part jsonb / text
        sql = """
            SELECT id, name, size, mime_type, channel_id, parts
            FROM files
            WHERE (
                parts::text LIKE $1
                OR parts::text LIKE $2
            )
            ORDER BY created_at DESC
            LIMIT 1;
        """
        pattern1 = f'%"id":{msg_id_str}%'
        pattern2 = f'%"id": {msg_id_str}%'

        try:
            async with self._db_pool.acquire() as conn:
                row = await conn.fetchrow(sql, pattern1, pattern2)
                if not row:
                    raise FileNotFoundInTeldriveError(
                        "Video belum ada di Teldrive, upload dulu via Teldrive UI"
                    )

                parts_raw = row["parts"]
                parts_data = json.loads(parts_raw) if isinstance(parts_raw, str) else parts_raw

                return TeldriveFile(
                    id=str(row["id"]),
                    name=row["name"],
                    size=int(row["size"]),
                    mime_type=row.get("mime_type") or "video/mp4",
                    channel_id=row.get("channel_id"),
                    parts=parts_data if isinstance(parts_data, list) else None
                )
        except FileNotFoundInTeldriveError:
            raise
        except Exception as e:
            logger.error(f"Postgres query exception: {e}")
            raise TeldriveOfflineError("Teldrive sedang down, coba lagi nanti") from e

    async def _query_from_api(
        self,
        message_id: int,
        channel_id: Optional[int] = None
    ) -> TeldriveFile:
        """
        Query ke REST API Teldrive (GET /api/files).
        Melakukan traversal file list dan mencari file yang memiliki part dengan message_id terkait.
        """
        if not self.api_host:
            raise TeldriveOfflineError("Teldrive sedang down, coba lagi nanti")

        headers = {
            "Accept": "application/json",
            "User-Agent": "Televid-Discord-Bot/1.0"
        }
        cookies = {}
        if self.access_token:
            # Teldrive menggunakan cookie user-session atau Authorization header
            cookies["user-session"] = self.access_token
            headers["Authorization"] = f"Bearer {self.access_token}"

        timeout = aiohttp.ClientTimeout(total=15)

        try:
            async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=timeout) as session:
                # 1. Coba endpoint langsung jika ada query filter khusus
                # (misal: /api/files?telegram_message_id=... atau /api/files/find?...)
                url_direct = f"{self.api_host}/api/files"
                params = {"telegram_message_id": str(message_id)}

                async with session.get(url_direct, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        file_found = self._extract_file_from_response(data, message_id)
                        if file_found:
                            return file_found
                    elif resp.status in (401, 403):
                        logger.error("Akses Teldrive API ditolak (401/403).")
                        raise TeldriveAuthError("Autentikasi Teldrive gagal, periksa TELDRIVE_ACCESS_TOKEN")
                    elif resp.status >= 500:
                        logger.error(f"Teldrive server error: {resp.status}")
                        raise TeldriveOfflineError("Teldrive sedang down, coba lagi nanti")

                # 2. Jika endpoint dengan param telegram_message_id tidak langsung memfilter,
                # minta file list dari root /api/files?path=/
                list_params = {"path": "/"}
                async with session.get(url_direct, params=list_params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        file_found = self._extract_file_from_response(data, message_id)
                        if file_found:
                            return file_found
                    elif resp.status in (401, 403):
                        raise TeldriveAuthError("Autentikasi Teldrive gagal, periksa TELDRIVE_ACCESS_TOKEN")
                    elif resp.status >= 500:
                        raise TeldriveOfflineError("Teldrive sedang down, coba lagi nanti")

                # Jika tetap tidak ditemukan
                raise FileNotFoundInTeldriveError(
                    "Video belum ada di Teldrive, upload dulu via Teldrive UI"
                )

        except (aiohttp.ClientConnectorError, aiohttp.ServerTimeoutError, TimeoutError) as e:
            logger.error(f"Koneksi ke Teldrive API gagal: {e}")
            raise TeldriveOfflineError("Teldrive sedang down, coba lagi nanti") from e
        except (FileNotFoundInTeldriveError, TeldriveAuthError, TeldriveOfflineError):
            raise
        except Exception as e:
            logger.error(f"Unexpected error saat menghubungi Teldrive: {e}")
            raise TeldriveOfflineError("Teldrive sedang down, coba lagi nanti") from e

    def _extract_file_from_response(self, data: Any, target_message_id: int) -> Optional[TeldriveFile]:
        """Helper untuk mencari file dari JSON response Teldrive."""
        files_list = []
        if isinstance(data, list):
            files_list = data
        elif isinstance(data, dict):
            # Format bisa berupa {"files": [...]} atau {"results": [...]} atau single file object
            if "files" in data and isinstance(data["files"], list):
                files_list = data["files"]
            elif "results" in data and isinstance(data["results"], list):
                files_list = data["results"]
            elif "id" in data:
                files_list = [data]

        for item in files_list:
            if not isinstance(item, dict):
                continue

            file_id = str(item.get("id", ""))
            name = item.get("name", "video.mp4")
            size = int(item.get("size", 0))
            mime = item.get("mimeType") or item.get("mime_type") or "video/mp4"
            channel_id = item.get("channelId") or item.get("channel_id")
            parts = item.get("parts", [])

            # Cek apakah message_id cocok dengan salah satu part
            match = False
            if isinstance(parts, list):
                for p in parts:
                    if isinstance(p, dict) and p.get("id") == target_message_id:
                        match = True
                        break
                    elif isinstance(p, (int, str)) and str(p) == str(target_message_id):
                        match = True
                        break

            # Atau jika Teldrive menyimpan message_id langsung di atribut root
            if not match and str(item.get("messageId") or item.get("message_id")) == str(target_message_id):
                match = True

            if match:
                return TeldriveFile(
                    id=file_id,
                    name=name,
                    size=size,
                    mime_type=mime,
                    channel_id=channel_id,
                    parts=parts
                )

        return None

    def generate_direct_url(self, file_id: str, filename: str = "video.mp4") -> str:
        """
        Generate direct stream URL untuk file Teldrive.
        Menggunakan template yang dapat dikonfigurasi di .env
        """
        template = Config.PUBLIC_STREAM_URL_TEMPLATE
        url = template.format(
            host=self.api_host,
            file_id=file_id,
            filename=filename
        )
        return url
