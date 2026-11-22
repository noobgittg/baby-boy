import sys
import os
import asyncio

def _install_fast_event_loop():
    if sys.platform != "win32" and sys.version_info < (3, 14):
        try:
            import uvloop
            uvloop.install()
        except Exception:
            pass

_install_fast_event_loop()
from config import Config
from utils import auto_clean_expired_cache_job
from plugins.core_utils import logger, to_smallcaps, task_manager
from plugins.core_db import mongo
from plugins.core_bot import MMWProBot
from plugins.core_web import WebSupport
from plugins.core_services import (
    run_autodelete_sweeper,
    run_keepalive_worker,
    schedule_24h_restart,
    send_restart_notification,
    RESTART_MARKER_FILE
)
from plugins.compressor import (
    run_task_recovery_watchdog,
    checkpoint_active_tasks_before_shutdown,
)

async def start_bot_and_services(bot: MMWProBot, service_tasks=None):
    service_tasks = service_tasks if service_tasks is not None else []
    try:
        logger.info(f"🍃 {to_smallcaps('connecting to unified mongodb cluster...')}")
        await mongo.connect()
    except Exception as e:
        logger.warning(f"⚠️ {to_smallcaps('mongodb connection warning')}: {e}. {to_smallcaps('continuing with unified in-memory db + fastmemorycache.')}")

    if not Config.BOT_TOKEN:
        logger.warning(f"⚠️ {to_smallcaps('bot_token is not set in environment! web server is running, waiting for bot_token.')}")
        return

    try:
        await bot.start()

        if os.path.exists(RESTART_MARKER_FILE):
            try:
                os.remove(RESTART_MARKER_FILE)
            except Exception:
                pass
            await send_restart_notification(bot)

        service_tasks.extend([
            asyncio.create_task(run_autodelete_sweeper(bot), name="mmw-autodelete"),
            asyncio.create_task(run_keepalive_worker(bot, interval=Config.KEEPALIVE_INTERVAL), name="mmw-keepalive"),
            asyncio.create_task(auto_clean_expired_cache_job(), name="mmw-cache-cleanup"),
            asyncio.create_task(run_task_recovery_watchdog(bot, interval=Config.TASK_WATCHDOG_INTERVAL), name="mmw-task-recovery"),
        ])

        logger.info(f"⚡ {to_smallcaps('mmw all-in-one pro engine running! stream, auto-forward, group/channel manager & auto task recovery ready.')}")
    except Exception as e:
        logger.error(f"❌ {to_smallcaps('error starting bot services')}: {e}", exc_info=True)

async def main():
    logger.info(f"⚡ {to_smallcaps('starting mmw all-in-one pro engine (python 3.12 + pyrofork 2.3.45)...')}")
    bot = MMWProBot()

    web_support = WebSupport(bot)
    server = web_support.get_server()

    service_tasks = []
    startup_task = asyncio.create_task(start_bot_and_services(bot, service_tasks), name="mmw-startup")

    try:
        await server.serve()
    finally:
        logger.info(f"🛑 {to_smallcaps('gracefully stopping engine & checkpointing active tasks...')}")
        try:
            await checkpoint_active_tasks_before_shutdown("server_shutdown")
            await task_manager.cancel_all_background_tasks()
        except Exception:
            pass
        startup_task.cancel()
        await asyncio.gather(startup_task, return_exceptions=True)
        for task in service_tasks:
            task.cancel()
        if service_tasks:
            await asyncio.gather(*service_tasks, return_exceptions=True)
        try:
            await bot.stop()
        except Exception:
            pass
        try:
            await mongo.close()
        except Exception:
            pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info(f"👋 {to_smallcaps('application stopped gracefully.')}")
