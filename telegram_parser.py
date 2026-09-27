"""
Telegram link parser module.
Mengekstrak channel_id, username, dan message_id dari link Telegram.
"""

import re
from typing import NamedTuple, Optional

class TelegramParsedLink(NamedTuple):
    message_id: int
    raw_channel_id: Optional[int] = None      # Contoh: 4483946044
    full_channel_id: Optional[int] = None     # Contoh: -1004483946044
    username: Optional[str] = None           # Contoh: 'my_channel'
    is_private_channel: bool = False

# Regex patterns untuk berbagai format link Telegram
# 1. Private channel / supergroup: https://t.me/c/4483946044/2 atau t.me/c/4483946044/2?single
RE_PRIVATE_CHANNEL = re.compile(
    r"(?:https?:\/\/)?(?:www\.)?(?:t\.me|telegram\.me)\/c\/(\d+)\/(\d+)(?:\?[^\s]*)?",
    re.IGNORECASE
)

# 2. Public channel: https://t.me/channel_name/1234 atau telegram.me/channel_name/1234
RE_PUBLIC_CHANNEL = re.compile(
    r"(?:https?:\/\/)?(?:www\.)?(?:t\.me|telegram\.me)\/([a-zA-Z0-9_]{4,})\/(\d+)(?:\?[^\s]*)?",
    re.IGNORECASE
)

# 3. Protocol link: tg://resolve?domain=channel_name&post=1234
RE_TG_PROTOCOL = re.compile(
    r"tg:\/\/resolve\?domain=([a-zA-Z0-9_]+)&post=(\d+)",
    re.IGNORECASE
)


def parse_telegram_link(link: str) -> TelegramParsedLink:
    """
    Parse link Telegram untuk mendapatkan message_id dan channel_id / username.
    
    Raises:
        ValueError: Jika format link tidak valid atau bukan link pesan Telegram.
    """
    clean_link = link.strip()

    # Cek format 1: Private channel (t.me/c/<channel_id>/<message_id>)
    match_private = RE_PRIVATE_CHANNEL.search(clean_link)
    if match_private:
        raw_cid_str, msg_id_str = match_private.groups()
        raw_cid = int(raw_cid_str)
        msg_id = int(msg_id_str)
        
        # Konversi ke standard Telegram -100 prefix untuk supergroup/channel
        full_cid = int(f"-100{raw_cid}")

        return TelegramParsedLink(
            message_id=msg_id,
            raw_channel_id=raw_cid,
            full_channel_id=full_cid,
            is_private_channel=True
        )

    # Cek format 2: Public channel (t.me/<username>/<message_id>)
    # Catatan: hindari false positive dengan path seperti t.me/c/... atau t.me/joinchat/...
    match_public = RE_PUBLIC_CHANNEL.search(clean_link)
    if match_public:
        username, msg_id_str = match_public.groups()
        if username.lower() not in ("c", "joinchat", "addstickers", "invoice", "share"):
            msg_id = int(msg_id_str)
            return TelegramParsedLink(
                message_id=msg_id,
                username=username,
                is_private_channel=False
            )

    # Cek format 3: tg://resolve
    match_tg = RE_TG_PROTOCOL.search(clean_link)
    if match_tg:
        username, msg_id_str = match_tg.groups()
        msg_id = int(msg_id_str)
        return TelegramParsedLink(
            message_id=msg_id,
            username=username,
            is_private_channel=False
        )

    raise ValueError("Format link tidak valid")
