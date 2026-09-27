"""
Discord Bot Televid.
Slash commands:
- /televid <link_telegram>: Generate direct streaming link untuk Discord video embed.
- /televid-info <link_telegram>: Tampilkan metadata video tanpa link streaming.
"""

import sys
import logging
import discord
from discord import app_commands
from discord.ext import commands

from config import Config
from telegram_parser import parse_telegram_link
from teldrive_client import (
    TeldriveClient,
    FileNotFoundInTeldriveError,
    TeldriveOfflineError,
    TeldriveAuthError,
    TeldriveError,
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("televid.bot")

# Validasi konfigurasi awal
Config.validate()

# Inisialisasi Discord Intents dan Bot
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# Inisialisasi Teldrive Client
teldrive = TeldriveClient()


@bot.event
async def on_ready():
    """Event dipanggil saat bot berhasil online dan login ke Discord."""
    logger.info(f"🤖 Bot berhasil login sebagai: {bot.user} (ID: {bot.user.id})")

    # Inisialisasi client Teldrive (database pool jika ada)
    await teldrive.init()

    # Sinkronisasi slash command ke Guild tertentu (instan) atau Global
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
    logger.info("🚀 Televid Bot siap digunakan!")


@bot.tree.command(
    name="televid",
    description="Ubah link video Telegram menjadi playable video embed di Discord."
)
@app_commands.describe(link="Link pesan Telegram video (contoh: https://t.me/c/4483946044/2)")
async def televid_command(interaction: discord.Interaction, link: str):
    """
    Handler untuk slash command /televid <link>.
    Alur kerja:
    1. Parse link Telegram -> dapat message_id
    2. Query Teldrive -> dapat file_id & metadata
    3. Generate direct stream URL
    4. Reply URL ke Discord untuk auto-embed streaming
    """
    # Defer interaction agar tidak timeout (Discord batas 3 detik untuk initial response)
    await interaction.response.defer(thinking=True)

    # 1. Parse link Telegram
    try:
        parsed = parse_telegram_link(link)
    except ValueError:
        await interaction.followup.send(
            "❌ **Format link tidak valid.**\n"
            "Pastikan link berbentuk `https://t.me/c/<channel_id>/<message_id>` atau `https://t.me/<channel_name>/<message_id>`.",
            ephemeral=True
        )
        return

    # 2. Query Teldrive
    try:
        file_info = await teldrive.get_file_by_message_id(
            message_id=parsed.message_id,
            channel_id=parsed.raw_channel_id or Config.TELDRIVE_CHANNEL_ID
        )
    except FileNotFoundInTeldriveError:
        await interaction.followup.send(
            "⚠️ **Video belum ada di Teldrive, upload dulu via Teldrive UI.**",
            ephemeral=True
        )
        return
    except TeldriveOfflineError:
        await interaction.followup.send(
            "🔌 **Teldrive sedang down, coba lagi nanti.**",
            ephemeral=True
        )
        return
    except TeldriveAuthError:
        await interaction.followup.send(
            "🔒 **Autentikasi Teldrive gagal.** Periksa konfigurasi `TELDRIVE_ACCESS_TOKEN` di server bot.",
            ephemeral=True
        )
        return
    except TeldriveError as e:
        await interaction.followup.send(
            f"❌ **Terjadi kesalahan Teldrive:** {e}",
            ephemeral=True
        )
        return
    except Exception as e:
        logger.exception(f"Unhandled error pada /televid: {e}")
        await interaction.followup.send(
            "❌ Terjadi kesalahan internal saat memproses video.",
            ephemeral=True
        )
        return

    # 3. Generate direct stream URL
    direct_url = teldrive.generate_direct_url(file_info.id, file_info.name)

    # 4. Kirim respon ke Discord
    # Meletakkan URL langsung di pesan agar Discord scraper mendeteksi video dan merender inline player
    response_text = (
        f"🎬 **Video siap diputar!**\n"
        f"**Nama:** `{file_info.name}`\n"
        f"**Ukuran:** `{file_info.formatted_size}`\n"
        f"**URL:** {direct_url}"
    )

    # Tambahkan action button opsional untuk membuka link di tab baru
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Tonton / Download", url=direct_url, style=discord.ButtonStyle.link))

    await interaction.followup.send(response_text, view=view)
    logger.info(f"✅ Berhasil memproses televid untuk file '{file_info.name}' (ID: {file_info.id})")


@bot.tree.command(
    name="televid-info",
    description="Lihat metadata video dari link Telegram tanpa membagikan link streaming."
)
@app_commands.describe(link="Link pesan Telegram video (contoh: https://t.me/c/4483946044/2)")
async def televid_info_command(interaction: discord.Interaction, link: str):
    """
    Handler untuk slash command /televid-info <link>.
    Menampilkan info metadata tanpa expose direct stream URL.
    """
    await interaction.response.defer(thinking=True, ephemeral=True)

    # 1. Parse link Telegram
    try:
        parsed = parse_telegram_link(link)
    except ValueError:
        await interaction.followup.send(
            "❌ **Format link tidak valid.**",
            ephemeral=True
        )
        return

    # 2. Query Teldrive
    try:
        file_info = await teldrive.get_file_by_message_id(
            message_id=parsed.message_id,
            channel_id=parsed.raw_channel_id or Config.TELDRIVE_CHANNEL_ID
        )
    except FileNotFoundInTeldriveError:
        await interaction.followup.send(
            "⚠️ **Video belum ada di Teldrive, upload dulu via Teldrive UI.**",
            ephemeral=True
        )
        return
    except TeldriveOfflineError:
        await interaction.followup.send(
            "🔌 **Teldrive sedang down, coba lagi nanti.**",
            ephemeral=True
        )
        return
    except TeldriveAuthError:
        await interaction.followup.send(
            "🔒 **Autentikasi Teldrive gagal.** Periksa `TELDRIVE_ACCESS_TOKEN`.",
            ephemeral=True
        )
        return
    except Exception as e:
        logger.exception(f"Unhandled error pada /televid-info: {e}")
        await interaction.followup.send(
            "❌ Terjadi kesalahan saat mengambil metadata file.",
            ephemeral=True
        )
        return

    # 3. Buat Rich Embed untuk informasi metadata
    embed = discord.Embed(
        title="ℹ️ Informasi Metadata Video",
        description=f"Metadata file dari pesan Telegram `{parsed.message_id}`",
        color=discord.Color.blue()
    )
    embed.add_field(name="📄 Nama File", value=f"`{file_info.name}`", inline=False)
    embed.add_field(name="📦 Ukuran", value=f"`{file_info.formatted_size}`", inline=True)
    embed.add_field(name="🎞️ Tipe Konten", value=f"`{file_info.mime_type}`", inline=True)
    embed.add_field(name="🆔 Teldrive ID", value=f"`{file_info.id}`", inline=False)
    
    parts_count = len(file_info.parts) if file_info.parts else 1
    embed.add_field(name="🧩 Jumlah Parts / Segmen", value=f"`{parts_count} part(s)`", inline=True)

    if parsed.raw_channel_id:
        embed.add_field(name="📢 Channel ID", value=f"`{parsed.raw_channel_id}`", inline=True)

    embed.set_footer(text="Televid Bot • Zero VPS Storage")

    await interaction.followup.send(embed=embed, ephemeral=True)
    logger.info(f"ℹ️ Berhasil menampilkan info file '{file_info.name}'")


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
