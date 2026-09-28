"""
Telegram MTProto Streamer Client using Telethon.
Menghubungkan bot ke Telegram secara langsung menggunakan API ID & Hash,
mengambil metadata pesan, dan melakukan streaming byte-range secara on-the-fly.
"""

import logging
import re
from typing import Optional, Tuple, AsyncGenerator
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import (
    DocumentAttributeVideo,
    DocumentAttributeFilename,
    MessageMediaDocument,
)
from telethon.errors import (
    ChannelPrivateError,
    ChatAdminRequiredError,
    ChannelInvalidError,
)

from config import Config

logger = logging.getLogger("televid.tg_streamer")


class TelegramMediaInfo:
    """Struktur data metadata media Telegram."""
    def __init__(
        self,
        message_id: int,
        channel_id: int,
        media: any,
        filename: str,
        size: int,
        mime_type: str = "video/mp4",
        duration: int = 0,
        width: int = 0,
        height: int = 0,
    ):
        self.message_id = message_id
        self.channel_id = channel_id
        self.media = media
        self.filename = filename
        self.size = size
        self.mime_type = mime_type
        self.duration = duration
        self.width = width
        self.height = height

    @property
    def formatted_size(self) -> str:
        """Ukuran file dalam format B, KB, MB, GB."""
        bytes_val = float(self.size)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if bytes_val < 1024.0:
                return f"{bytes_val:.2f} {unit}"
            bytes_val /= 1024.0
        return f"{bytes_val:.2f} PB"


