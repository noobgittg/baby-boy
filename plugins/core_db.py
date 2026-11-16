import os
import re
import json
import time
import asyncio
from typing import Optional, Dict, Any, List
from config import Config
from plugins.core_utils import logger, cache

try:
    from motor.motor_asyncio import AsyncIOMotorClient
except ImportError:
    AsyncIOMotorClient = None


# ==================== 1. UNIFIED DATABASE MODELS ====================

def new_user_dict(user_id: int, first_name: str, username: Optional[str] = None) -> Dict[str, Any]:
    now_ts = float(time.time())
    return {
        "user_id": int(user_id),
        "first_name": first_name or "User",
        "username": username,
        "is_banned": False,
        "thumb_id": None,
        "tham_url": None,
        "custom_caption": None,
        "captions_list": [],
        "prefix": "",
        "suffix": "",
        "rename_format": None,
        "auto_rename": False,
        "auto_compress": False,
        "auto_stream": True,
        "auto_caption": True,
        "upload_mode": "video",
        "auto_delete_time": int(Config.DEFAULT_AUTO_DELETE),
        "compression_preset": "superfast",
        "resolution": "720p",
        "created_at": now_ts,
        "updated_at": now_ts
    }

def new_chat_dict(chat_id: int, title: str, chat_type: str) -> Dict[str, Any]:
    now_ts = float(time.time())
    return {
        "chat_id": int(chat_id),
        "title": title or "Chat",
        "chat_type": chat_type or "channel",
        "auto_thumbnail": True,
        "tham_url": None,
        "auto_caption": True,
        "custom_caption": None,
        "auto_delete_time": 0,
        "clean_service_messages": True,
        "auto_approve_joins": True,
        "created_at": now_ts,
        "updated_at": now_ts
    }

def new_file_dict(
    file_id: str,
    chat_id: int,
    message_id: int,
    file_name: str,
    file_size: int,
    mime_type: str,
    user_id: int = 0,
    metadata: Optional[Dict[str, Any]] = None,
    thumb_id: Optional[str] = None,
    file_path: Optional[str] = None,
    tg_file_id: Optional[str] = None
) -> Dict[str, Any]:
    now_ts = float(time.time())
    return {
        "file_id": str(file_id),
        "chat_id": int(chat_id),
        "message_id": int(message_id),
        "file_name": file_name or "stream_media.mp4",
        "file_size": int(file_size or 0),
        "mime_type": mime_type or "video/mp4",
        "user_id": int(user_id or 0),
        "metadata": metadata or {},
        "thumb_id": thumb_id,
        "file_path": file_path,
        "tg_file_id": tg_file_id,
        "is_revoked": False,
        "created_at": now_ts,
        "updated_at": now_ts
    }


def new_task_dict(
    task_id: str,
    task_type: str,
    user_id: int,
    chat_id: int,
    original_message_id: int,
    status_chat_id: int = 0,
    status_message_id: int = 0,
    storage_chat_id: int = 0,
    storage_message_id: int = 0,
    file_name: str = "",
    file_size: int = 0,
    mime_type: str = "",
    tg_file_id: Optional[str] = None,
    work_dir: str = "",
    input_path: str = "",
    output_path: str = "",
    params: Optional[Dict[str, Any]] = None,
    idempotency_key: str = "",
    max_retries: int = 4,
) -> Dict[str, Any]:
    now_ts = float(time.time())
    return {
        "task_id": str(task_id),
        "task_type": str(task_type),
        "user_id": int(user_id or 0),
        "chat_id": int(chat_id or 0),
        "original_message_id": int(original_message_id or 0),
        "status_chat_id": int(status_chat_id or chat_id or 0),
        "status_message_id": int(status_message_id or 0),
        "storage_chat_id": int(storage_chat_id or 0),
        "storage_message_id": int(storage_message_id or 0),
        "file_name": str(file_name or "media.mp4"),
        "file_size": int(file_size or 0),
        "mime_type": str(mime_type or "video/mp4"),
        "tg_file_id": tg_file_id,
        "work_dir": str(work_dir or ""),
        "input_path": str(input_path or ""),
        "output_path": str(output_path or ""),
        "params": dict(params or {}),
        "idempotency_key": str(idempotency_key or f"{task_type}:{chat_id}:{original_message_id}"),
        "status": "queued",      # queued | running | interrupted | retrying | completed | failed | cancelled | skipped
        "stage": "initialized",  # initialized | downloading | downloaded | encoding | encoded | uploading | completed
        "progress_pct": 0.0,
        "retry_count": 0,
        "max_retries": int(max_retries or Config.TASK_MAX_RETRIES),
        "recovered": False,
        "recovery_count": 0,
        "output_message_id": 0,
        "output_file_id": "",
        "last_error": "",
        "error_history": [],
        "created_at": now_ts,
        "updated_at": now_ts,
        "completed_at": 0.0,
    }


