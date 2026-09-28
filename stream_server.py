"""
HTTP Streaming Server using aiohttp.
Menyediakan endpoint video streaming berkecepatan tinggi dengan dukungan
HTTP Range Requests (206 Partial Content) untuk Discord video embed.
"""

import re
import logging
from typing import Optional
from aiohttp import web

from telegram_streamer import TelegramStreamer

logger = logging.getLogger("televid.stream_server")

# Regex untuk membaca HTTP Range header
RE_RANGE = re.compile(r"^bytes=(\d+)-(\d+)?$")


def create_stream_app(telegram_streamer: TelegramStreamer) -> web.Application:
    """Membuat aplikasi web aiohttp untuk streaming video."""
    app = web.Application()

    async def handle_health(request: web.Request) -> web.Response:
        """Endpoint status health check."""
        return web.json_response({
            "status": "online",
            "service": "Televid Telegram Streamer",
            "message": "Zero VPS Storage Active"
        })

    async def handle_stream(request: web.Request) -> web.StreamResponse:
        """
        Endpoint streaming media langsung dari Telegram.
        URL pattern: /stream/{channel_id}/{message_id}/{filename}
        """
        channel_id_raw = request.match_info.get("channel_id")
        message_id_raw = request.match_info.get("message_id")
        filename = request.match_info.get("filename", "video.mp4")

        try:
            channel_id = int(channel_id_raw)
            message_id = int(message_id_raw)
        except (ValueError, TypeError):
            raise web.HTTPBadRequest(text="Parameter channel_id dan message_id harus berupa angka.")

        # Ambil informasi file dari Telegram
        try:
            info = await telegram_streamer.get_media_info(channel_id, message_id)
        except PermissionError as e:
            logger.warning(f"Akses ditolak saat streaming {channel_id}/{message_id}: {e}")
            raise web.HTTPForbidden(text=str(e))
        except FileNotFoundError as e:
            raise web.HTTPNotFound(text=str(e))
        except Exception as e:
            logger.error(f"Error mengambil media info: {e}")
            raise web.HTTPInternalServerError(text="Gagal mengambil file dari Telegram.")

        total_size = info.size
        range_header = request.headers.get("Range")

        # Parsing header Range jika diminta oleh client (Discord/Browser)
        start = 0
        end = total_size - 1
        is_range_request = False

        if range_header:
            match = RE_RANGE.match(range_header.strip())
            if match:
                start_str, end_str = match.groups()
                start = int(start_str)
                if end_str:
                    end = min(int(end_str), total_size - 1)
                is_range_request = True

        if start > end or start >= total_size:
            raise web.HTTPRequestRangeNotSatisfiable(
                headers={"Content-Range": f"bytes */{total_size}"}
            )

        content_length = end - start + 1
        status = 206 if is_range_request else 200

        headers = {
            "Content-Type": info.mime_type or "video/mp4",
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "public, max-age=7200",
            "Access-Control-Allow-Origin": "*",
        }

        if is_range_request:
            headers["Content-Range"] = f"bytes {start}-{end}/{total_size}"

        response = web.StreamResponse(status=status, headers=headers)
        await response.prepare(request)

        try:
            # Stream chunk langsung dari MTProto ke HTTP response tanpa simpan ke disk
            async for chunk in telegram_streamer.iter_stream_chunks(
                media=info.media,
                offset=start,
                limit=content_length,
                chunk_size=512 * 1024  # 512 KB per chunk
            ):
                await response.write(chunk)

            await response.write_eof()
        except (ConnectionResetError, web.HTTPException):
            # Client (misal Discord player) menutup koneksi / seek ke posisi lain
            logger.debug(f"Streaming dihentikan oleh client (seeking/close) untuk {filename}")
        except Exception as e:
            logger.error(f"Error saat streaming data: {e}")

        return response

    # Daftarkan rute
    app.router.add_get("/", handle_health)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/stream/{channel_id}/{message_id}/{filename}", handle_stream)

    return app
