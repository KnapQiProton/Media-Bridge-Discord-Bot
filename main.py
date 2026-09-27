"""
Alternative entry point for hosting environments (such as Pterodactyl / Kerit Cloud)
that default to running 'main.py' instead of 'bot.py'.
"""

import sys
from config import Config
from bot import bot, logger

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