# ==================== 2. FALLBACK IN-MEMORY COLLECTION (WHEN MONGO OFFLINE) ====================

class InMemoryCollection:
    """Async in-memory Mongo-compatible collection so the bot never crashes if MongoDB is unreachable."""
    def __init__(self, key_field: str = "_id"):
        self.key_field = key_field
        self._docs: List[Dict[str, Any]] = []

    def _matches(self, doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
        for k, v in query.items():
            if k == "$or" and isinstance(v, list):
                if not any(self._matches(doc, sub_q) for sub_q in v):
                    return False
                continue
            val = doc.get(k)
            if isinstance(v, dict):
                if "$ne" in v and val == v["$ne"]:
                    return False
                if "$in" in v and val not in v["$in"]:
                    return False
                if "$nin" in v and val in v["$nin"]:
                    return False
                if "$lte" in v and (val is None or float(val) > float(v["$lte"])):
                    return False
                if "$lt" in v and (val is None or float(val) >= float(v["$lt"])):
                    return False
                if "$gte" in v and (val is None or float(val) < float(v["$gte"])):
                    return False
                if "$gt" in v and (val is None or float(val) <= float(v["$gt"])):
                    return False
                if "$regex" in v:
                    pattern = v["$regex"]
                    flags = re.IGNORECASE if "i" in v.get("$options", "") else 0
                    if not re.search(pattern, str(val or ""), flags):
                        return False
            else:
                if val != v:
                    return False
        return True

    async def create_index(self, *args, **kwargs):
        return True

    async def find_one(self, query: Dict[str, Any], projection: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        for d in self._docs:
            if self._matches(d, query):
                res = dict(d)
                res.pop("_id", None)
                return res
        return None

    async def insert_one(self, doc: Dict[str, Any]):
        self._docs.append(dict(doc))
        class Res:
            acknowledged = True
        return Res()

    async def update_one(self, query: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
        found = None
        for d in self._docs:
            if self._matches(d, query):
                found = d
                break
        if found is None:
            if upsert:
                new_doc = dict(query)
                if "$setOnInsert" in update:
                    new_doc.update(update["$setOnInsert"])
                if "$set" in update:
                    new_doc.update(update["$set"])
                if "$inc" in update:
                    for k, v in update["$inc"].items():
                        new_doc[k] = new_doc.get(k, 0) + v
                if "$addToSet" in update:
                    for k, v in update["$addToSet"].items():
                        lst = new_doc.setdefault(k, [])
                        if v not in lst:
                            lst.append(v)
                if "$push" in update:
                    for k, v in update["$push"].items():
                        lst = new_doc.setdefault(k, [])
                        lst.append(v)
                self._docs.append(new_doc)
        else:
            if "$set" in update:
                found.update(update["$set"])
            if "$inc" in update:
                for k, v in update["$inc"].items():
                    found[k] = found.get(k, 0) + v
            if "$addToSet" in update:
                for k, v in update["$addToSet"].items():
                    lst = found.setdefault(k, [])
                    if v not in lst:
                        lst.append(v)
            if "$push" in update:
                for k, v in update["$push"].items():
                    lst = found.setdefault(k, [])
                    lst.append(v)
            if "$pull" in update:
                for k, v in update["$pull"].items():
                    lst = found.get(k, [])
                    if v in lst:
                        lst.remove(v)
        class Res:
            acknowledged = True
        return Res()

    async def delete_one(self, query: Dict[str, Any]):
        deleted = 0
        for i, d in enumerate(self._docs):
            if self._matches(d, query):
                self._docs.pop(i)
                deleted = 1
                break
        class Res:
            deleted_count = deleted
        return Res()

    async def delete_many(self, query: Dict[str, Any]):
        before = len(self._docs)
        self._docs = [d for d in self._docs if not self._matches(d, query)]
        class Res:
            deleted_count = before - len(self._docs)
        return Res()

    async def count_documents(self, query: Dict[str, Any]) -> int:
        return sum(1 for d in self._docs if self._matches(d, query))

    def find(self, query: Dict[str, Any], projection: Optional[Dict[str, Any]] = None):
        matched = [dict(d) for d in self._docs if self._matches(d, query)]
        for m in matched:
            m.pop("_id", None)

        class Cursor:
            def __init__(self, items):
                self.items = items
            def sort(self, key: str, direction: int = -1):
                self.items.sort(key=lambda x: x.get(key, 0), reverse=(direction == -1))
                return self
            def skip(self, n: int):
                self.items = self.items[n:]
                return self
            def limit(self, n: int):
                self.items = self.items[:n]
                return self
            async def to_list(self, length: int = 100):
                return self.items[:length]

        return Cursor(matched)


class InMemoryDB:
    def __init__(self):
        self.users = InMemoryCollection("user_id")
        self.chats = InMemoryCollection("chat_id")
        self.files = InMemoryCollection("file_id")
        self.auto_delete = InMemoryCollection()
        self.tasks = InMemoryCollection("task_id")


# ==================== 3. SINGLE UNIFIED DATABASE MANAGER ====================

class MongoManager:
    """
    Single unified database manager (MongoDB Motor with automatic in-memory fallback).
    Guarantees zero crashes and fast cache-backed queries across all plugins.
    """
    def __init__(self):
        self.client = None
        self.db = None
        self._fallback_db = InMemoryDB()
        self._connected = False

    @property
    def active_db(self):
        return self.db if (self._connected and self.db is not None) else self._fallback_db

    async def connect(self):
        if self._connected and self.client:
            return
        if AsyncIOMotorClient is None:
            logger.warning("motor package not found; using unified in-memory DB + cache.")
            await task_repo.load_journal_into_db()
            return
        try:
            self.client = AsyncIOMotorClient(
                Config.MONGO_URI,
                maxPoolSize=100,
                minPoolSize=5,
                serverSelectionTimeoutMS=2500,
                connectTimeoutMS=2500,
                retryReads=True,
                retryWrites=True,
            )
            await self.client.admin.command("ping")
            self.db = self.client[Config.DATABASE_NAME]
            self._connected = True
            await self._init_indexes()
            logger.info(f"⚡ Single Unified MongoDB Connected: {Config.DATABASE_NAME}")
        except Exception as e:
            logger.warning(f"MongoDB unreachable ({e}) — running seamlessly on unified In-Memory DB + FastMemoryCache.")
            self._connected = False
            self.db = None
        await task_repo.load_journal_into_db()

    async def _init_indexes(self):
        if not self._connected or self.db is None:
            return
        try:
            await self.db.users.create_index("user_id", unique=True)
            await self.db.users.create_index([("created_at", -1)])
            await self.db.users.create_index("is_banned")
            await self.db.chats.create_index("chat_id", unique=True)
            await self.db.chats.create_index([("created_at", -1)])
            await self.db.files.create_index("file_id", unique=True)
            await self.db.files.create_index([("created_at", -1)])
            await self.db.files.create_index("file_name")
            await self.db.auto_delete.create_index("delete_at")
            await self.db.tasks.create_index("task_id", unique=True)
            await self.db.tasks.create_index("idempotency_key")
            await self.db.tasks.create_index([("status", 1), ("updated_at", 1)])
            await self.db.tasks.create_index("user_id")
        except Exception as e:
            logger.debug(f"Index initialization note: {e}")

    @property
    def is_connected(self) -> bool:
        return self._connected and self.client is not None and self.db is not None

    async def close(self):
        if self.client:
            self.client.close()
            self.client = None
            self.db = None
            self._connected = False
            logger.info("MongoDB Connection closed.")

mongo = MongoManager()


# ==================== 4. UNIFIED REPOSITORIES (WITH FAST CACHE) ====================

class UserRepository:
    @property
    def col(self):
        return mongo.active_db.users

    async def create_user(self, user_id: int, first_name: str, username: Optional[str] = None) -> Dict[str, Any]:
        user_data = new_user_dict(user_id, first_name, username)
        await self.col.update_one({"user_id": user_id}, {"$setOnInsert": user_data}, upsert=True)
        await cache.set(f"user_{user_id}", user_data, ttl=900)
        await cache.delete("stats_total_users")
        return user_data

    async def create_data(self, *args, **kwargs) -> Dict[str, Any]:
        return await self.create_user(*args, **kwargs)

    async def get_or_create(self, user_id: int, first_name: str, username: Optional[str] = None) -> Dict[str, Any]:
        cache_key = f"user_{user_id}"
        cached = await cache.get(cache_key)
        if cached:
            return cached
        user = await self.col.find_one({"user_id": user_id})
        if not user:
            user = new_user_dict(user_id, first_name, username)
            await self.col.insert_one(user)
            await cache.delete("stats_total_users")
        else:
            user.pop("_id", None)
        await cache.set(cache_key, user, ttl=900)
        return user

    async def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        cache_key = f"user_{user_id}"
        cached = await cache.get(cache_key)
        if cached:
            return cached
        user = await self.col.find_one({"user_id": user_id})
        if user:
            user.pop("_id", None)
            await cache.set(cache_key, user, ttl=900)
        return user

    async def get_data(self, user_id: int) -> Optional[Dict[str, Any]]:
        return await self.get_user(user_id)

    async def get_all_users(self) -> List[Dict[str, Any]]:
        cursor = self.col.find({}, {"user_id": 1, "_id": 0})
        return await cursor.to_list(length=100000)

    async def get_users_paginated(self, skip: int = 0, limit: int = 10) -> List[Dict[str, Any]]:
        cursor = self.col.find({}, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit)
        return await cursor.to_list(length=limit)

    async def get_total_users(self) -> int:
        cached = await cache.get("stats_total_users")
        if cached is not None:
            return cached
        count = await self.col.count_documents({})
        await cache.set("stats_total_users", count, ttl=300)
        return count

    async def get_banned_users_count(self) -> int:
        cached = await cache.get("stats_banned_users")
        if cached is not None:
            return cached
        count = await self.col.count_documents({"is_banned": True})
        await cache.set("stats_banned_users", count, ttl=300)
        return count

    async def is_banned(self, user_id: int) -> bool:
        cache_key = f"banned_{user_id}"
        cached = await cache.get(cache_key)
        if cached is not None:
            return cached
        user = await self.get_user(user_id)
        banned = bool(user.get("is_banned", False)) if user else False
        await cache.set(cache_key, banned, ttl=1800)
        return banned

    async def update_user(self, user_id: int, data: Dict[str, Any]) -> bool:
        data["updated_at"] = float(time.time())
        existing = await self.get_user(user_id)
        if not existing:
            base = new_user_dict(user_id, "User")
            base.update(data)
            await self.col.update_one({"user_id": user_id}, {"$set": base}, upsert=True)
            await cache.set(f"user_{user_id}", base, ttl=900)
            return True
        res = await self.col.update_one({"user_id": user_id}, {"$set": data}, upsert=True)
        existing.update(data)
        await cache.set(f"user_{user_id}", existing, ttl=900)
        return res.acknowledged

    async def update_data(self, user_id: int, data: Dict[str, Any]) -> bool:
        return await self.update_user(user_id, data)

    async def ban_user(self, user_id: int):
        await self.update_user(user_id, {"is_banned": True})
        await cache.set(f"banned_{user_id}", True, ttl=86400)
        await cache.delete("stats_banned_users")

    async def unban_user(self, user_id: int):
        await self.update_user(user_id, {"is_banned": False})
        await cache.set(f"banned_{user_id}", False, ttl=86400)
        await cache.delete("stats_banned_users")

    async def add_caption(self, user_id: int, caption_text: str):
        now_ts = float(time.time())
        await self.col.update_one(
            {"user_id": user_id},
            {"$addToSet": {"captions_list": caption_text}, "$set": {"updated_at": now_ts}},
            upsert=True
        )
        await cache.delete(f"user_{user_id}")

    async def remove_caption(self, user_id: int, caption_text: str):
        now_ts = float(time.time())
        await self.col.update_one(
            {"user_id": user_id},
            {"$pull": {"captions_list": caption_text}, "$set": {"updated_at": now_ts}}
        )
        await cache.delete(f"user_{user_id}")

    async def delete_caption(self, user_id: int, caption_text: str):
        await self.remove_caption(user_id, caption_text)

    async def delete_user(self, user_id: int) -> bool:
        res = await self.col.delete_one({"user_id": user_id})
        await cache.delete(f"user_{user_id}")
        await cache.delete(f"banned_{user_id}")
        await cache.delete("stats_total_users")
        await cache.delete("stats_banned_users")
        return res.deleted_count > 0

    async def delete_data(self, user_id: int) -> bool:
        return await self.delete_user(user_id)


class ChatRepository:
    @property
    def col(self):
        return mongo.active_db.chats

    @property
    def auto_del_col(self):
        return mongo.active_db.auto_delete

    async def create_chat(self, chat_id: int, title: str, chat_type: str) -> Dict[str, Any]:
        chat_data = new_chat_dict(chat_id, title, chat_type)
        await self.col.update_one({"chat_id": chat_id}, {"$setOnInsert": chat_data}, upsert=True)
        await cache.set(f"chat_{chat_id}", chat_data, ttl=900)
        await cache.delete("stats_total_chats")
        return chat_data

    async def create_data(self, *args, **kwargs) -> Dict[str, Any]:
        return await self.create_chat(*args, **kwargs)

    async def get_or_create_chat(self, chat_id: int, title: str, chat_type: str) -> Dict[str, Any]:
        cache_key = f"chat_{chat_id}"
        cached = await cache.get(cache_key)
        if cached:
            return cached
        chat = await self.col.find_one({"chat_id": chat_id})
        if not chat:
            chat = new_chat_dict(chat_id, title, chat_type)
            await self.col.insert_one(chat)
            await cache.delete("stats_total_chats")
        else:
            chat.pop("_id", None)
        await cache.set(cache_key, chat, ttl=900)
        return chat

    async def get_chat(self, chat_id: int) -> Optional[Dict[str, Any]]:
        cache_key = f"chat_{chat_id}"
        cached = await cache.get(cache_key)
        if cached:
            return cached
        chat = await self.col.find_one({"chat_id": chat_id})
        if chat:
            chat.pop("_id", None)
            await cache.set(cache_key, chat, ttl=900)
        return chat

    async def get_data(self, chat_id: int) -> Optional[Dict[str, Any]]:
        return await self.get_chat(chat_id)

    async def get_all_chats(self) -> List[Dict[str, Any]]:
        cursor = self.col.find({}, {"chat_id": 1, "_id": 0})
        return await cursor.to_list(length=100000)

    async def get_total_chats(self) -> int:
        cached = await cache.get("stats_total_chats")
        if cached is not None:
            return cached
        count = await self.col.count_documents({})
        await cache.set("stats_total_chats", count, ttl=300)
        return count

    async def update_chat(self, chat_id: int, data: Dict[str, Any]) -> bool:
        data["updated_at"] = float(time.time())
        res = await self.col.update_one({"chat_id": chat_id}, {"$set": data}, upsert=True)
        await cache.delete(f"chat_{chat_id}")
        return res.acknowledged

    async def update_data(self, chat_id: int, data: Dict[str, Any]) -> bool:
        return await self.update_chat(chat_id, data)

    async def delete_chat(self, chat_id: int) -> bool:
        res = await self.col.delete_one({"chat_id": chat_id})
        await cache.delete(f"chat_{chat_id}")
        await cache.delete("stats_total_chats")
        return res.deleted_count > 0

    async def delete_data(self, chat_id: int) -> bool:
        return await self.delete_chat(chat_id)

    async def create_auto_delete_task(self, chat_id: int, message_id: int, delete_at: float):
        await self.auto_del_col.insert_one({
            "chat_id": chat_id,
            "message_id": message_id,
            "delete_at": float(delete_at)
        })

    async def add_auto_delete_task(self, chat_id: int, message_id: int, delete_at: float):
        await self.create_auto_delete_task(chat_id, message_id, delete_at)

    async def get_expired_auto_delete(self, current_time: float) -> List[Dict[str, Any]]:
        cursor = self.auto_del_col.find({"delete_at": {"$lte": float(current_time)}})
        return await cursor.to_list(length=100)

    async def delete_auto_delete_task(self, chat_id: int, message_id: int) -> bool:
        res = await self.auto_del_col.delete_one({"chat_id": chat_id, "message_id": message_id})
        return res.deleted_count > 0

    async def remove_auto_delete_task(self, chat_id: int, message_id: int) -> bool:
        return await self.delete_auto_delete_task(chat_id, message_id)


class FileRepository:
    @property
    def col(self):
        return mongo.active_db.files

    async def create_file(
        self,
        file_id: str,
        chat_id: int,
        message_id: int,
        file_name: str,
        file_size: int,
        mime_type: str,
        user_id: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
        thumb_id: Optional[str] = None,
        file_path: Optional[str] = None,
        tg_file_id: Optional[str] = None
    ) -> Dict[str, Any]:
        data = new_file_dict(
            file_id=file_id,
            chat_id=chat_id,
            message_id=message_id,
            file_name=file_name,
            file_size=file_size,
            mime_type=mime_type,
            user_id=user_id,
            metadata=metadata,
            thumb_id=thumb_id,
            file_path=file_path,
            tg_file_id=tg_file_id
        )
        await self.col.update_one({"file_id": file_id}, {"$set": data}, upsert=True)
        await cache.set(f"file_{file_id}", data, ttl=3600)
        await cache.delete("stats_total_files")
        return data

    async def save_file(self, *args, **kwargs) -> Dict[str, Any]:
        return await self.create_file(*args, **kwargs)

    async def create_data(self, *args, **kwargs) -> Dict[str, Any]:
        return await self.create_file(*args, **kwargs)

    async def get_file(self, file_id: str, include_revoked: bool = False) -> Optional[Dict[str, Any]]:
        cache_key = f"file_{file_id}"
        cached = await cache.get(cache_key)
        if cached:
            if not include_revoked and cached.get("is_revoked"):
                return None
            return cached

        file_doc = await self.col.find_one({"file_id": file_id}, {"_id": 0})
        if file_doc:
            file_doc.pop("_id", None)
            await cache.set(cache_key, file_doc, ttl=3600)
            if not include_revoked and file_doc.get("is_revoked"):
                return None
        return file_doc

    @classmethod
    async def get_file_by_id(cls, file_id: str, include_revoked: bool = False) -> Optional[Dict[str, Any]]:
        """Classmethod alias so both FileRepository.get_file_by_id(id) and file_repo.get_file(id) work."""
        return await file_repo.get_file(file_id, include_revoked=include_revoked)

    async def get_data(self, file_id: str, include_revoked: bool = False) -> Optional[Dict[str, Any]]:
        return await self.get_file(file_id, include_revoked=include_revoked)

    async def get_total_files(self) -> int:
        cached = await cache.get("stats_total_files")
        if cached is not None:
            return cached
        count = await self.col.count_documents({"is_revoked": {"$ne": True}})
        await cache.set("stats_total_files", count, ttl=300)
        return count

    async def get_recent_files(self, limit: int = 15) -> List[Dict[str, Any]]:
        cursor = self.col.find({"is_revoked": {"$ne": True}}, {"_id": 0}).sort("created_at", -1).limit(limit)
        return await cursor.to_list(length=limit)

    async def get_user_files(self, user_id: int, skip: int = 0, limit: int = 8) -> List[Dict[str, Any]]:
        cursor = (
            self.col.find({"user_id": int(user_id), "is_revoked": {"$ne": True}}, {"_id": 0})
            .sort("created_at", -1)
            .skip(max(0, int(skip)))
            .limit(max(1, int(limit)))
        )
        return await cursor.to_list(length=limit)

    async def get_user_files_count(self, user_id: int) -> int:
        return await self.col.count_documents({"user_id": int(user_id), "is_revoked": {"$ne": True}})

    async def clear_user_files(self, user_id: int) -> int:
        files = await self.get_user_files(user_id, skip=0, limit=500)
        count = 0
        for f in files:
            fid = f.get("file_id")
            if fid:
                await self.revoke_file(fid)
                count += 1
        return count

    async def search_files(self, query: str, limit: int = 15) -> List[Dict[str, Any]]:
        if not query or not query.strip():
            return []
        safe_q = re.escape(query.strip())
        cursor = self.col.find(
            {"file_name": {"$regex": safe_q, "$options": "i"}, "is_revoked": {"$ne": True}},
            {"_id": 0}
        ).sort("created_at", -1).limit(limit)
        return await cursor.to_list(length=limit)

    async def update_file(self, file_id: str, data: Dict[str, Any]) -> bool:
        data["updated_at"] = float(time.time())
        res = await self.col.update_one({"file_id": file_id}, {"$set": data})
        await cache.delete(f"file_{file_id}")
        return res.acknowledged

    async def revoke_file(self, file_id: str) -> bool:
        now_ts = float(time.time())
        res = await self.col.update_one(
            {"file_id": file_id},
            {"$set": {"is_revoked": True, "updated_at": now_ts}}
        )
        await cache.delete(f"file_{file_id}")
        await cache.delete("stats_total_files")
        return res.acknowledged

    async def unrevoke_file(self, file_id: str) -> bool:
        now_ts = float(time.time())
        res = await self.col.update_one(
            {"file_id": file_id},
            {"$set": {"is_revoked": False, "updated_at": now_ts}}
        )
        await cache.delete(f"file_{file_id}")
        await cache.delete("stats_total_files")
        return res.acknowledged

    async def update_data(self, file_id: str, data: Dict[str, Any]) -> bool:
        return await self.update_file(file_id, data)

    async def delete_file(self, file_id: str) -> bool:
        res = await self.col.delete_one({"file_id": file_id})
        await cache.delete(f"file_{file_id}")
        await cache.delete("stats_total_files")
        return res.deleted_count > 0

    async def delete_data(self, file_id: str) -> bool:
        return await self.delete_file(file_id)


# ==================== 5. PERSISTENT TASK RECOVERY REPOSITORY ====================

class TaskRepository:
    """
    Dual-layer persistent task repository:
    1. Primary: MongoDB (`mongo.active_db.tasks`) for survival across server rebuilds and container redeployments.
    2. Secondary: Atomic local JSON journal (`Config.TASK_JOURNAL_PATH`) + per-task workspace `state.json`
       so tasks also survive process restarts (`os.execl`), container restarts, and offline-MongoDB environments.
    """
    def __init__(self):
        self._journal_lock = asyncio.Lock()

    @property
    def col(self):
        return mongo.active_db.tasks

    def _journal_path(self) -> str:
        return getattr(Config, "TASK_JOURNAL_PATH", os.path.join(Config.DOWNLOAD_DIR, ".persistent_tasks_journal.json"))

    def _read_journal_sync(self) -> Dict[str, Dict[str, Any]]:
        jpath = self._journal_path()
        if not os.path.exists(jpath):
            return {}
        try:
            with open(jpath, "r", encoding="utf-8") as f:
                raw = json.load(f)
                if isinstance(raw, dict):
                    return raw
        except Exception as e:
            logger.debug(f"Task journal read notice: {e}")
        return {}

    def _write_journal_sync(self, data: Dict[str, Dict[str, Any]], work_dir_state: Optional[Dict[str, Any]] = None) -> None:
        jpath = self._journal_path()
        try:
            os.makedirs(os.path.dirname(jpath), exist_ok=True)
            tmp_path = f"{jpath}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(tmp_path, jpath)
        except Exception as e:
            logger.debug(f"Task journal write notice: {e}")

        if work_dir_state and work_dir_state.get("work_dir"):
            wdir = work_dir_state["work_dir"]
            if os.path.isdir(wdir):
                try:
                    st_path = os.path.join(wdir, "task_state.json")
                    st_tmp = f"{st_path}.tmp"
                    with open(st_tmp, "w", encoding="utf-8") as f:
                        json.dump(work_dir_state, f, indent=2, default=str)
                    os.replace(st_tmp, st_path)
                except Exception:
                    pass

    async def _sync_task_to_journal(self, task_doc: Dict[str, Any]) -> None:
        async with self._journal_lock:
            def _do_sync():
                jdata = self._read_journal_sync()
                clean = {k: v for k, v in task_doc.items() if k != "_id"}
                jdata[str(clean["task_id"])] = clean
                # Keep journal bounded to most recent 1000 tasks
                if len(jdata) > 1000:
                    sorted_keys = sorted(
                        jdata.keys(),
                        key=lambda k: float(jdata[k].get("updated_at", 0.0)),
                    )
                    for old_k in sorted_keys[:-1000]:
                        if jdata[old_k].get("status") in ("completed", "cancelled", "skipped"):
                            jdata.pop(old_k, None)
                self._write_journal_sync(jdata, clean)
            await asyncio.to_thread(_do_sync)

    async def load_journal_into_db(self) -> int:
        """Load tasks from local disk journal and workspace `task_state.json` files into active DB on startup."""
        async with self._journal_lock:
            jdata = await asyncio.to_thread(self._read_journal_sync)
            # Also scan DOWNLOAD_DIR for any orphaned `task_state.json` files
            def _scan_workspaces():
                found = {}
                if os.path.isdir(Config.DOWNLOAD_DIR):
                    for root, _, files in os.walk(Config.DOWNLOAD_DIR):
                        if "task_state.json" in files:
                            fpath = os.path.join(root, "task_state.json")
                            try:
                                with open(fpath, "r", encoding="utf-8") as f:
                                    doc = json.load(f)
                                    if isinstance(doc, dict) and doc.get("task_id"):
                                        found[str(doc["task_id"])] = doc
                            except Exception:
                                pass
                return found

            ws_tasks = await asyncio.to_thread(_scan_workspaces)
            for tid, wdoc in ws_tasks.items():
                if tid not in jdata or float(wdoc.get("updated_at", 0)) > float(jdata[tid].get("updated_at", 0)):
                    jdata[tid] = wdoc

        restored = 0
        for tid, doc in jdata.items():
            clean = {k: v for k, v in doc.items() if k != "_id"}
            existing = await self.col.find_one({"task_id": tid})
            if not existing:
                await self.col.update_one({"task_id": tid}, {"$set": clean}, upsert=True)
                restored += 1
            elif float(clean.get("updated_at", 0)) > float(existing.get("updated_at", 0)):
                await self.col.update_one({"task_id": tid}, {"$set": clean}, upsert=True)
                restored += 1
        return restored

    async def create_task(self, **kwargs) -> Dict[str, Any]:
        doc = new_task_dict(**kwargs)
        await self.col.update_one({"task_id": doc["task_id"]}, {"$set": doc}, upsert=True)
        await cache.set(f"task_{doc['task_id']}", doc, ttl=1800)
        await self._sync_task_to_journal(doc)
        return doc

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        cached = await cache.get(f"task_{task_id}")
        if cached:
            return dict(cached)
        doc = await self.col.find_one({"task_id": str(task_id)}, {"_id": 0})
        if doc:
            doc.pop("_id", None)
            await cache.set(f"task_{task_id}", doc, ttl=1800)
        return doc

    async def find_by_idempotency_key(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        if not idempotency_key:
            return None
        doc = await self.col.find_one({"idempotency_key": str(idempotency_key)}, {"_id": 0})
        if doc:
            doc.pop("_id", None)
        return doc

    async def update_task(self, task_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        now_ts = float(time.time())
        updates["updated_at"] = now_ts
        existing = await self.get_task(task_id) or {"task_id": str(task_id)}
        existing.update(updates)
        existing.pop("_id", None)
        await self.col.update_one({"task_id": str(task_id)}, {"$set": existing}, upsert=True)
        await cache.set(f"task_{task_id}", existing, ttl=1800)
        await self._sync_task_to_journal(existing)
        return existing

    async def record_task_error(self, task_id: str, error_msg: str, stage: str = "") -> Optional[Dict[str, Any]]:
        existing = await self.get_task(task_id) or {"task_id": str(task_id), "error_history": []}
        hist = list(existing.get("error_history") or [])
        hist.append({
            "timestamp": float(time.time()),
            "stage": stage or existing.get("stage", "unknown"),
            "error": str(error_msg)[:500],
        })
        if len(hist) > 20:
            hist = hist[-20:]
        return await self.update_task(task_id, {
            "last_error": str(error_msg)[:500],
            "error_history": hist,
        })

    async def get_recoverable_tasks(self) -> List[Dict[str, Any]]:
        """
        Return all incomplete, interrupted, queued, running, retrying, or retry-eligible failed tasks.
        """
        await self.load_journal_into_db()
        cursor = self.col.find(
            {
                "status": {
                    "$in": ["queued", "running", "downloading", "downloaded", "encoding", "encoded", "uploading", "interrupted", "retrying", "failed"]
                }
            },
            {"_id": 0},
        ).sort("created_at", 1)
        docs = await cursor.to_list(length=500)
        recoverable = []
        for d in docs:
            d.pop("_id", None)
            st = d.get("status", "queued")
            retries = int(d.get("retry_count", 0))
            max_r = int(d.get("max_retries", Config.TASK_MAX_RETRIES))
            if st == "failed" and retries >= max_r:
                continue
            recoverable.append(d)
        return recoverable

    async def get_all_tasks(self, limit: int = 200) -> List[Dict[str, Any]]:
        cursor = self.col.find({}, {"_id": 0}).sort("updated_at", -1).limit(limit)
        docs = await cursor.to_list(length=limit)
        for d in docs:
            d.pop("_id", None)
        return docs


user_repo = UserRepository()
chat_repo = ChatRepository()
file_repo = FileRepository()
task_repo = TaskRepository()
