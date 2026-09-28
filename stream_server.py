"""
HTTP Streaming Server using aiohttp.
Menyediakan endpoint video streaming berkecepatan tinggi dengan dukungan
HTTP Range Requests (206 Partial Content) dan OpenGraph Video Embed untuk Discord.
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

        # Wajib set video/mp4 agar Discord mengenali sebagai playable video
        content_type = info.mime_type or "video/mp4"
        if filename.lower().endswith(".mp4") or content_type == "application/octet-stream":
            content_type = "video/mp4"

        headers = {
            "Content-Type": content_type,
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

        # Jika request berupa HEAD (biasa digunakan Discordbot scraper untuk cek metadata),
        # cukup kirim header tanpa menulis body data!
        if request.method == "HEAD":
            return response

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
            logger.debug(f"Streaming dihentikan oleh client (seeking/close) untuk {filename}")
        except Exception as e:
            logger.error(f"Error saat streaming data: {e}")

        return response

    async def handle_watch(request: web.Request) -> web.Response:
        """
        Endpoint OpenGraph Video Embed untuk Discord.
        URL pattern: /watch/{channel_id}/{message_id}/{filename}
        Menghasilkan meta tags video.other agar Discord menampilkan inline video player.
        """
        channel_id_raw = request.match_info.get("channel_id")
        message_id_raw = request.match_info.get("message_id")
        filename = request.match_info.get("filename", "video.mp4")

        try:
            channel_id = int(channel_id_raw)
            message_id = int(message_id_raw)
            info = await telegram_streamer.get_media_info(channel_id, message_id)
        except Exception:
            raise web.HTTPNotFound(text="Video tidak ditemukan.")

        host = request.headers.get("Host", request.host)
        scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
        stream_url = f"{scheme}://{host}/stream/{channel_id}/{message_id}/{filename}"
        width = info.width or 1280
        height = info.height or 720

        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{info.filename}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <!-- Discord OpenGraph Video Meta Tags -->
    <meta property="og:site_name" content="Televid Streamer">
    <meta property="og:title" content="{info.filename}">
    <meta property="og:description" content="Ukuran: {info.formatted_size}">
    <meta property="og:type" content="video.other">
    <meta property="og:video" content="{stream_url}">
    <meta property="og:video:url" content="{stream_url}">
    <meta property="og:video:secure_url" content="{stream_url}">
    <meta property="og:video:type" content="video/mp4">
    <meta property="og:video:width" content="{width}">
    <meta property="og:video:height" content="{height}">
    <!-- Twitter Player Card -->
    <meta name="twitter:card" content="player">
    <meta name="twitter:title" content="{info.filename}">
    <meta name="twitter:player" content="{stream_url}">
    <meta name="twitter:player:width" content="{width}">
    <meta name="twitter:player:height" content="{height}">
</head>
<body style="margin:0; background:#0b0f19; display:flex; align-items:center; justify-content:center; height:100vh;">
    <video controls autoplay style="max-width:100%; max-height:100%;">
        <source src="{stream_url}" type="video/mp4">
        Browser Anda tidak mendukung tag video.
    </video>
</body>
</html>"""
        return web.Response(text=html, content_type="text/html")

    # Daftarkan rute
    app.router.add_get("/", handle_health)
    app.router.add_get("/health", handle_health)
    app.router.add_route("*", "/stream/{channel_id}/{message_id}/{filename}", handle_stream)
    app.router.add_get("/watch/{channel_id}/{message_id}/{filename}", handle_watch)

    return app
