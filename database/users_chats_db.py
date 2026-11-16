import logging
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
try:
    from cachetools import TTLCache
except ImportError:
    class TTLCache(dict):
        def __init__(self, maxsize=2000, ttl=1800):
            super().__init__()
            self.maxsize = maxsize
            self.ttl = ttl

try:
    import motor.motor_asyncio
except ImportError:
    motor = None
from info import (
    DATABASE_URI,
    SECONDDB_URI,
    DATABASE_NAME,
    MELCOW_NEW_USERS,
    PROTECT_CONTENT,
    FAST_CACHE_ENABLED,
    LOG_CHANNEL,
)

logger = logging.getLogger("MMWDatabase")


class Database:
    def __init__(self, database_name: str):
        uri = DATABASE_URI or SECONDDB_URI
        self._uri = uri
        try:
            self._client = (
                motor.motor_asyncio.AsyncIOMotorClient(
                    uri,
                    maxPoolSize=150,
                    minPoolSize=5,
                    serverSelectionTimeoutMS=2500,
                    connectTimeoutMS=2500,
                    retryReads=True,
                    retryWrites=True,
                )
                if (uri and motor is not None)
                else None
            )
        except Exception as e:
            logger.warning("⚠️ ᴍᴏɴɢᴏ ᴄʟɪᴇɴᴛ ɪɴɪᴛ ᴡᴀʀɴɪɴɢ › %s", e)
            self._client = None

        self.db = self._client[database_name] if self._client is not None else None
        self.col = self.db.users if self.db is not None else None
        self.grp = self.db.groups if self.db is not None else None
        self.chnl = self.db.channels if self.db is not None else None
        self.adm = self.db.admins if self.db is not None else None
        self.hd_groups = self.db.helpdesk_groups if self.db is not None else None
        self.hd_warns = self.db.helpdesk_warnings if self.db is not None else None
        self.hd_logs = self.db.helpdesk_modlogs if self.db is not None else None
        self.notes_col = self.db.group_notes if self.db is not None else None
        self.filters_col = self.db.group_filters if self.db is not None else None
        self.blacklist_col = self.db.group_blacklist if self.db is not None else None
        self.afk_col = self.db.user_afk if self.db is not None else None
        self.autofwd_col = self.db.autoforwards if self.db is not None else None

        # High-speed L1 in-memory caches & complete offline fallbacks
        self._mem_users: Dict[int, Dict[str, Any]] = {}
        self._mem_groups: Dict[int, Dict[str, Any]] = {}
        self._mem_hd_grp: TTLCache = TTLCache(maxsize=2000, ttl=1800)
        self._mem_chnl_cfg: TTLCache = TTLCache(maxsize=2000, ttl=1800)
        self._mem_user_conn: Dict[int, int] = {}
        self._mem_hd_warns: Dict[Tuple[int, int], Dict[str, Any]] = {}
        self._mem_hd_logs: Dict[int, List[Dict[str, Any]]] = {}
        self._mem_notes: Dict[Tuple[int, str], Dict[str, Any]] = {}
        self._mem_filters: Dict[Tuple[int, str], Dict[str, Any]] = {}
        self._mem_blacklist: Dict[int, Dict[str, Any]] = {}
        self._mem_afk: Dict[int, Dict[str, Any]] = {}
        self._mem_autofwd: Dict[str, Dict[str, Any]] = {}
        self._mem_config: Dict[str, Any] = {
            "_id": "bot_config",
            "maintenance_mode": False,
            "protect_content": bool(PROTECT_CONTENT),
            "welcome_new_users": bool(MELCOW_NEW_USERS),
            "auto_clean_service": False,
            "global_antispam": False,
            "log_new_users": True,
            "log_new_groups": True,
            "public_uptime": True,
            "channel_auto_sign": False,
            "auto_stream_global": True,
            "auto_caption_global": True,
            "auto_forward_global": True,
            "fast_cache": bool(FAST_CACHE_ENABLED),
            "total_commands_run": 0,
            "total_broadcasts": 0,
        }

    async def ensure_indexes(self):
        if self.db is None:
            logger.warning("⚠️ ᴍᴏɴɢᴏᴅʙ ᴜʀɪ ɴᴏᴛ ᴄᴏɴғɪɢᴜʀᴇᴅ › ʀᴜɴɴɪɴɢ ɪɴ ʟ1 ᴍᴇᴍᴏʀʏ-ᴏɴʟʏ ᴍᴏᴅᴇ")
            return
        try:
            t0 = time.perf_counter()
            await self.col.create_index("id", background=True)
            await self.grp.create_index("id", background=True)
            await self.chnl.create_index("chat_id", unique=True, background=True)
            await self.hd_groups.create_index("chat_id", unique=True, background=True)
            await self.hd_warns.create_index([("chat_id", 1), ("user_id", 1)], unique=True, background=True)
            await self.hd_logs.create_index([("chat_id", 1), ("timestamp", -1)], background=True)
            await self.notes_col.create_index([("chat_id", 1), ("name", 1)], unique=True, background=True)
            await self.filters_col.create_index([("chat_id", 1), ("trigger", 1)], unique=True, background=True)
            await self.blacklist_col.create_index("chat_id", unique=True, background=True)
            await self.afk_col.create_index("user_id", unique=True, background=True)
            await self.autofwd_col.create_index("rule_id", unique=True, background=True)
            await self.autofwd_col.create_index("source_chat_id", background=True)
            elapsed = (time.perf_counter() - t0) * 1000.0
            logger.info("✅ ᴍᴏɴɢᴏᴅʙ ɪɴᴅᴇxᴇs ᴠᴇʀɪғɪᴇᴅ (11 ᴄᴏʟʟᴇᴄᴛɪᴏɴs) ɪɴ %.1f ᴍs", elapsed)
        except Exception as e:
            logger.debug("⚠️ ᴅᴀᴛᴀʙᴀsᴇ ɪɴᴅᴇx ᴄʀᴇᴀᴛɪᴏɴ ɴᴏᴛɪᴄᴇ › %s", e)

    def default_helpdesk_group_config(self, chat_id: int) -> Dict[str, Any]:
        return {
            "chat_id": int(chat_id),
            "welcome_enabled": bool(MELCOW_NEW_USERS),
            "welcome_text": "👋 ʜᴇʏ {mention}, ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ <b>{chat_title}</b>! ✨",
            "goodbye_enabled": False,
            "goodbye_text": "👋 ɢᴏᴏᴅʙʏᴇ {mention}, ᴡᴇ ᴡɪʟʟ ᴍɪss ʏᴏᴜ! 🌟",
            "rules_text": (
                "📜 <b>ɢʀᴏᴜᴘ ʀᴜʟᴇs:</b>\n"
                "1. 🤝 ʙᴇ ʀᴇsᴘᴇᴄᴛғᴜʟ ᴛᴏ ᴀʟʟ ᴍᴇᴍʙᴇʀs.\n"
                "2. 🚫 ɴᴏ sᴘᴀᴍ, ғʟᴏᴏᴅɪɴɢ, ᴏʀ ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴘʀᴏᴍᴏᴛɪᴏɴs.\n"
                "3. 🛡️ ғᴏʟʟᴏᴡ ᴀᴅᴍɪɴ ɪɴsᴛʀᴜᴄᴛɪᴏɴs."
            ),
            "log_channel": int(LOG_CHANNEL) if LOG_CHANNEL else 0,
            "warn_limit": 3,
            "warn_action": "ban",
            "antiflood_enabled": False,
            "flood_limit": 5,
            "flood_window": 5,
            "flood_action": "mute",
            "antidup_enabled": False,
            "antispam_enabled": False,
            "spam_limit": 3,
            "spam_window": 30,
            "autoban_stats": {},
            "raid_mode": False,
            "emergency_mode": False,
            "verify_new_members": False,
            "clean_service": False,
            "clean_commands": False,
            "nightmode_enabled": False,
            "auto_react_emoji": "",
            "blacklist_action": "warn",
            "locks": {
                "all": False,
                "button": False,
                "emoji": False,
                "forward": False,
                "url": False,
                "username": False,
                "msg": False,
                "media": False,
                "document": False,
                "photo": False,
                "video": False,
                "audio": False,
                "voice": False,
                "links": False,
                "stickers": False,
                "gifs": False,
                "polls": False,
                "inline": False,
                "bots": False,
                "contact": False,
            },
            "approved_users": [],
            "trusted_users": [],
            "pending_requests": [],
            "updated_at": time.time(),
        }

    def default_channel_config(self, chat_id: int, title: str = "Channel") -> Dict[str, Any]:
        return {
            "chat_id": int(chat_id),
            "title": str(title or "Channel"),
            "auto_tag_enabled": False,
            "header_text": "",
            "footer_text": "",
            "button_text": "",
            "button_url": "",
            "protect_posts": False,
            "silent_posts": False,
            "disable_preview": True,
            "auto_caption_enabled": True,
            "custom_caption": "",
            "auto_stream_enabled": False,
            "auto_react_emoji": "",
            "auto_delete_seconds": 0,
            "log_channel": int(LOG_CHANNEL) if LOG_CHANNEL else 0,
            "connected_admins": [],
            "updated_at": time.time(),
        }

    def new_user(self, id: int, name: str) -> Dict[str, Any]:
        return {
            "id": int(id),
            "name": name or "User",
            "joined_at": time.time(),
            "ban_status": {
                "is_banned": False,
                "ban_reason": "",
            },
        }

    def new_group(self, id: int, title: str) -> Dict[str, Any]:
        return {
            "id": int(id),
            "title": title or "Group",
            "added_at": time.time(),
            "chat_status": {
                "is_disabled": False,
                "reason": "",
            },
        }

    # ─── Generic CRUD Helpers ─────────────────────────────────────────────
    async def create_data(self, collection_name: str, data: dict):
        if self.db is None:
            return None
        try:
            return await self.db[collection_name].insert_one(data)
        except Exception:
            return None

    async def get_data(self, collection_name: str, query: dict):
        if self.db is None:
            return None
        try:
            return await self.db[collection_name].find_one(query)
        except Exception:
            return None

    async def update_data(self, collection_name: str, query: dict, updates: dict, upsert: bool = False):
        if self.db is None:
            return None
        try:
            return await self.db[collection_name].update_one(query, {"$set": updates}, upsert=upsert)
        except Exception:
            return None

    async def delete_data(self, collection_name: str, query: dict):
        if self.db is None:
            return None
        try:
            return await self.db[collection_name].delete_one(query)
        except Exception:
            return None

    # ─── Users Management ─────────────────────────────────────────────────
    async def add_user(self, id: int, name: str):
        uid = int(id)
        if uid not in self._mem_users:
            self._mem_users[uid] = self.new_user(uid, name)
        if self.col is not None:
            try:
                await self.col.update_one({"id": uid}, {"$setOnInsert": self._mem_users[uid]}, upsert=True)
            except Exception as e:
                logger.debug("⚠️ ᴀᴅᴅ_ᴜsᴇʀ ғᴀʟʟʙᴀᴄᴋ › %s", e)
        try:
            from plugins.core_db import user_repo
            await user_repo.get_or_create(uid, name or "User")
        except Exception:
            pass
        logger.info("👤 ᴅʙ ᴜsᴇʀ ʀᴇɢɪsᴛᴇʀᴇᴅ › ɪᴅ=%s ɴᴀᴍᴇ=%s", uid, name)

    async def is_user_exist(self, id: int) -> bool:
        uid = int(id)
        if uid in self._mem_users:
            return True
        if self.col is not None:
            try:
                user = await self.col.find_one({"id": uid}, {"_id": 1})
                if user:
                    self._mem_users[uid] = self.new_user(uid, "User")
                    return True
            except Exception:
                pass
        return False

    async def total_users_count(self) -> int:
        mem_cnt = len(self._mem_users)
        if self.col is not None:
            try:
                db_cnt = await self.col.count_documents({})
                mem_cnt = max(mem_cnt, int(db_cnt))
            except Exception:
                pass
        try:
            from plugins.core_db import user_repo
            repo_cnt = await user_repo.get_total_users()
            mem_cnt = max(mem_cnt, int(repo_cnt))
        except Exception:
            pass
        return mem_cnt

    async def remove_ban(self, id: int):
        uid = int(id)
        ban_status = {"is_banned": False, "ban_reason": ""}
        user_doc = self._mem_users.setdefault(uid, self.new_user(uid, "User"))
        user_doc["ban_status"] = ban_status
        if self.col is not None:
            try:
                await self.col.update_one({"id": uid}, {"$set": {"ban_status": ban_status}}, upsert=True)
            except Exception:
                pass
        try:
            from plugins.core_db import user_repo
            await user_repo.unban_user(uid)
        except Exception:
            pass
        logger.info("🔓 ᴅʙ ᴜsᴇʀ ᴜɴʙᴀɴɴᴇᴅ › ɪᴅ=%s", uid)

    async def ban_user(self, user_id: int, ban_reason: str = "ɴᴏ ʀᴇᴀsᴏɴ"):
        uid = int(user_id)
        ban_status = {"is_banned": True, "ban_reason": ban_reason}
        user_doc = self._mem_users.setdefault(uid, self.new_user(uid, "User"))
        user_doc["ban_status"] = ban_status
        if self.col is not None:
            try:
                await self.col.update_one({"id": uid}, {"$set": {"ban_status": ban_status}}, upsert=True)
            except Exception:
                pass
        try:
            from plugins.core_db import user_repo
            await user_repo.ban_user(uid)
        except Exception:
            pass
        logger.info("🚫 ᴅʙ ᴜsᴇʀ ʙᴀɴɴᴇᴅ › ɪᴅ=%s ʀᴇᴀsᴏɴ=%s", uid, ban_reason)

    async def get_ban_status(self, id: int) -> Dict[str, Any]:
        uid = int(id)
        default = {"is_banned": False, "ban_reason": ""}
        if uid in self._mem_users and "ban_status" in self._mem_users[uid]:
            return dict(self._mem_users[uid]["ban_status"])
        if self.col is not None:
            try:
                user = await self.col.find_one({"id": uid})
                if user and "ban_status" in user:
                    return user.get("ban_status", default)
            except Exception:
                pass
        return default

    async def get_all_users(self) -> List[Dict[str, Any]]:
        by_id: Dict[int, Dict[str, Any]] = {uid: dict(doc) for uid, doc in self._mem_users.items()}
        if self.col is not None:
            try:
                docs = await self.col.find({}).to_list(length=100000)
                for d in docs:
                    if "id" in d:
                        by_id[int(d["id"])] = d
            except Exception:
                pass
        try:
            from plugins.core_db import user_repo
            repo_users = await user_repo.get_all_users()
            for ru in repo_users:
                uid = int(ru.get("user_id") or ru.get("id") or 0)
                if uid and uid not in by_id:
                    by_id[uid] = {"id": uid, "name": ru.get("first_name", "User")}
        except Exception:
            pass
        return list(by_id.values())

    async def delete_user(self, user_id: int):
        uid = int(user_id)
        self._mem_users.pop(uid, None)
        if self.col is not None:
            try:
                await self.col.delete_many({"id": uid})
            except Exception:
                pass
        try:
            from plugins.core_db import user_repo
            await user_repo.delete_user(uid)
        except Exception:
            pass
        logger.info("🗑️ ᴅʙ ᴜsᴇʀ ᴅᴇʟᴇᴛᴇᴅ › ɪᴅ=%s", uid)

    async def get_banned(self) -> Tuple[List[int], List[int]]:
        b_users = {
            uid for uid, doc in self._mem_users.items()
            if doc.get("ban_status", {}).get("is_banned")
        }
        b_chats = {
            cid for cid, doc in self._mem_groups.items()
            if doc.get("chat_status", {}).get("is_disabled")
        }
        if self.col is not None and self.grp is not None:
            try:
                users = self.col.find({"ban_status.is_banned": True}, {"id": 1})
                chats = self.grp.find({"chat_status.is_disabled": True}, {"id": 1})
                async for chat in chats:
                    if "id" in chat:
                        b_chats.add(int(chat["id"]))
                async for user in users:
                    if "id" in user:
                        b_users.add(int(user["id"]))
            except Exception as e:
                logger.debug("⚠️ ɢᴇᴛ_ʙᴀɴɴᴇᴅ ᴡᴀʀɴɪɴɢ › %s", e)
        return sorted(b_users), sorted(b_chats)

    # ─── Groups Management ────────────────────────────────────────────────
    async def add_chat(self, chat: int, title: str):
        cid = int(chat)
        chat_doc = self.new_group(cid, title)
        self._mem_groups.setdefault(cid, chat_doc)
        if self.grp is not None:
            try:
                await self.grp.update_one({"id": cid}, {"$setOnInsert": chat_doc}, upsert=True)
            except Exception:
                pass
        try:
            from plugins.core_db import chat_repo
            await chat_repo.get_or_create_chat(cid, title or "Group", "group")
        except Exception:
            pass
        logger.info("💬 ᴅʙ ɢʀᴏᴜᴘ ʀᴇɢɪsᴛᴇʀᴇᴅ › ɪᴅ=%s ᴛɪᴛʟᴇ=%s", cid, title)

    async def get_chat(self, chat: int):
        cid = int(chat)
        if cid in self._mem_groups:
            return self._mem_groups[cid].get("chat_status", {"is_disabled": False, "reason": ""})
        if self.grp is not None:
            try:
                chat_doc = await self.grp.find_one({"id": cid})
                if chat_doc:
                    self._mem_groups[cid] = chat_doc
                    return chat_doc.get("chat_status", {"is_disabled": False, "reason": ""})
            except Exception:
                pass
        return False

    async def re_enable_chat(self, id: int):
        cid = int(id)
        chat_status = {"is_disabled": False, "reason": ""}
        doc = self._mem_groups.setdefault(cid, self.new_group(cid, "Group"))
        doc["chat_status"] = chat_status
        if self.grp is not None:
            try:
                await self.grp.update_one({"id": cid}, {"$set": {"chat_status": chat_status}}, upsert=True)
            except Exception:
                pass
        logger.info("🟢 ᴅʙ ɢʀᴏᴜᴘ ʀᴇ-ᴇɴᴀʙʟᴇᴅ › ɪᴅ=%s", cid)

    async def update_settings(self, id: int, settings: Dict[str, Any]):
        cid = int(id)
        doc = self._mem_groups.setdefault(cid, self.new_group(cid, "Group"))
        doc["settings"] = dict(settings)
        if self.grp is not None:
            try:
                await self.grp.update_one({"id": cid}, {"$set": {"settings": settings}}, upsert=True)
            except Exception:
                pass

    async def get_settings(self, id: int) -> Dict[str, Any]:
        cid = int(id)
        default = {
            "file_secure": PROTECT_CONTENT,
            "welcome": MELCOW_NEW_USERS,
        }
        if cid in self._mem_groups and isinstance(self._mem_groups[cid].get("settings"), dict):
            merged = dict(default)
            merged.update(self._mem_groups[cid]["settings"])
            return merged
        if self.grp is not None:
            try:
                chat = await self.grp.find_one({"id": cid})
                if chat and isinstance(chat.get("settings"), dict):
                    merged = dict(default)
                    merged.update(chat["settings"])
                    return merged
            except Exception:
                pass
        return dict(default)

    async def disable_chat(self, chat: int, reason: str = "ɴᴏ ʀᴇᴀsᴏɴ"):
        cid = int(chat)
        chat_status = {"is_disabled": True, "reason": reason}
        doc = self._mem_groups.setdefault(cid, self.new_group(cid, "Group"))
        doc["chat_status"] = chat_status
        if self.grp is not None:
            try:
                await self.grp.update_one({"id": cid}, {"$set": {"chat_status": chat_status}}, upsert=True)
            except Exception:
                pass
        logger.info("🚫 ᴅʙ ɢʀᴏᴜᴘ ᴅɪsᴀʙʟᴇᴅ › ɪᴅ=%s ʀᴇᴀsᴏɴ=%s", cid, reason)

    async def total_chat_count(self) -> int:
        mem_cnt = len(self._mem_groups)
        if self.grp is not None:
            try:
                db_cnt = await self.grp.count_documents({})
                mem_cnt = max(mem_cnt, int(db_cnt))
            except Exception:
                pass
        return mem_cnt

    async def get_all_chats(self) -> List[Dict[str, Any]]:
        by_id: Dict[int, Dict[str, Any]] = {cid: dict(doc) for cid, doc in self._mem_groups.items()}
        if self.grp is not None:
            try:
                docs = await self.grp.find({}).to_list(length=100000)
                for d in docs:
                    if "id" in d:
                        by_id[int(d["id"])] = d
            except Exception:
                pass
        return list(by_id.values())

    # ─── Channels Management & Channel Helper Config ──────────────────────
    async def get_channel_config(self, chat_id: int, title: str = "Channel") -> Dict[str, Any]:
        cid = int(chat_id)
        default = self.default_channel_config(cid, title=title)
        if cid in self._mem_chnl_cfg:
            cached = dict(self._mem_chnl_cfg[cid])
            if title and title != "Channel":
                cached["title"] = title
            return cached
        if self.chnl is not None:
            try:
                doc = await self.chnl.find_one({"chat_id": cid})
                if doc:
                    merged = dict(default)
                    for k, v in doc.items():
                        if k != "_id":
                            merged[k] = v
                    if title and title != "Channel":
                        merged["title"] = title
                    self._mem_chnl_cfg[cid] = merged
                    return dict(merged)
            except Exception as e:
                logger.debug("⚠️ ɢᴇᴛ_ᴄʜᴀɴɴᴇʟ_ᴄᴏɴғɪɢ ᴇʀʀᴏʀ › %s", e)
        self._mem_chnl_cfg[cid] = dict(default)
        return dict(default)

    async def update_channel_config(self, chat_id: int, updates: Dict[str, Any], title: str = "") -> Dict[str, Any]:
        cid = int(chat_id)
        current = await self.get_channel_config(cid, title=title or "Channel")
        current.update(updates)
        if title:
            current["title"] = title
        current["updated_at"] = time.time()
        self._mem_chnl_cfg[cid] = dict(current)
        if self.chnl is not None:
            try:
                payload = {k: v for k, v in current.items() if k != "_id"}
                await self.chnl.update_one({"chat_id": cid}, {"$set": payload}, upsert=True)
            except Exception as e:
                logger.debug("⚠️ ᴜᴘᴅᴀᴛᴇ_ᴄʜᴀɴɴᴇʟ_ᴄᴏɴғɪɢ ᴇʀʀᴏʀ › %s", e)
        logger.info("📢 ᴄʜᴀɴɴᴇʟ ᴄᴏɴғɪɢ ᴜᴘᴅᴀᴛᴇᴅ › ᴄʜᴀᴛ=%s ᴋᴇʏs=%s", cid, list(updates.keys()))
        return dict(current)

    async def connect_user_channel(self, user_id: int, chat_id: int, title: str = "Channel") -> Dict[str, Any]:
        uid, cid = int(user_id), int(chat_id)
        self._mem_user_conn[uid] = cid
        cfg = await self.get_channel_config(cid, title=title)
        admins_list = list(cfg.get("connected_admins", []))
        if uid not in admins_list:
            admins_list.append(uid)
        cfg = await self.update_channel_config(cid, {"connected_admins": admins_list}, title=title)
        if self.col is not None:
            try:
                await self.col.update_one(
                    {"id": uid},
                    {"$set": {"connected_channel": cid}},
                    upsert=True,
                )
            except Exception:
                pass
        logger.info("🔗 ᴜsᴇʀ ᴄᴏɴɴᴇᴄᴛᴇᴅ ᴛᴏ ᴄʜᴀɴɴᴇʟ › ᴜsᴇʀ=%s ᴄʜᴀɴɴᴇʟ=%s (%s)", uid, cid, title)
        return cfg

    async def get_user_connected_channel(self, user_id: int) -> Optional[int]:
        uid = int(user_id)
        if uid in self._mem_user_conn:
            return self._mem_user_conn[uid]
        if self.col is not None:
            try:
                doc = await self.col.find_one({"id": uid}, {"connected_channel": 1})
                if doc and doc.get("connected_channel"):
                    cid = int(doc["connected_channel"])
                    self._mem_user_conn[uid] = cid
                    return cid
            except Exception:
                pass
        return None

    async def disconnect_user_channel(self, user_id: int) -> bool:
        uid = int(user_id)
        self._mem_user_conn.pop(uid, None)
        if self.col is not None:
            try:
                await self.col.update_one({"id": uid}, {"$unset": {"connected_channel": ""}})
            except Exception:
                pass
        logger.info("🔌 ᴜsᴇʀ ᴅɪsᴄᴏɴɴᴇᴄᴛᴇʟ ᴄʜᴀɴɴᴇʟ › ᴜsᴇʀ=%s", uid)
        return True

    async def get_user_channels(self, user_id: int) -> List[Dict[str, Any]]:
        uid = int(user_id)
        out = []
        if self.chnl is not None:
            try:
                cursor = self.chnl.find({"connected_admins": uid})
                out = await cursor.to_list(length=50)
            except Exception:
                pass
        if not out:
            for cid, cfg in list(self._mem_chnl_cfg.items()):
                if uid in cfg.get("connected_admins", []):
                    out.append(cfg)
        return out

    async def total_channels_count(self) -> int:
        if self.chnl is not None:
            try:
                cnt = await self.chnl.count_documents({})
                return max(int(cnt), len(self._mem_chnl_cfg))
            except Exception:
                pass
        return len(self._mem_chnl_cfg)

    async def get_all_channels(self) -> List[Dict[str, Any]]:
        if self.chnl is not None:
            try:
                docs = await self.chnl.find({}).to_list(length=None)
                if docs:
                    return docs
            except Exception:
                pass
        return list(self._mem_chnl_cfg.values())

    # ─── Auto-Forward Rules Engine ────────────────────────────────────────
    async def add_autoforward_rule(
        self,
        user_id: int,
        source_chat_id: int,
        target_chat_id: int,
        source_title: str = "Source",
        target_title: str = "Target",
        copy_mode: bool = True,
        custom_caption: str = "",
        header_text: str = "",
        footer_text: str = "",
        strip_buttons: bool = False,
        media_only: bool = False,
    ) -> Dict[str, Any]:
        rule_id = f"{int(source_chat_id)}_{int(target_chat_id)}"
        doc = {
            "rule_id": rule_id,
            "user_id": int(user_id),
            "source_chat_id": int(source_chat_id),
            "target_chat_id": int(target_chat_id),
            "source_title": str(source_title or source_chat_id),
            "target_title": str(target_title or target_chat_id),
            "enabled": True,
            "copy_mode": bool(copy_mode),
            "custom_caption": str(custom_caption or ""),
            "header_text": str(header_text or ""),
            "footer_text": str(footer_text or ""),
            "strip_buttons": bool(strip_buttons),
            "media_only": bool(media_only),
            "forwarded_count": 0,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        self._mem_autofwd[rule_id] = dict(doc)
        if self.autofwd_col is not None:
            try:
                await self.autofwd_col.update_one({"rule_id": rule_id}, {"$set": doc}, upsert=True)
            except Exception as e:
                logger.debug("⚠️ ᴀᴅᴅ_ᴀᴜᴛᴏғᴏʀᴡᴀʀᴅ_ʀᴜʟᴇ ᴡᴀʀɴ › %s", e)
        logger.info("🔁 ᴀᴜᴛᴏ-ғᴏʀᴡᴀʀᴅ ʀᴜʟᴇ sᴀᴠᴇᴅ › %s ➔ %s (ʙʏ %s)", source_chat_id, target_chat_id, user_id)
        return dict(doc)

    async def get_autoforward_rule(self, rule_id: str) -> Optional[Dict[str, Any]]:
        if rule_id in self._mem_autofwd:
            return dict(self._mem_autofwd[rule_id])
        if self.autofwd_col is not None:
            try:
                doc = await self.autofwd_col.find_one({"rule_id": str(rule_id)})
                if doc:
                    clean = {k: v for k, v in doc.items() if k != "_id"}
                    self._mem_autofwd[rule_id] = clean
                    return dict(clean)
            except Exception:
                pass
        return None

    async def update_autoforward_rule(self, rule_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        curr = await self.get_autoforward_rule(rule_id)
        if not curr:
            return None
        curr.update(updates)
        curr["updated_at"] = time.time()
        self._mem_autofwd[rule_id] = dict(curr)
        if self.autofwd_col is not None:
            try:
                payload = {k: v for k, v in curr.items() if k != "_id"}
                await self.autofwd_col.update_one({"rule_id": rule_id}, {"$set": payload}, upsert=True)
            except Exception:
                pass
        return dict(curr)

    async def remove_autoforward_rule(self, rule_id: str) -> bool:
        existed = self._mem_autofwd.pop(str(rule_id), None) is not None
        if self.autofwd_col is not None:
            try:
                res = await self.autofwd_col.delete_one({"rule_id": str(rule_id)})
                existed = existed or bool(res.deleted_count)
            except Exception:
                pass
        logger.info("🗑️ ᴀᴜᴛᴏ-ғᴏʀᴡᴀʀᴅ ʀᴜʟᴇ ʀᴇᴍᴏᴠᴇᴅ › %s", rule_id)
        return existed

    async def get_user_autoforward_rules(self, user_id: int) -> List[Dict[str, Any]]:
        uid = int(user_id)
        by_id: Dict[str, Dict[str, Any]] = {
            rid: dict(r) for rid, r in self._mem_autofwd.items() if r.get("user_id") == uid
        }
        if self.autofwd_col is not None:
            try:
                cursor = self.autofwd_col.find({"user_id": uid})
                async for doc in cursor:
                    rid = str(doc.get("rule_id", ""))
                    if rid:
                        clean = {k: v for k, v in doc.items() if k != "_id"}
                        self._mem_autofwd[rid] = clean
                        by_id[rid] = clean
            except Exception:
                pass
        return list(by_id.values())

    async def get_matching_autoforward_rules(self, source_chat_id: int) -> List[Dict[str, Any]]:
        scid = int(source_chat_id)
        by_id: Dict[str, Dict[str, Any]] = {
            rid: dict(r)
            for rid, r in self._mem_autofwd.items()
            if int(r.get("source_chat_id", 0)) == scid and r.get("enabled", True)
        }
        if self.autofwd_col is not None:
            try:
                cursor = self.autofwd_col.find({"source_chat_id": scid, "enabled": True})
                async for doc in cursor:
                    rid = str(doc.get("rule_id", ""))
                    if rid:
                        clean = {k: v for k, v in doc.items() if k != "_id"}
                        self._mem_autofwd[rid] = clean
                        by_id[rid] = clean
            except Exception:
                pass
        return list(by_id.values())

    async def get_all_autoforward_rules(self) -> List[Dict[str, Any]]:
        by_id: Dict[str, Dict[str, Any]] = {rid: dict(r) for rid, r in self._mem_autofwd.items()}
        if self.autofwd_col is not None:
            try:
                cursor = self.autofwd_col.find({})
                async for doc in cursor:
                    rid = str(doc.get("rule_id", ""))
                    if rid:
                        clean = {k: v for k, v in doc.items() if k != "_id"}
                        self._mem_autofwd[rid] = clean
                        by_id[rid] = clean
            except Exception:
                pass
        return list(by_id.values())

    async def clear_user_autoforwards(self, user_id: int) -> int:
        uid = int(user_id)
        rids = [rid for rid, r in self._mem_autofwd.items() if r.get("user_id") == uid]
        for rid in rids:
            self._mem_autofwd.pop(rid, None)
        cnt = len(rids)
        if self.autofwd_col is not None:
            try:
                res = await self.autofwd_col.delete_many({"user_id": uid})
                cnt = max(cnt, int(res.deleted_count))
            except Exception:
                pass
        return cnt

    # ─── Helpdesk Group Config & Locks ────────────────────────────────────
    async def get_helpdesk_config(self, chat_id: int) -> Dict[str, Any]:
        cid = int(chat_id)
        default = self.default_helpdesk_group_config(cid)
        if cid in self._mem_hd_grp:
            return dict(self._mem_hd_grp[cid])
        if self.hd_groups is not None:
            try:
                doc = await self.hd_groups.find_one({"chat_id": cid})
                if doc:
                    merged = dict(default)
                    for k, v in doc.items():
                        if k == "_id":
                            continue
                        if k == "locks" and isinstance(v, dict):
                            merged_locks = dict(default["locks"])
                            merged_locks.update(v)
                            merged["locks"] = merged_locks
                        else:
                            merged[k] = v
                    self._mem_hd_grp[cid] = merged
                    return dict(merged)
            except Exception as e:
                logger.debug("⚠️ ɢᴇᴛ_ʜᴇʟᴘᴅᴇsᴋ_ᴄᴏɴғɪɢ ᴇʀʀᴏʀ › %s", e)
        self._mem_hd_grp[cid] = dict(default)
        return dict(default)

    async def update_helpdesk_config(self, chat_id: int, updates: Dict[str, Any]) -> Dict[str, Any]:
        cid = int(chat_id)
        current = await self.get_helpdesk_config(cid)
        for k, v in updates.items():
            if k == "locks" and isinstance(v, dict):
                locks_copy = dict(current.get("locks", {}))
                locks_copy.update(v)
                current["locks"] = locks_copy
            else:
                current[k] = v
        current["updated_at"] = time.time()
        self._mem_hd_grp[cid] = dict(current)
        if self.hd_groups is not None:
            try:
                payload = {k: v for k, v in current.items() if k != "_id"}
                await self.hd_groups.update_one(
                    {"chat_id": cid},
                    {"$set": payload},
                    upsert=True,
                )
            except Exception as e:
                logger.debug("⚠️ ᴜᴘᴅᴀᴛᴇ_ʜᴇʟᴘᴅᴇsᴋ_ᴄᴏɴғɪɢ ᴇʀʀᴏʀ › %s", e)
        logger.info("⚙️ ʜᴇʟᴘᴅᴇsᴋ ᴄᴏɴғɪɢ ᴜᴘᴅᴀᴛᴇᴅ › ᴄʜᴀᴛ=%s ᴋᴇʏs=%s", cid, list(updates.keys()))
        return dict(current)

    async def set_group_lock(self, chat_id: int, lock_type: str, locked: bool) -> Dict[str, Any]:
        cfg = await self.get_helpdesk_config(chat_id)
        locks = dict(cfg.get("locks", {}))
        if lock_type == "all":
            for lk in list(locks.keys()):
                locks[lk] = bool(locked)
        else:
            locks[lock_type] = bool(locked)
        logger.info("🔐 ɢʀᴏᴜᴘ ʟᴏᴄᴋ ᴜᴘᴅᴀᴛᴇᴅ › ᴄʜᴀᴛ=%s ᴛʏᴘᴇ=%s ʟᴏᴄᴋᴇᴅ=%s", chat_id, lock_type, locked)
        return await self.update_helpdesk_config(chat_id, {"locks": locks})

    async def add_approved_user(self, chat_id: int, user_id: int) -> bool:
        cfg = await self.get_helpdesk_config(chat_id)
        approved = list(cfg.get("approved_users", []))
        uid = int(user_id)
        if uid not in approved:
            approved.append(uid)
            await self.update_helpdesk_config(chat_id, {"approved_users": approved})
            logger.info("✅ ᴜsᴇʀ ᴀᴘᴘʀᴏᴠᴇᴅ › ᴄʜᴀᴛ=%s ᴜsᴇʀ=%s", chat_id, uid)
            return True
        return False

    async def remove_approved_user(self, chat_id: int, user_id: int) -> bool:
        cfg = await self.get_helpdesk_config(chat_id)
        approved = list(cfg.get("approved_users", []))
        uid = int(user_id)
        if uid in approved:
            approved.remove(uid)
            await self.update_helpdesk_config(chat_id, {"approved_users": approved})
            logger.info("🚫 ᴜsᴇʀ ᴜɴᴀᴘᴘʀᴏᴠᴇᴅ › ᴄʜᴀᴛ=%s ᴜsᴇʀ=%s", chat_id, uid)
            return True
        return False

    async def is_user_approved(self, chat_id: int, user_id: int) -> bool:
        cfg = await self.get_helpdesk_config(chat_id)
        uid = int(user_id)
        return uid in cfg.get("approved_users", []) or uid in cfg.get("trusted_users", [])

    # ─── Warnings & Mod Logs ──────────────────────────────────────────────
    async def add_warning(
        self,
        chat_id: int,
        user_id: int,
        reason: str,
        admin_id: int,
    ) -> Tuple[int, List[Dict[str, Any]]]:
        cid, uid = int(chat_id), int(user_id)
        entry = {
            "reason": str(reason or "ɴᴏ ʀᴇᴀsᴏɴ"),
            "admin_id": int(admin_id),
            "timestamp": time.time(),
        }
        doc = await self.get_warnings(cid, uid)
        reasons = list(doc.get("reasons", []))
        reasons.append(entry)
        count = len(reasons)
        updated_doc = {
            "chat_id": cid,
            "user_id": uid,
            "count": count,
            "reasons": reasons,
            "updated_at": time.time(),
        }
        self._mem_hd_warns[(cid, uid)] = updated_doc
        if self.hd_warns is not None:
            try:
                await self.hd_warns.update_one(
                    {"chat_id": cid, "user_id": uid},
                    {"$set": updated_doc},
                    upsert=True,
                )
            except Exception as e:
                logger.debug("⚠️ ᴀᴅᴅ_ᴡᴀʀɴɪɴɢ ᴇʀʀᴏʀ › %s", e)
        logger.info("⚠️ ᴡᴀʀɴɪɴɢ ᴀᴅᴅᴇᴅ › ᴄʜᴀᴛ=%s ᴜsᴇʀ=%s ᴄᴏᴜɴᴛ=%s", cid, uid, count)
        return count, reasons

    async def get_warnings(self, chat_id: int, user_id: int) -> Dict[str, Any]:
        cid, uid = int(chat_id), int(user_id)
        default = {"chat_id": cid, "user_id": uid, "count": 0, "reasons": []}
        if (cid, uid) in self._mem_hd_warns:
            return dict(self._mem_hd_warns[(cid, uid)])
        if self.hd_warns is not None:
            try:
                doc = await self.hd_warns.find_one({"chat_id": cid, "user_id": uid})
                if doc:
                    clean = {k: v for k, v in doc.items() if k != "_id"}
                    self._mem_hd_warns[(cid, uid)] = clean
                    return dict(clean)
            except Exception as e:
                logger.debug("⚠️ ɢᴇᴛ_ᴡᴀʀɴɪɴɢs ᴇʀʀᴏʀ › %s", e)
        return default

    async def remove_one_warning(self, chat_id: int, user_id: int) -> Tuple[int, List[Dict[str, Any]]]:
        cid, uid = int(chat_id), int(user_id)
        doc = await self.get_warnings(cid, uid)
        reasons = list(doc.get("reasons", []))
        if reasons:
            reasons.pop()
        count = len(reasons)
        if count <= 0:
            await self.reset_warnings(cid, uid)
            return 0, []
        updated_doc = {
            "chat_id": cid,
            "user_id": uid,
            "count": count,
            "reasons": reasons,
            "updated_at": time.time(),
        }
        self._mem_hd_warns[(cid, uid)] = updated_doc
        if self.hd_warns is not None:
            try:
                await self.hd_warns.update_one(
                    {"chat_id": cid, "user_id": uid},
                    {"$set": updated_doc},
                    upsert=True,
                )
            except Exception:
                pass
        return count, reasons

    async def reset_warnings(self, chat_id: int, user_id: int) -> bool:
        cid, uid = int(chat_id), int(user_id)
        self._mem_hd_warns.pop((cid, uid), None)
        if self.hd_warns is not None:
            try:
                await self.hd_warns.delete_one({"chat_id": cid, "user_id": uid})
                return True
            except Exception:
                return False
        return True

    async def total_warnings_count(self) -> int:
        if self.hd_warns is not None:
            try:
                return max(await self.hd_warns.count_documents({}), len(self._mem_hd_warns))
            except Exception:
                pass
        return len(self._mem_hd_warns)

    async def record_mod_log(
        self,
        chat_id: int,
        action: str,
        admin_id: int,
        target_id: int = 0,
        details: str = "",
    ) -> None:
        cid = int(chat_id)
        entry = {
            "chat_id": cid,
            "action": str(action),
            "admin_id": int(admin_id),
            "target_id": int(target_id),
            "details": str(details),
            "timestamp": time.time(),
        }
        logs = self._mem_hd_logs.setdefault(cid, [])
        logs.insert(0, entry)
        if len(logs) > 50:
            del logs[50:]
        if self.hd_logs is not None:
            try:
                await self.hd_logs.insert_one(dict(entry))
            except Exception as e:
                logger.debug("⚠️ ʀᴇᴄᴏʀᴅ_ᴍᴏᴅ_ʟᴏɢ ᴇʀʀᴏʀ › %s", e)

    async def get_mod_logs(self, chat_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        cid = int(chat_id)
        if self.hd_logs is not None:
            try:
                cursor = self.hd_logs.find({"chat_id": cid}).sort("timestamp", -1).limit(int(limit))
                docs = await cursor.to_list(length=int(limit))
                if docs:
                    return docs
            except Exception:
                pass
        return list(self._mem_hd_logs.get(cid, []))[: int(limit)]

    async def total_mod_logs_count(self) -> int:
        if self.hd_logs is not None:
            try:
                return max(await self.hd_logs.count_documents({}), sum(len(v) for v in self._mem_hd_logs.values()))
            except Exception:
                pass
        return sum(len(v) for v in self._mem_hd_logs.values())

    # ─── Notes Management ─────────────────────────────────────────────────
    async def save_note(self, chat_id: int, name: str, note_data: Dict[str, Any]) -> None:
        cid = int(chat_id)
        key = name.strip().lower()
        payload = {
            **note_data,
            "chat_id": cid,
            "name": key,
            "updated_at": time.time(),
        }
        self._mem_notes[(cid, key)] = dict(payload)
        if self.notes_col is not None:
            try:
                await self.notes_col.update_one(
                    {"chat_id": cid, "name": key},
                    {"$set": payload},
                    upsert=True,
                )
            except Exception as e:
                logger.debug("⚠️ sᴀᴠᴇ_ɴᴏᴛᴇ ᴇʀʀᴏʀ › %s", e)
        logger.info("📝 ɴᴏᴛᴇ sᴀᴠᴇᴅ › ᴄʜᴀᴛ=%s ɴᴀᴍᴇ=%s", cid, key)

    async def get_note(self, chat_id: int, name: str) -> Optional[Dict[str, Any]]:
        cid = int(chat_id)
        key = name.strip().lower()
        if (cid, key) in self._mem_notes:
            return dict(self._mem_notes[(cid, key)])
        if self.notes_col is not None:
            try:
                doc = await self.notes_col.find_one({"chat_id": cid, "name": key})
                if doc:
                    clean = {k: v for k, v in doc.items() if k != "_id"}
                    self._mem_notes[(cid, key)] = clean
                    return dict(clean)
            except Exception as e:
                logger.debug("⚠️ ɢᴇᴛ_ɴᴏᴛᴇ ᴇʀʀᴏʀ › %s", e)
        return None

    async def get_all_notes(self, chat_id: int) -> List[str]:
        cid = int(chat_id)
        names = {k[1] for k in self._mem_notes.keys() if k[0] == cid}
        if self.notes_col is not None:
            try:
                cursor = self.notes_col.find({"chat_id": cid}, {"name": 1})
                async for doc in cursor:
                    if doc.get("name"):
                        names.add(str(doc["name"]))
            except Exception:
                pass
        return sorted(names)

    async def delete_note(self, chat_id: int, name: str) -> bool:
        cid = int(chat_id)
        key = name.strip().lower()
        existed = self._mem_notes.pop((cid, key), None) is not None
        if self.notes_col is not None:
            try:
                res = await self.notes_col.delete_one({"chat_id": cid, "name": key})
                existed = existed or bool(res.deleted_count)
            except Exception:
                pass
        return existed

    async def delete_all_notes(self, chat_id: int) -> int:
        cid = int(chat_id)
        keys = [k for k in self._mem_notes.keys() if k[0] == cid]
        for k in keys:
            self._mem_notes.pop(k, None)
        cnt = len(keys)
        if self.notes_col is not None:
            try:
                res = await self.notes_col.delete_many({"chat_id": cid})
                cnt = max(cnt, int(res.deleted_count))
            except Exception:
                pass
        return cnt

    async def total_notes_count(self) -> int:
        if self.notes_col is not None:
            try:
                return max(await self.notes_col.count_documents({}), len(self._mem_notes))
            except Exception:
                pass
        return len(self._mem_notes)

    # ─── Custom Auto-Filters Management ───────────────────────────────────
    async def save_filter(self, chat_id: int, trigger: str, filter_data: Dict[str, Any]) -> None:
        cid = int(chat_id)
        key = trigger.strip().lower()
        payload = {
            **filter_data,
            "chat_id": cid,
            "trigger": key,
            "updated_at": time.time(),
        }
        self._mem_filters[(cid, key)] = dict(payload)
        if self.filters_col is not None:
            try:
                await self.filters_col.update_one(
                    {"chat_id": cid, "trigger": key},
                    {"$set": payload},
                    upsert=True,
                )
            except Exception as e:
                logger.debug("⚠️ sᴀᴠᴇ_ғɪʟᴛᴇʀ ᴇʀʀᴏʀ › %s", e)
        logger.info("⚡ ғɪʟᴛᴇʀ sᴀᴠᴇᴅ › ᴄʜᴀᴛ=%s ᴛʀɪɢɢᴇʀ=%s", cid, key)

    async def get_all_filters(self, chat_id: int) -> Dict[str, Dict[str, Any]]:
        cid = int(chat_id)
        result: Dict[str, Dict[str, Any]] = {
            k[1]: dict(v) for k, v in self._mem_filters.items() if k[0] == cid
        }
        if self.filters_col is not None:
            try:
                cursor = self.filters_col.find({"chat_id": cid})
                async for doc in cursor:
                    trig = str(doc.get("trigger", "")).lower()
                    if trig:
                        clean = {k: v for k, v in doc.items() if k != "_id"}
                        self._mem_filters[(cid, trig)] = clean
                        result[trig] = clean
            except Exception:
                pass
        return result

    async def delete_filter(self, chat_id: int, trigger: str) -> bool:
        cid = int(chat_id)
        key = trigger.strip().lower()
        existed = self._mem_filters.pop((cid, key), None) is not None
        if self.filters_col is not None:
            try:
                res = await self.filters_col.delete_one({"chat_id": cid, "trigger": key})
                existed = existed or bool(res.deleted_count)
            except Exception:
                pass
        return existed

    async def delete_all_filters(self, chat_id: int) -> int:
        cid = int(chat_id)
        keys = [k for k in self._mem_filters.keys() if k[0] == cid]
        for k in keys:
            self._mem_filters.pop(k, None)
        cnt = len(keys)
        if self.filters_col is not None:
            try:
                res = await self.filters_col.delete_many({"chat_id": cid})
                cnt = max(cnt, int(res.deleted_count))
            except Exception:
                pass
        return cnt

    async def total_filters_count(self) -> int:
        if self.filters_col is not None:
            try:
                return max(await self.filters_col.count_documents({}), len(self._mem_filters))
            except Exception:
                pass
        return len(self._mem_filters)

    # ─── Word Blacklist Management ────────────────────────────────────────
    async def get_blacklist_config(self, chat_id: int) -> Dict[str, Any]:
        cid = int(chat_id)
        default = {"chat_id": cid, "words": [], "mode": "warn"}
        if cid in self._mem_blacklist:
            return dict(self._mem_blacklist[cid])
        if self.blacklist_col is not None:
            try:
                doc = await self.blacklist_col.find_one({"chat_id": cid})
                if doc:
                    clean = {
                        "chat_id": cid,
                        "words": list(doc.get("words", [])),
                        "mode": str(doc.get("mode", "delete")),
                    }
                    self._mem_blacklist[cid] = clean
                    return dict(clean)
            except Exception:
                pass
        self._mem_blacklist[cid] = dict(default)
        return dict(default)

    async def update_blacklist_config(self, chat_id: int, words: List[str], mode: Optional[str] = None) -> Dict[str, Any]:
        cid = int(chat_id)
        curr = await self.get_blacklist_config(cid)
        curr["words"] = sorted(set(w.strip().lower() for w in words if w.strip()))
        if mode:
            curr["mode"] = mode.strip().lower()
        self._mem_blacklist[cid] = dict(curr)
        if self.blacklist_col is not None:
            try:
                await self.blacklist_col.update_one(
                    {"chat_id": cid},
                    {"$set": curr},
                    upsert=True,
                )
            except Exception:
                pass
        return dict(curr)

    # ─── AFK Status Management ────────────────────────────────────────────
    async def set_afk(self, user_id: int, reason: str = "") -> Dict[str, Any]:
        uid = int(user_id)
        payload = {
            "user_id": uid,
            "is_afk": True,
            "reason": str(reason or "ʙᴜsʏ / ᴀᴡᴀʏ ғʀᴏᴍ ᴋᴇʏʙᴏᴀʀᴅ"),
            "since": time.time(),
        }
        self._mem_afk[uid] = dict(payload)
        if self.afk_col is not None:
            try:
                await self.afk_col.update_one(
                    {"user_id": uid},
                    {"$set": payload},
                    upsert=True,
                )
            except Exception:
                pass
        logger.info("💤 ᴜsᴇʀ sᴇᴛ ᴀғᴋ › ᴜsᴇʀ=%s ʀᴇᴀsᴏɴ=%s", uid, payload["reason"])
        return payload

    async def get_afk(self, user_id: int) -> Optional[Dict[str, Any]]:
        uid = int(user_id)
        if uid in self._mem_afk:
            return dict(self._mem_afk[uid]) if self._mem_afk[uid].get("is_afk") else None
        if self.afk_col is not None:
            try:
                doc = await self.afk_col.find_one({"user_id": uid, "is_afk": True})
                if doc:
                    clean = {k: v for k, v in doc.items() if k != "_id"}
                    self._mem_afk[uid] = clean
                    return dict(clean)
            except Exception:
                pass
        return None

    async def clear_afk(self, user_id: int) -> Optional[Dict[str, Any]]:
        uid = int(user_id)
        prev = self._mem_afk.pop(uid, None)
        if self.afk_col is not None:
            try:
                if not prev:
                    prev = await self.afk_col.find_one({"user_id": uid, "is_afk": True})
                await self.afk_col.delete_one({"user_id": uid})
            except Exception:
                pass
        if prev:
            logger.info("🌅 ᴜsᴇʀ ᴄʟᴇᴀʀᴇᴅ ᴀғᴋ › ᴜsᴇʀ=%s", uid)
        return prev

    # ─── Global Bot Config & Storage Telemetry ────────────────────────────
    async def get_bot_config(self) -> Dict[str, Any]:
        if self.adm is not None:
            try:
                doc = await self.adm.find_one({"_id": "bot_config"})
                if doc:
                    merged = dict(self._mem_config)
                    for k, v in doc.items():
                        if k != "_id" and v is not None:
                            merged[k] = v
                    self._mem_config = merged
                    return dict(merged)
            except Exception as e:
                logger.debug("⚠️ ɢᴇᴛ_ʙᴏᴛ_ᴄᴏɴғɪɢ ᴡᴀʀɴɪɴɢ › %s", e)
        return dict(self._mem_config)

    async def update_bot_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(updates)
        self._mem_config.update(payload)
        if self.adm is not None:
            try:
                await self.adm.update_one(
                    {"_id": "bot_config"},
                    {"$set": payload},
                    upsert=True,
                )
            except Exception as e:
                logger.debug("⚠️ ᴜᴘᴅᴀᴛᴇ_ʙᴏᴛ_ᴄᴏɴғɪɢ ᴡᴀʀɴɪɴɢ › %s", e)
        logger.info("⚙️ ɢʟᴏʙᴀʟ ʙᴏᴛ ᴄᴏɴғɪɢ ᴜᴘᴅᴀᴛᴇᴅ › %s", updates)
        return dict(self._mem_config)

    async def get_db_storage_stats(self) -> Tuple[float, float, float]:
        if self.db is None:
            return 0.0, 512.0, 0.0
        t0 = time.perf_counter()
        try:
            stats = await self.db.command("dbStats")
            ping_ms = (time.perf_counter() - t0) * 1000.0
            used_mb = (stats.get("dataSize", 0) + stats.get("indexSize", 0)) / (1024 * 1024)
            free_mb = max(0.0, 512.0 - used_mb)
            return round(used_mb, 2), round(free_mb, 2), round(ping_ms, 1)
        except Exception as e:
            logger.debug("⚠️ ɢᴇᴛ_ᴅʙ_sᴛᴏʀᴀɢᴇ_sᴛᴀᴛs ᴡᴀʀɴɪɴɢ › %s", e)
            return 0.0, 512.0, 0.0


    async def get_all_group_notes(self, chat_id: int) -> Dict[str, Dict[str, Any]]:
        cid = int(chat_id)
        result: Dict[str, Dict[str, Any]] = {
            k[1]: dict(v) for k, v in self._mem_notes.items() if k[0] == cid
        }
        if self.notes_col is not None:
            try:
                cursor = self.notes_col.find({"chat_id": cid})
                async for doc in cursor:
                    name = str(doc.get("name", "")).lower()
                    if name:
                        clean = {k: v for k, v in doc.items() if k != "_id"}
                        self._mem_notes[(cid, name)] = clean
                        result[name] = clean
            except Exception:
                pass
        return result

    async def save_group_note(
        self,
        chat_id: int,
        name: str,
        content: str = "",
        file_id: Optional[str] = None,
        media_type: Optional[str] = None,
    ) -> None:
        await self.save_note(
            chat_id,
            name,
            {"content": content or "", "file_id": file_id, "media_type": media_type},
        )

    async def get_all_group_filters(self, chat_id: int) -> Dict[str, Dict[str, Any]]:
        return await self.get_all_filters(chat_id)

    async def save_group_filter(
        self,
        chat_id: int,
        trigger: str,
        reply_text: str = "",
        file_id: Optional[str] = None,
        media_type: Optional[str] = None,
    ) -> None:
        await self.save_filter(
            chat_id,
            trigger,
            {"content": reply_text or "", "reply_text": reply_text or "", "file_id": file_id, "media_type": media_type},
        )

    async def get_group_blacklist(self, chat_id: int) -> List[str]:
        cfg = await self.get_blacklist_config(chat_id)
        return list(cfg.get("words", []))

    async def add_blacklist_word(self, chat_id: int, word: str) -> Dict[str, Any]:
        cfg = await self.get_blacklist_config(chat_id)
        words = list(cfg.get("words", []))
        if word and word.strip():
            words.append(word.strip().lower())
        return await self.update_blacklist_config(chat_id, words)


db = Database(DATABASE_NAME)
