"""
Televid Discord Bot (Direct Telegram Streamer).
Slash Commands:
- /televid <link_telegram>: Menghasilkan playable direct streaming link di Discord.
- /televid-info <link_telegram>: Melihat metadata video tanpa membagikan link streaming.
"""

import sys
import asyncio
import logging
import discord
from discord import app_commands
from discord.ext import commands
from aiohttp import web

from config import Config
from telegram_parser import parse_telegram_link
from telegram_streamer import TelegramStreamer
from stream_server import create_stream_app

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("televid.bot")

# Validasi konfigurasi awal
Config.validate()

# Inisialisasi Discord Intents & Bot
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# Inisialisasi Telegram Streamer Client
tg_streamer = TelegramStreamer()
runner: web.AppRunner = None


async def start_web_server():
    """Menjalankan aiohttp web server pada event loop yang sama dengan bot."""
    global runner
    app = create_stream_app(tg_streamer)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, Config.WEB_HOST, Config.WEB_PORT)
    await site.start()
    logger.info(f"🌐 HTTP Stream Server berjalan di http://{Config.WEB_HOST}:{Config.WEB_PORT}")


@bot.event
async def on_ready():
    """Event saat Discord Bot berhasil terhubung."""
    logger.info(f"🤖 Bot Discord berhasil login sebagai: {bot.user} (ID: {bot.user.id})")

    # 1. Jalankan koneksi Telegram MTProto
    try:
        await tg_streamer.start()
    except Exception as e:
        logger.error(f"❌ Gagal menghubungkan ke Telegram: {e}")

    # 2. Jalankan HTTP Web Stream Server
    try:
        await start_web_server()
    except Exception as e:
        logger.error(f"❌ Gagal menyalakan web streaming server: {e}")

    # 3. Sinkronisasi Slash Commands
    try:
        if Config.DISCORD_GUILD_ID:
            guild = discord.Object(id=Config.DISCORD_GUILD_ID)
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            logger.info(f"⚡ Berhasil sinkronisasi {len(synced)} slash command ke Guild ID {Config.DISCORD_GUILD_ID}.")
        else:
            synced = await bot.tree.sync()
            logger.info(f"🌐 Berhasil sinkronisasi {len(synced)} slash command secara Global.")
    except Exception as e:
        logger.error(f"❌ Gagal sinkronisasi slash command: {e}")

    # Set aktivitas bot
    activity = discord.Activity(
        type=discord.ActivityType.watching,
        name="video Telegram via /televid"
    )
    await bot.change_presence(status=discord.Status.online, activity=activity)
    logger.info("🚀 Televid Streamer Bot siap digunakan!")


@bot.tree.command(
    name="televid",
    description="Ubah link video Telegram menjadi playable video embed di Discord."
)
@app_commands.describe(link="Link pesan Telegram video (contoh: https://t.me/c/4483946044/2)")
async def televid_command(interaction: discord.Interaction, link: str):
    """
    Handler untuk slash command /televid <link>.
    """
    await interaction.response.defer(thinking=True)

    # 1. Parse link Telegram
    try:
        parsed = parse_telegram_link(link)
    except ValueError:
        await interaction.followup.send(
            "❌ **Format link tidak valid.**\n"
            "Pastikan link berbentuk `https://t.me/c/<channel_id>/<message_id>` atau `https://t.me/<username>/<message_id>`.",
            ephemeral=True
        )
        return

    # 2. Ambil channel target (bisa dari raw_channel_id atau username)
    channel_target = parsed.full_channel_id or parsed.raw_channel_id or parsed.username

    # 3. Ambil metadata media dari Telegram
    try:
        info = await tg_streamer.get_media_info(channel_target, parsed.message_id)
    except PermissionError as e:
        await interaction.followup.send(
            f"⚠️ **Akses Ditolak:** {e}",
            ephemeral=True
        )
        return
    except FileNotFoundError:
        await interaction.followup.send(
            f"❌ **Pesan ID `{parsed.message_id}` tidak ditemukan** di channel tersebut.",
            ephemeral=True
        )
        return
    except ValueError as e:
        await interaction.followup.send(
            f"⚠️ **Bukan Video:** {e}",
            ephemeral=True
        )
        return
    except Exception as e:
        logger.exception(f"Error saat memproses /televid: {e}")
        await interaction.followup.send(
            "❌ Terjadi kesalahan internal saat mengambil media Telegram.",
            ephemeral=True
        )
        return

    # 4. Generate URL Streaming
    base_url = Config.STREAM_BASE_URL or f"http://{Config.WEB_HOST}:{Config.WEB_PORT}"
    # Gunakan channel_id positif bersih di URL
    cid_str = str(info.channel_id).replace("-100", "").replace("-", "")
    stream_url = f"{base_url}/stream/{cid_str}/{info.message_id}/{info.filename}"

    # 5. Kirim respon ke Discord
    response_text = (
        f"🎬 **Video siap diputar!**\n"
        f"**Nama:** `{info.filename}`\n"
        f"**Ukuran:** `{info.formatted_size}`\n"
        f"**URL:** {stream_url}"
    )

    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Tonton / Download", url=stream_url, style=discord.ButtonStyle.link))

    await interaction.followup.send(response_text, view=view)
    logger.info(f"✅ Berhasil memproses televid untuk file '{info.filename}' (Ukuran: {info.formatted_size})")