class TelegramStreamer:
    """Manager client Telethon untuk streaming file Telegram."""

    def __init__(self):
        self.client: Optional[TelegramClient] = None
        self._is_started = False
        self.start_error: Optional[str] = None

    async def start(self) -> None:
        """Inisialisasi dan jalankan Telethon client."""
        if self._is_started:
            return

        self.start_error = None
        logger.info(f"Menghubungkan ke server Telegram (MTProto)... API_ID={Config.TG_API_ID}")

        try:
            if Config.TG_SESSION_STRING:
                # Login sebagai user session
                self.client = TelegramClient(
                    StringSession(Config.TG_SESSION_STRING),
                    Config.TG_API_ID,
                    Config.TG_API_HASH
                )
                await self.client.start()
            else:
                # Login sebagai Telegram Bot (@BotFather)
                self.client = TelegramClient(
                    "televid_streamer_bot",
                    Config.TG_API_ID,
                    Config.TG_API_HASH
                )
                await self.client.start(bot_token=Config.TG_BOT_TOKEN)

            me = await self.client.get_me()
            self._is_started = True
            logger.info(f"✅ Terhubung ke Telegram sebagai: @{getattr(me, 'username', 'User')} (ID: {me.id})")
        except Exception as e:
            self._is_started = False
            self.start_error = f"{type(e).__name__}: {e}"
            logger.exception(f"❌ Gagal menghubungkan ke Telegram: {e}")
            raise

    async def stop(self) -> None:
        """Tutup koneksi Telethon client."""
        if self.client and self._is_started:
            await self.client.disconnect()
            self._is_started = False
            logger.info("Koneksi Telegram ditutup.")

    async def get_media_info(self, channel_identifier: any, message_id: int) -> TelegramMediaInfo:
        """
        Mengambil pesan dan metadata video dari channel Telegram.
        
        Raises:
            ValueError: Jika pesan tidak memiliki media video/dokumen.
            PermissionError: Jika bot belum dimasukkan sebagai admin/anggota channel.
            FileNotFoundError: Jika pesan tidak ditemukan.
        """
        if not self._is_started or not self.client:
            err_msg = self.start_error or "Koneksi ke Telegram belum berhasil dijalankan."
            raise RuntimeError(f"Telegram client belum aktif ({err_msg}). Periksa TG_BOT_TOKEN atau kredensial di Railway.")

        # Normalisasi channel ID jika berupa integer positif tanpa prefix -100
        peer = channel_identifier
        if isinstance(peer, int) and peer > 0:
            peer = int(f"-100{peer}")
        elif isinstance(peer, str) and peer.isdigit():
            peer = int(f"-100{peer}")

        try:
            entity = await self.client.get_entity(peer)
        except (ChannelPrivateError, ChatAdminRequiredError):
            raise PermissionError(
                "Bot Telegram belum ditambahkan ke channel ini atau belum dijadikan Admin.\n"
                "👉 Silakan buka Channel Telegram Anda -> Settings -> Administrators -> Tambahkan bot Telegram Anda sebagai Admin."
            )
        except ChannelInvalidError:
            raise PermissionError("Channel Telegram tidak valid atau tidak dapat diakses oleh bot.")
        except ValueError as e:
            if "Could not find the input entity" in str(e):
                raise PermissionError(
                    "Bot Telegram belum mengenali channel ini di database-nya.\n"
                    "👉 **Cara mengatasi:**\n"
                    "1. Pastikan bot Telegram Anda sudah dimasukkan ke channel ini sebagai **Admin**.\n"
                    "2. Kirim pesan sembarang (misal ketik: `tes`) di dalam channel tersebut agar bot bisa mendeteksi channel-nya."
                )
            raise PermissionError(f"Format channel tidak valid: {e}")
        except Exception as e:
            logger.error(f"Gagal mengambil entity channel '{peer}': {e}")
            raise PermissionError(f"Gagal mengakses channel Telegram ({type(e).__name__}): {e}")

        # Ambil pesan berdasarkan message_id
        try:
            message = await self.client.get_messages(entity, ids=message_id)
            if isinstance(message, list):
                message = message[0] if message else None
        except Exception as e:
            logger.error(f"Gagal mengambil pesan ID {message_id}: {e}")
            raise FileNotFoundError(f"Pesan Telegram ID {message_id} tidak ditemukan ({e}).")

        if not message:
            raise FileNotFoundError(f"Pesan ID {message_id} tidak ditemukan di channel tersebut.")

        media_type_name = type(message.media).__name__ if message.media else "None"
        preview_text = repr(message.text or "")[:80]
        logger.info(f"📥 Pesan Telegram ID {message_id} ditemukan: media={media_type_name}, text={preview_text}")

        if not message.media:
            msg_snippet = f": '{message.text}'" if message.text else ""
            raise ValueError(
                f"Pesan ID {message_id} tidak memiliki file video (isi teks pesan{msg_snippet}).\n"
                f"👉 Pastikan Anda menyalin link dari gelembung pesan yang memuat file video."
            )

        # Cek jika media berupa web link preview biasa
        if hasattr(message.media, "webpage"):
            raise ValueError("Pesan tersebut berupa preview link web, bukan file video yang diunggah ke Telegram.")

        # Ekstrak informasi dokumen / video
        filename = f"video_{message_id}.mp4"
        size = 0
        mime_type = "video/mp4"
        duration = 0
        width = 0
        height = 0

        if hasattr(message.media, "document") and message.media.document:
            doc = message.media.document
            size = doc.size
            mime_type = doc.mime_type or "video/mp4"

            for attr in doc.attributes:
                if isinstance(attr, DocumentAttributeFilename):
                    filename = attr.file_name
                elif isinstance(attr, DocumentAttributeVideo):
                    duration = attr.duration
                    width = attr.w
                    height = attr.h
                    if not filename.endswith((".mp4", ".mkv", ".webm", ".mov")):
                        filename = f"{filename}.mp4"

        elif hasattr(message.media, "photo") and message.media.photo:
            filename = f"photo_{message_id}.jpg"
            mime_type = "image/jpeg"
            size = 1024 * 1024
        else:
            raise ValueError(f"Tipe media '{media_type_name}' belum didukung untuk streaming video.")

        # Sanitasi nama file agar aman di URL
        clean_filename = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', filename)
        if not clean_filename.endswith((".mp4", ".webm", ".mkv")):
            clean_filename += ".mp4"

        # Pastikan mime_type adalah video jika file berakhiran video
        # (Sangat penting karena file TeleDrive seringkali di-upload sebagai Document 'application/octet-stream')
        if clean_filename.lower().endswith(".mp4") or mime_type == "application/octet-stream":
            mime_type = "video/mp4"
        elif clean_filename.lower().endswith(".webm"):
            mime_type = "video/webm"
        elif clean_filename.lower().endswith((".mkv", ".mov")):
            mime_type = "video/mp4"

        return TelegramMediaInfo(
            message_id=message_id,
            channel_id=entity.id,
            media=message.media,
            filename=clean_filename,
            size=size,
            mime_type=mime_type,
            duration=duration,
            width=width,
            height=height
        )

    async def iter_stream_chunks(
        self,
        media: any,
        offset: int = 0,
        limit: Optional[int] = None,
        chunk_size: int = 512 * 1024  # 512 KB per chunk
    ) -> AsyncGenerator[bytes, None]:
        """
        Streaming byte range langsung dari server Telegram via MTProto.
        100% in-memory streaming (Zero VPS Harddisk).
        """
        if not self._is_started or not self.client:
            raise RuntimeError("Telegram client belum dijalankan.")

        bytes_sent = 0
        # Telethon iter_download mendukung offset dan request_size
        async for chunk in self.client.iter_download(
            media,
            offset=offset,
            request_size=chunk_size
        ):
            if limit is not None:
                remaining = limit - bytes_sent
                if remaining <= 0:
                    break
                if len(chunk) > remaining:
                    chunk = chunk[:remaining]

            yield chunk
            bytes_sent += len(chunk)
            if limit is not None and bytes_sent >= limit:
                break
