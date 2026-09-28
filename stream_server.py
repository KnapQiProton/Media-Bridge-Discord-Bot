"""
HTTP Streaming Server using aiohttp.
Menyediakan endpoint video streaming berkecepatan tinggi dengan dukungan
HTTP Range Requests (206 Partial Content), OpenGraph Video Shim Endpoint,
dan Poster Image Caching Endpoint untuk Discord.
"""

import re
import struct
import zlib
import logging
from typing import Optional, Tuple, Dict
from aiohttp import web

from telegram_streamer import TelegramStreamer

logger = logging.getLogger("televid.stream_server")

# In-memory metadata store untuk OpenGraph Shim endpoint.
# Menyimpan metadata video saat bot membuat link, sehingga /embed/<token> dapat
# merespons Discordbot crawler dalam hitungan < 1ms TANPA perlu koneksi ke Telegram.
EMBED_METADATA_STORE: Dict[str, dict] = {}

# In-memory cache untuk poster thumbnail video dari Telegram.
# Memastikan /poster/<token> merespons Discord crawler instan (< 1ms) tanpa I/O.
POSTER_CACHE: Dict[str, bytes] = {}


def register_embed_token(
    channel_id: int,
    message_id: int,
    title: str,
    description: str,
    width: int,
    height: int,
    filename: str,
    thumb_bytes: Optional[bytes] = None
) -> str:
    """
    Mendaftarkan metadata video dan thumbnail ke memori dan menghasilkan token unik.
    Memungkinkan endpoint /embed/<token> dan /poster/<token> merespons instan (< 1ms).
    """
    cid_str = str(channel_id).replace("-100", "").replace("-", "")
    token = f"{cid_str}_{message_id}_{filename}"
    meta = {
        "channel_id": channel_id,
        "message_id": message_id,
        "title": title,
        "description": description,
        "width": width or 1280,
        "height": height or 720,
        "filename": filename
    }
    EMBED_METADATA_STORE[token] = meta
    short_token = f"{cid_str}_{message_id}"
    EMBED_METADATA_STORE[short_token] = meta

    if thumb_bytes:
        POSTER_CACHE[token] = thumb_bytes
        POSTER_CACHE[short_token] = thumb_bytes

    return token


# Fallback thumbnail 1280x720 dark PNG untuk Discord og:image jika Telegram tidak memiliki thumb
def _generate_fallback_png(w: int = 1280, h: int = 720) -> bytes:
    raw_data = b"".join([b"\x00" + bytes([20, 24, 33]) * w for _ in range(h)])
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(raw_data)) + chunk(b'IEND', b'')

FALLBACK_THUMB_PNG = _generate_fallback_png(1280, 720)

# Regex untuk membaca HTTP Range header:
# Mendukung standard range: bytes=0-1024, bytes=1024-, serta suffix range: bytes=-65536
# (Sangat penting untuk membaca atom moov MP4 di akhir berkas pada rekaman layar / video besar)
RE_RANGE = re.compile(r"^bytes=(?:(\d+)-(\d+)?|-(\d+))$")


