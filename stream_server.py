"""
HTTP Streaming Server using aiohttp.
Menyediakan endpoint video streaming berkecepatan tinggi dengan dukungan
HTTP Range Requests (206 Partial Content) dan OpenGraph Video Embed untuk Discord.
"""

import re
import struct
import zlib
import logging
from typing import Optional
from aiohttp import web

from telegram_streamer import TelegramStreamer

logger = logging.getLogger("televid.stream_server")

# Fallback thumbnail 640x360 dark PNG untuk Discord og:image jika Telegram tidak memiliki thumb
def _generate_fallback_png(w: int = 640, h: int = 360) -> bytes:
    raw_data = b"".join([b"\x00" + bytes([20, 24, 33]) * w for _ in range(h)])
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(raw_data)) + chunk(b'IEND', b'')

FALLBACK_THUMB_PNG = _generate_fallback_png()

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
            # Jika request berasal dari crawler Discordbot tanpa Range, batasi transfer maksimal 1 MB
            # agar Discordbot tidak timeout (3-5 detik) saat mencoba mendownload file video besar.
            user_agent = request.headers.get("User-Agent", "")
            is_discord_crawler = "Discordbot" in user_agent
            stream_limit = min(content_length, 1024 * 1024) if (is_discord_crawler and not is_range_request) else content_length

            # Stream chunk langsung dari MTProto ke HTTP response tanpa simpan ke disk
            async for chunk in telegram_streamer.iter_stream_chunks(
                media=info.media,
                offset=start,
                limit=stream_limit,
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

        try:
            channel_id = int(channel_id_raw)
            message_id = int(message_id_raw)
            info = await telegram_streamer.get_media_info(channel_id, message_id)
        except Exception as e:
            logger.warning(f"Watch endpoint gagal memuat media {channel_id_raw}/{message_id_raw}: {e}")
            raise web.HTTPNotFound(text="Video tidak ditemukan.")

        host = request.headers.get("Host", request.host)
        scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
        clean_filename = info.filename
        watch_url = f"{scheme}://{host}/watch/{channel_id}/{message_id}/{clean_filename}"
        stream_url = f"{scheme}://{host}/stream/{channel_id}/{message_id}/{clean_filename}"
        thumb_url = f"{scheme}://{host}/thumb/{channel_id}/{message_id}.jpg"
        width = info.width or 1280
        height = info.height or 720

        html = f"""<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="utf-8">
    <title>{info.filename}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="theme-color" content="#5865F2">

    <!-- OpenGraph Video Meta Tags untuk Discord & Telegram -->
    <meta property="og:site_name" content="MediaBridge Televid">
    <meta property="og:title" content="{info.filename}">
    <meta property="og:description" content="Ukuran: {info.formatted_size}">
    <meta property="og:type" content="video.other">
    <meta property="og:url" content="{watch_url}">

    <!-- Video Direct Stream -->
    <meta property="og:video" content="{stream_url}">
    <meta property="og:video:url" content="{stream_url}">
    <meta property="og:video:secure_url" content="{stream_url}">
    <meta property="og:video:type" content="video/mp4">
    <meta property="og:video:width" content="{width}">
    <meta property="og:video:height" content="{height}">

    <!-- Poster Thumbnail -->
    <meta property="og:image" content="{thumb_url}">
    <meta property="og:image:url" content="{thumb_url}">
    <meta property="og:image:secure_url" content="{thumb_url}">
    <meta property="og:image:type" content="image/jpeg">
    <meta property="og:image:width" content="{width}">
    <meta property="og:image:height" content="{height}">

    <!-- Twitter Player Card -->
    <meta name="twitter:card" content="player">
    <meta name="twitter:title" content="{info.filename}">
    <meta name="twitter:description" content="Ukuran: {info.formatted_size}">
    <meta name="twitter:image" content="{thumb_url}">
    <meta name="twitter:player" content="{watch_url}">
    <meta name="twitter:player:width" content="{width}">
    <meta name="twitter:player:height" content="{height}">
    <meta name="twitter:player:stream" content="{stream_url}">
    <meta name="twitter:player:stream:content_type" content="video/mp4">
</head>
<body style="margin:0; background:#0b0f19; display:flex; flex-direction:column; align-items:center; justify-content:center; height:100vh; font-family:sans-serif; color:#ffffff;">
    <video controls autoplay playsinline style="max-width:96%; max-height:85vh; border-radius:8px; box-shadow:0 8px 24px rgba(0,0,0,0.5);">
        <source src="{stream_url}" type="video/mp4">
        Browser Anda tidak mendukung HTML5 video playback.
    </video>
    <div style="margin-top:12px; font-size:14px; opacity:0.8;">
        <b>{info.filename}</b> • {info.formatted_size}
    </div>
</body>
</html>"""
        return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "public, max-age=3600"})

    async def handle_thumb(request: web.Request) -> web.Response:
        """Endpoint thumbnail media untuk Discord og:image."""
        channel_id_raw = request.match_info.get("channel_id")
        message_id_raw = request.match_info.get("message_id")
        try:
            channel_id = int(channel_id_raw)
            message_id = int(message_id_raw)
            thumb_data = await telegram_streamer.get_thumbnail_bytes(channel_id, message_id)
            if thumb_data:
                content_type = "image/jpeg"
                if thumb_data.startswith(b"\x89PNG"):
                    content_type = "image/png"
                elif thumb_data.startswith(b"GIF"):
                    content_type = "image/gif"
                elif thumb_data.startswith(b"RIFF") and b"WEBP" in thumb_data[:16]:
                    content_type = "image/webp"
                return web.Response(
                    body=thumb_data,
                    content_type=content_type,
                    headers={"Cache-Control": "public, max-age=86400", "Access-Control-Allow-Origin": "*"}
                )
        except Exception as e:
            logger.debug(f"Gagal mengambil thumbnail: {e}")

        # Fallback: 640x360 placeholder PNG
        return web.Response(
            body=FALLBACK_THUMB_PNG,
            content_type="image/png",
            headers={"Cache-Control": "public, max-age=86400", "Access-Control-Allow-Origin": "*"}
        )

    # Daftarkan rute
    app.router.add_get("/", handle_health)
    app.router.add_get("/health", handle_health)
    app.router.add_route("*", "/stream/{channel_id}/{message_id}/{filename}", handle_stream)
    app.router.add_get("/watch/{channel_id}/{message_id}/{filename}", handle_watch)
    app.router.add_get("/thumb/{channel_id}/{message_id}.jpg", handle_thumb)

    return app
