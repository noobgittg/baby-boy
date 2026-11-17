import time
import asyncio
import functools
from typing import Union
from pyrogram import Client, utils as pyroutils
from pyrogram.errors import FloodWait, MessageNotModified
from config import Config
from info import LOG_STR, LOG_CHANNEL
from database.users_chats_db import db
from utils import temp
from plugins.core_utils import (
    cache,
    logger,
    to_smallcaps,
    _flood_wait_seconds,
    pro_floodwait_sleep,
    record_floodwait_event,
    rate_limit_manager,
)

pyroutils.MIN_CHAT_ID = -999999999999
pyroutils.MIN_CHANNEL_ID = -100999999999999

_PACED_RPC_NAMES = {
    "SendMessage",
    "SendMedia",
    "SendMultiMedia",
    "ForwardMessages",
}


def _wrap_handler_with_auto_cache(callback):
    if getattr(callback, "_mmw_auto_cache_wrapped", False):
        return callback

    @functools.wraps(callback)
    async def _wrapped(client, update, *args, **kwargs):
        async with cache.response_scope(getattr(callback, "__name__", "handler")):
            return await callback(client, update, *args, **kwargs)

    setattr(_wrapped, "_mmw_auto_cache_wrapped", True)
    return _wrapped


class MMWProBot(Client):
    def __init__(self):
        super().__init__(
            name="MMW_PRO_BOT",
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
            bot_token=Config.BOT_TOKEN,
            in_memory=True,
            workers=Config.WORKERS,
            sleep_threshold=120,
            max_concurrent_transmissions=Config.TG_TRANSMISSION_WORKERS,
            plugins=dict(root="plugins")
        )

    def add_handler(self, handler, group: int = 0):
        if hasattr(handler, "callback") and callable(handler.callback):
            handler.callback = _wrap_handler_with_auto_cache(handler.callback)
        return super().add_handler(handler, group=group)

    async def invoke(self, query, retries: int = 4, timeout: float = 20, sleep_threshold: float = None):
        """Global Centralized Rate-Limit Manager & Pro FloodWait Solver using asyncio.sleep()."""
        rpc_name = type(query).__name__

        if rpc_name in _PACED_RPC_NAMES:
            await rate_limit_manager.wait_for_write_slot(
                rpc_name=rpc_name,
                chat_id=0,
                is_edit=False,
                is_optional_progress=False,
            )

        eff_threshold = sleep_threshold if sleep_threshold is not None else 120
        for attempt in range(5):
            try:
                res = await super().invoke(
                    query,
                    retries=retries,
                    timeout=timeout,
                    sleep_threshold=eff_threshold,
                )
                rate_limit_manager.record_rpc_success()
                return res
            except MessageNotModified:
                rate_limit_manager.record_rpc_success()
                raise
            except FloodWait as fw:
                wait_s = _flood_wait_seconds(fw)
                await rate_limit_manager.notify_floodwait(rpc_name, wait_s)
                if rpc_name == "EditMessage":
                    raise
                if attempt >= 4:
                    logger.error(
                        "❌ [ᴘʀᴏ ғʟᴏᴏᴅᴡᴀɪᴛ sᴏʟᴠᴇʀ] %s exhausted retries after %ss wait",
                        rpc_name,
                        wait_s,
                    )
                    raise
                await pro_floodwait_sleep(wait_s, f"MMWProBot.invoke({rpc_name})")
                continue

    async def start(self):
        t0 = time.perf_counter()
        logger.info(f"🚀 {to_smallcaps('initializing mmw all-in-one pro client engine...')}")
        await db.ensure_indexes()
        b_users, b_chats = await db.get_banned()
        temp.BANNED_USERS = b_users
        temp.BANNED_CHATS = b_chats
        bot_cfg = await db.get_bot_config()
        temp.BOT_CONFIG.update(bot_cfg)
        temp.MAINTENANCE_MODE = bool(bot_cfg.get("maintenance_mode", False))

        await super().start()
        rate_limit_manager.record_rpc_success()
        me = await self.get_me()
        temp.ME, temp.U_NAME, temp.B_NAME = me.id, me.username, me.first_name
        boot_ms = (time.perf_counter() - t0) * 1000.0
        logger.info(f"⚡ {to_smallcaps('bot connected as')} @{me.username} (ɪᴅ={me.id}) ɪɴ {boot_ms:.0f}ᴍs")
        logger.info(f"🚀 {to_smallcaps('adaptive range stream & watermark engine')}: {Config.WATERMARK}")
        logger.info(f"📋 {to_smallcaps('boot configuration summary')}:\n{LOG_STR}")

        try:
            from plugins.seruptime import resume_uptime_monitors
            resumed_cnt = await resume_uptime_monitors(self)
            logger.info(f"🌐 {to_smallcaps('24/7 uptime monitor engine ready')} › ᴀᴄᴛɪᴠᴇ={resumed_cnt}")
        except Exception as e:
            logger.warning(f"⚠️ {to_smallcaps('uptime monitor resume notice')} › {e}")

        if LOG_CHANNEL:
            try:
                await self.send_message(
                    int(LOG_CHANNEL),
                    f"⚡ <b>{to_smallcaps('mmw all-in-one pro bot online & ready!')}</b> 🚀✨\n"
                    f"• 🤖 <b>{to_smallcaps('bot')}:</b> @{me.username} (<code>{me.id}</code>)\n"
                    f"• ⏱️ <b>{to_smallcaps('boot latency')}:</b> <code>{boot_ms:.0f} ms</code>\n"
                    f"• 🛡️ <b>{to_smallcaps('modules')}:</b> <code>/admin</code> │ <code>/helpdesk</code> │ <code>/channelhelp</code> │ <code>/autoset</code> │ <code>/autoforward</code>",
                )
            except Exception as e:
                logger.debug(f"⚠️ {to_smallcaps('log channel startup notify skipped')}: {e}")

    async def stop(self, *args):
        logger.info(f"🛑 {to_smallcaps('shutting down mmw pro bot client...')}")
        try:
            from plugins.compressor import checkpoint_active_tasks_before_shutdown
            await checkpoint_active_tasks_before_shutdown("bot_stop")
        except Exception as e:
            logger.debug(f"Shutdown task checkpoint notice: {e}")
        await super().stop(*args)
        logger.info(f"✅ {to_smallcaps('bot disconnected gracefully.')}")

    async def iter_messages(self, chat_id: Union[int, str], limit: int, offset: int = 0):
        current = offset
        consecutive_floods = 0
        while current < limit:
            batch_size = min(200, limit - current)
            ids = list(range(current, current + batch_size))
            if not ids:
                break
            try:
                messages = await self.get_messages(chat_id, ids)
                rate_limit_manager.record_rpc_success()
                consecutive_floods = 0
            except FloodWait as e:
                wait = _flood_wait_seconds(e) + 1
                wait = min(max(wait, 1), 120)
                consecutive_floods += 1
                await pro_floodwait_sleep(wait, f"iter_messages(x{consecutive_floods})")
                if consecutive_floods >= 8:
                    break
                continue
            except Exception:
                break
            if not isinstance(messages, list):
                messages = [messages]
            for msg in messages:
                if msg:
                    yield msg
            current += len(ids)
            await asyncio.sleep(0.15)