def parse_token(token: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Ekstrak channel_id, message_id, filename dari token URL.
    Mendukung format:
    - {channel_id}_{message_id}_{filename}
    - {channel_id}_{message_id}
    """
    if not token:
        return None, None, None
    parts = token.split("_", 2)
    if len(parts) >= 2:
        filename = parts[2] if len(parts) > 2 else "video.mp4"
        return parts[0], parts[1], filename
    return None, None, None


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
        Mendukung rute:
        - /stream/{channel_id}/{message_id}/{filename}
        - /stream/{token}
        Mendukung HTTP Range Requests (206 Partial Content) untuk seek player dan parsing metadata atom.
        """
        channel_id_raw = request.match_info.get("channel_id")
        message_id_raw = request.match_info.get("message_id")
        filename = request.match_info.get("filename")

        if not channel_id_raw or not message_id_raw:
            token = request.match_info.get("token", "")
            if token in EMBED_METADATA_STORE:
                meta = EMBED_METADATA_STORE[token]
                channel_id_raw = str(meta["channel_id"])
                message_id_raw = str(meta["message_id"])
                filename = meta["filename"]
            else:
                channel_id_raw, message_id_raw, filename_parsed = parse_token(token)
                if filename_parsed:
                    filename = filename_parsed

        filename = filename or "video.mp4"

        try:
            channel_id = int(channel_id_raw)
            message_id = int(message_id_raw)
        except (ValueError, TypeError):
            raise web.HTTPBadRequest(text="Parameter channel_id dan message_id harus berupa angka.")

        # Ambil informasi file dari Telegram (menggunakan in-memory cache jika sudah pernah diakses)
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

        # Parsing header Range jika diminta oleh client (Discord / Browser)
        start = 0
        end = total_size - 1
        is_range_request = False

        if range_header:
            match = RE_RANGE.match(range_header.strip())
            if match:
                start_str, end_str, suffix_str = match.groups()
                if suffix_str:
                    # Suffix range (contoh: bytes=-65536) -> membaca N byte terakhir berkas
                    suffix = int(suffix_str)
                    start = max(0, total_size - suffix)
                    end = total_size - 1
                else:
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

        # Jika request berupa HEAD (biasa digunakan Discordbot scraper untuk cek metadata file),
        # cukup kirim header tanpa menulis body data
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

    async def handle_embed(request: web.Request) -> web.Response:
        """
        Endpoint OpenGraph Video Shim untuk Discord.
        Mendukung rute:
        - /embed/{token}
        - /embed/{channel_id}/{message_id}/{filename}
        - /watch/{channel_id}/{message_id}/{filename} (backward compatibility)

        Menghasilkan HTML shim super cepat dengan meta tags OpenGraph (og:image primary trigger)
        dan fallback HTML5 video player untuk browser.
        """
        token = request.match_info.get("token")
        channel_id_raw = request.match_info.get("channel_id")
        message_id_raw = request.match_info.get("message_id")
        filename_raw = request.match_info.get("filename")

        # 1. Cek langsung dari in-memory token store untuk respon instan < 1ms tanpa koneksi Telegram
        meta = None
        if token and token in EMBED_METADATA_STORE:
            meta = EMBED_METADATA_STORE[token]
        elif channel_id_raw and message_id_raw:
            cid_str = str(channel_id_raw).replace("-100", "").replace("-", "")
            meta = EMBED_METADATA_STORE.get(f"{cid_str}_{message_id_raw}")
        elif token:
            c_raw, m_raw, fn_raw = parse_token(token)
            if c_raw and m_raw:
                meta = EMBED_METADATA_STORE.get(f"{c_raw}_{m_raw}")

        if meta:
            channel_id = meta["channel_id"]
            message_id = meta["message_id"]
            video_title = meta["title"]
            video_description = meta["description"]
            width = meta["width"]
            height = meta["height"]
            clean_filename = meta["filename"]
            effective_token = token or f"{str(channel_id).replace('-100', '').replace('-', '')}_{message_id}_{clean_filename}"
        else:
            # Fallback jika metadata belum terdaftar di memori (misal server baru restart)
            if not channel_id_raw or not message_id_raw:
                channel_id_raw, message_id_raw, filename_parsed = parse_token(token or "")
                if filename_parsed:
                    filename_raw = filename_parsed

            try:
                channel_id = int(channel_id_raw)
                message_id = int(message_id_raw)
                info = await telegram_streamer.get_media_info(channel_id, message_id)
            except Exception as e:
                logger.warning(f"Embed endpoint gagal memuat media {channel_id_raw}/{message_id_raw}: {e}")
                raise web.HTTPNotFound(text="Video tidak ditemukan.")

            video_title = info.filename
            video_description = f"Ukuran: {info.formatted_size}"
            width = info.width or 1280
            height = info.height or 720
            clean_filename = filename_raw or info.filename
            cid_clean = str(channel_id).replace("-100", "").replace("-", "")
            effective_token = token or f"{cid_clean}_{message_id}_{clean_filename}"

        host = request.headers.get("Host", request.host)
        scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
        public_base_url = f"{scheme}://{host}"
        stream_url = f"{public_base_url}/stream/{effective_token}"
        poster_url = f"{public_base_url}/poster/{effective_token}"
        player_url = f"{public_base_url}/player/{effective_token}"

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{video_title}</title>

  <!-- Discord & Twitter Player Card Tags -->
  <meta name="twitter:card" content="player">
  <meta name="twitter:title" content="{video_title}">
  <meta name="twitter:description" content="Streamed via Media Bridge Bot">
  <meta name="twitter:image" content="{poster_url}">
  <meta name="twitter:image:alt" content="{video_title}">
  <meta name="twitter:player" content="{player_url}">
  <meta name="twitter:player:width" content="{width}">
  <meta name="twitter:player:height" content="{height}">
  <meta name="twitter:player:stream" content="{stream_url}">
  <meta name="twitter:player:stream:content_type" content="video/mp4">

  <!-- OpenGraph Video Tags -->
  <meta property="og:type" content="video.other">
  <meta property="og:title" content="{video_title}">
  <meta property="og:description" content="Streamed via Media Bridge Bot">
  <meta property="og:video:url" content="{stream_url}">
  <meta property="og:video:secure_url" content="{stream_url}">
  <meta property="og:video:type" content="video/mp4">
  <meta property="og:video:width" content="{width}">
  <meta property="og:video:height" content="{height}">

  <!-- OpenGraph Image Poster -->
  <meta property="og:image" content="{poster_url}">
  <meta property="og:image:secure_url" content="{poster_url}">
  <meta property="og:image:type" content="image/jpeg">
  <meta property="og:image:width" content="{width}">
  <meta property="og:image:height" content="{height}">
  <meta property="og:image:alt" content="{video_title}">

  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    html, body {{
      width: 100%;
      min-height: 100vh;
      background: #0d0d0d;
      color: #ffffff;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    .container {{
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 24px;
    }}
    .player-wrap {{
      width: 100%;
      max-width: 1080px;
      background: #000;
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.6);
    }}
    video {{
      width: 100%;
      height: auto;
      display: block;
      background: #000;
    }}
    .meta {{
      margin-top: 16px;
      max-width: 1080px;
      width: 100%;
      padding: 0 4px;
    }}
    .meta h1 {{
      font-size: 18px;
      font-weight: 600;
      margin-bottom: 6px;
      line-height: 1.3;
      word-break: break-word;
    }}
    .meta p {{
      font-size: 13px;
      color: #a0a0a0;
      line-height: 1.5;
    }}
    .brand {{
      margin-top: 24px;
      font-size: 12px;
      color: #6b6b6b;
      text-align: center;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="player-wrap">
      <video
        controls
        autoplay
        playsinline
        preload="metadata"
        poster="{poster_url}"
      >
        <source src="{stream_url}" type="video/mp4">
        Your browser does not support HTML5 video.
      </video>
    </div>

    <div class="meta">
      <h1>{video_title}</h1>
      <p>{video_description}</p>
    </div>

    <div class="brand">
      ⚡ Powered by Media Bridge Bot
    </div>
  </div>
</body>
</html>"""
        return web.Response(
            text=html,
            content_type="text/html",
            charset="utf-8",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}
        )

    async def handle_player(request: web.Request) -> web.Response:
        """
        Endpoint standalone iframe player untuk twitter:player card Discord.
        Mendukung rute:
        - /player/{token}
        - /player/{channel_id}/{message_id}/{filename}

        Menghasilkan HTML player minimalis full-screen tanpa header/footer/dekorasi,
        merespons super cepat (< 500ms) tanpa menyentuh koneksi Telegram.
        """
        token = request.match_info.get("token")
        channel_id_raw = request.match_info.get("channel_id")
        message_id_raw = request.match_info.get("message_id")
        filename_raw = request.match_info.get("filename")

        # 1. Cek langsung dari in-memory token store untuk respon instan < 1ms
        meta = None
        if token and token in EMBED_METADATA_STORE:
            meta = EMBED_METADATA_STORE[token]
        elif channel_id_raw and message_id_raw:
            cid_str = str(channel_id_raw).replace("-100", "").replace("-", "")
            meta = EMBED_METADATA_STORE.get(f"{cid_str}_{message_id_raw}")
        elif token:
            c_raw, m_raw, fn_raw = parse_token(token)
            if c_raw and m_raw:
                meta = EMBED_METADATA_STORE.get(f"{c_raw}_{m_raw}")

        if meta:
            video_title = meta["title"]
            clean_filename = meta["filename"]
            channel_id = meta["channel_id"]
            message_id = meta["message_id"]
            cid_clean = str(channel_id).replace("-100", "").replace("-", "")
            effective_token = token or f"{cid_clean}_{message_id}_{clean_filename}"
        else:
            # Parse token langsung tanpa memanggil API Telegram agar respon < 500ms
            if not channel_id_raw or not message_id_raw:
                c_raw, m_raw, fn_raw = parse_token(token or "")
                if c_raw and m_raw:
                    channel_id_raw, message_id_raw = c_raw, m_raw
                    filename_raw = fn_raw or "video.mp4"

            if channel_id_raw and message_id_raw:
                clean_filename = filename_raw or "video.mp4"
                video_title = clean_filename
                cid_clean = str(channel_id_raw).replace("-100", "").replace("-", "")
                effective_token = token or f"{cid_clean}_{message_id_raw}_{clean_filename}"
            else:
                raise web.HTTPNotFound(text="Player tidak ditemukan.")

        host = request.headers.get("Host", request.host)
        scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
        public_base_url = f"{scheme}://{host}"
        stream_url = f"{public_base_url}/stream/{effective_token}"
        poster_url = f"{public_base_url}/poster/{effective_token}"

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{video_title}</title>
  <style>
    * {{
      margin: 0;
      padding: 0;
      box-sizing: border-box;
    }}
    html, body {{
      width: 100%;
      height: 100%;
      overflow: hidden;
      background: #000;
    }}
    video {{
      width: 100%;
      height: 100%;
      object-fit: contain;
      background: #000;
      display: block;
    }}
  </style>
</head>
<body>
  <video
    controls
    autoplay
    playsinline
    preload="metadata"
    poster="{poster_url}"
  >
    <source src="{stream_url}" type="video/mp4">
    Your browser does not support HTML5 video.
  </video>
</body>
</html>"""
        return web.Response(
            text=html,
            content_type="text/html",
            charset="utf-8",
            headers={
                "Cache-Control": "public, max-age=300",
                "Access-Control-Allow-Origin": "*"
            }
        )

    async def handle_poster(request: web.Request) -> web.Response:
        """
        Endpoint poster image untuk Discord og:image dan player poster.
        Mendukung rute:
        - /poster/{token}
        - /poster/{channel_id}/{message_id}.jpg
        - /thumb/{channel_id}/{message_id}.jpg
        """
        token = request.match_info.get("token")
        channel_id_raw = request.match_info.get("channel_id")
        message_id_raw = request.match_info.get("message_id")

        thumb_data = None

        # 1. Cek in-memory poster cache (Respons instan < 1ms)
        if token and token in POSTER_CACHE:
            thumb_data = POSTER_CACHE[token]
        elif channel_id_raw and message_id_raw:
            cid_clean = str(channel_id_raw).replace("-100", "").replace("-", "")
            key = f"{cid_clean}_{message_id_raw}"
            if key in POSTER_CACHE:
                thumb_data = POSTER_CACHE[key]
        elif token:
            c_raw, m_raw, _ = parse_token(token)
            if c_raw and m_raw:
                key = f"{c_raw}_{m_raw}"
                if key in POSTER_CACHE:
                    thumb_data = POSTER_CACHE[key]

        # 2. Jika belum ada di cache, unduh dari Telegram on-demand
        if not thumb_data:
            if not channel_id_raw or not message_id_raw:
                if token in EMBED_METADATA_STORE:
                    meta = EMBED_METADATA_STORE[token]
                    channel_id_raw = str(meta["channel_id"])
                    message_id_raw = str(meta["message_id"])
                else:
                    c_raw, m_raw, _ = parse_token(token or "")
                    if c_raw and m_raw:
                        channel_id_raw, message_id_raw = c_raw, m_raw

            if channel_id_raw and message_id_raw:
                try:
                    cid = int(channel_id_raw)
                    mid = int(message_id_raw)
                    thumb_data = await telegram_streamer.get_thumbnail_bytes(cid, mid)
                    if thumb_data:
                        # Cache untuk request berikutnya
                        if token:
                            POSTER_CACHE[token] = thumb_data
                        cid_clean = str(channel_id_raw).replace("-100", "").replace("-", "")
                        POSTER_CACHE[f"{cid_clean}_{message_id_raw}"] = thumb_data
                except Exception as e:
                    logger.debug(f"Gagal mengambil thumbnail dari Telegram: {e}")

        # 3. Kembalikan data thumbnail atau fallback placeholder
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

        # Fallback: 1280x720 dark placeholder PNG
        return web.Response(
            body=FALLBACK_THUMB_PNG,
            content_type="image/png",
            headers={"Cache-Control": "public, max-age=86400", "Access-Control-Allow-Origin": "*"}
        )

    # Daftarkan rute
    app.router.add_get("/", handle_health)
    app.router.add_get("/health", handle_health)
    app.router.add_route("*", "/stream/{channel_id}/{message_id}/{filename}", handle_stream)
    app.router.add_route("*", "/stream/{token}", handle_stream)
    app.router.add_get("/embed/{channel_id}/{message_id}/{filename}", handle_embed)
    app.router.add_get("/embed/{token}", handle_embed)
    app.router.add_get("/player/{channel_id}/{message_id}/{filename}", handle_player)
    app.router.add_get("/player/{token}", handle_player)
    app.router.add_get("/watch/{channel_id}/{message_id}/{filename}", handle_embed)
    app.router.add_get("/poster/{channel_id}/{message_id}.jpg", handle_poster)
    app.router.add_get("/poster/{token}", handle_poster)
    app.router.add_get("/thumb/{channel_id}/{message_id}.jpg", handle_poster)

    return app