@bot.tree.command(
    name="televid-info",
    description="Lihat metadata video dari link Telegram tanpa membagikan link streaming."
)
@app_commands.describe(link="Link pesan Telegram video (contoh: https://t.me/c/4483946044/2)")
async def televid_info_command(interaction: discord.Interaction, link: str):
    """
    Handler untuk slash command /televid-info <link>.
    """
    await interaction.response.defer(thinking=True, ephemeral=True)

    try:
        parsed = parse_telegram_link(link)
    except ValueError:
        await interaction.followup.send("❌ **Format link tidak valid.**", ephemeral=True)
        return

    channel_target = parsed.full_channel_id or parsed.raw_channel_id or parsed.username

    try:
        info = await tg_streamer.get_media_info(channel_target, parsed.message_id)
    except Exception as e:
        await interaction.followup.send(f"❌ **Gagal mengambil info:** {e}", ephemeral=True)
        return

    # Format durasi (jika ada)
    duration_str = "-"
    if info.duration > 0:
        mins, secs = divmod(info.duration, 60)
        hours, mins = divmod(mins, 60)
        duration_str = f"{hours:02d}:{mins:02d}:{secs:02d}" if hours > 0 else f"{mins:02d}:{secs:02d}"

    # Rich Embed Metadata
    embed = discord.Embed(
        title="ℹ️ Informasi Metadata Video Telegram",
        description=f"Detail media dari pesan ID `{parsed.message_id}`",
        color=discord.Color.blue()
    )
    embed.add_field(name="📄 Nama File", value=f"`{info.filename}`", inline=False)
    embed.add_field(name="📦 Ukuran", value=f"`{info.formatted_size}`", inline=True)
    embed.add_field(name="⏱️ Durasi", value=f"`{duration_str}`", inline=True)
    embed.add_field(name="🎞️ Tipe Konten", value=f"`{info.mime_type}`", inline=True)

    if info.width and info.height:
        embed.add_field(name="📐 Resolusi", value=f"`{info.width}x{info.height}`", inline=True)

    embed.add_field(name="📢 Channel ID", value=f"`{info.channel_id}`", inline=True)
    embed.set_footer(text="Televid Streamer • Zero VPS Storage")

    await interaction.followup.send(embed=embed, ephemeral=True)
    logger.info(f"ℹ️ Berhasil menampilkan info video '{info.filename}'")


if __name__ == "__main__":
    if not Config.DISCORD_TOKEN:
        logger.critical("Kunci DISCORD_TOKEN tidak ditemukan di file .env!")
        sys.exit(1)

    try:
        bot.run(Config.DISCORD_TOKEN)
    except KeyboardInterrupt:
        logger.info("Bot dimatikan oleh pengguna.")
    except Exception as e:
        logger.critical(f"Bot crash: {e}")
