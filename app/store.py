"""Хранилище многопользовательского режима: OAuth-клиенты, токены, зашифрованные сессии MAX.

Сессия MAX (токен входа, id устройства) шифруется ключом MAX_MASTER_KEY (Fernet: AES-128-CBC + HMAC).
OAuth-токены хранятся только в виде SHA-256: утечка базы не даёт ни доступа к MCP, ни к MAX без ключа.
"""
import hashlib
import json
import os
import time

import aiosqlite
from cryptography.fernet import Fernet, InvalidToken
from pymax.session.models import SessionInfo

DB_PATH = os.environ.get("MAX_DB", os.path.join(os.environ.get("MAX_DIR", "."), "service.db"))
_fernet = None
_db = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (id TEXT PRIMARY KEY, info TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, name TEXT, session BLOB, created REAL, seen REAL);
CREATE TABLE IF NOT EXISTS tokens (
    hash TEXT PRIMARY KEY, kind TEXT NOT NULL, grant_id TEXT NOT NULL, user_id TEXT NOT NULL,
    client_id TEXT NOT NULL, scopes TEXT NOT NULL, resource TEXT, expires REAL);
CREATE INDEX IF NOT EXISTS tokens_grant ON tokens(grant_id);
CREATE INDEX IF NOT EXISTS tokens_user ON tokens(user_id);
"""


def h(token):
    return hashlib.sha256(token.encode()).hexdigest()


async def db():
    global _db, _fernet
    if _db is None:
        key = os.environ.get("MAX_MASTER_KEY", "")
        try:
            _fernet = Fernet(key)
        except Exception:
            raise SystemExit("MAX_MASTER_KEY: нужен ключ Fernet (python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')")
        os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
        _db = await aiosqlite.connect(DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.executescript(SCHEMA)
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.commit()
        os.chmod(DB_PATH, 0o600)
    return _db


async def close():
    global _db
    if _db is not None:
        await _db.close()
        _db = None


async def q(sql, *args):
    d = await db()
    async with d.execute(sql, args) as cur:
        return await cur.fetchall()


async def x(sql, *args):
    d = await db()
    await d.execute(sql, args)
    await d.commit()


# ---------- OAuth-клиенты ----------

async def save_client(cid, info_json):
    await x("INSERT OR REPLACE INTO clients VALUES (?, ?, ?)", cid, info_json, time.time())


async def load_client(cid):
    rows = await q("SELECT info FROM clients WHERE id = ?", cid)
    return rows[0]["info"] if rows else None


# ---------- токены ----------

async def save_token(token, kind, grant_id, user_id, client_id, scopes, resource, expires):
    await x("INSERT INTO tokens VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            h(token), kind, grant_id, user_id, client_id, json.dumps(scopes), resource, expires)


async def load_token(token, kind):
    rows = await q("SELECT * FROM tokens WHERE hash = ? AND kind = ?", h(token), kind)
    if not rows:
        return None
    r = dict(rows[0])
    if r["expires"] and r["expires"] < time.time():
        await x("DELETE FROM tokens WHERE hash = ?", r["hash"])
        return None
    r["scopes"] = json.loads(r["scopes"])
    return r


async def drop_grant(grant_id):
    await x("DELETE FROM tokens WHERE grant_id = ?", grant_id)


async def cleanup():
    await x("DELETE FROM tokens WHERE expires IS NOT NULL AND expires < ?", time.time())


# ---------- пользователи и сессии MAX ----------

def seal(info: SessionInfo) -> bytes:
    return _fernet.encrypt(info.model_dump_json().encode())


def unseal(blob) -> SessionInfo | None:
    try:
        return SessionInfo.model_validate_json(_fernet.decrypt(blob))
    except (InvalidToken, ValueError):
        return None


async def save_user(uid, name, info: SessionInfo):
    await db()
    now = time.time()
    await x("INSERT INTO users VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET name = excluded.name, "
            "session = excluded.session, seen = excluded.seen", uid, name, seal(info), now, now)


async def user_exists(uid):
    return bool(await q("SELECT 1 FROM users WHERE id = ? AND session IS NOT NULL", uid))


async def delete_user(uid):
    """Полное удаление: сессия MAX и все токены пользователя."""
    await x("DELETE FROM tokens WHERE user_id = ?", uid)
    await x("DELETE FROM users WHERE id = ?", uid)


async def count_users():
    return (await q("SELECT COUNT(*) AS n FROM users"))[0]["n"]


class EncryptedStore:
    """Хранилище сессии PyMax для одного пользователя: строка в users, зашифрованная Fernet."""

    def __init__(self, uid):
        self.uid = uid

    async def save_session(self, session_info):
        await x("UPDATE users SET session = ?, seen = ? WHERE id = ?", seal(session_info), time.time(), self.uid)

    async def update_token(self, old_token, new_token, /):
        info = await self.load_session()
        if info is not None:
            info.token = new_token
            await self.save_session(info)

    async def load_session(self):
        rows = await q("SELECT session FROM users WHERE id = ?", self.uid)
        return unseal(rows[0]["session"]) if rows and rows[0]["session"] else None

    async def load_session_by_device_id(self, device_id):
        s = await self.load_session()
        return s if s and s.device_id == device_id else None

    async def load_session_by_phone(self, phone):
        s = await self.load_session()
        return s if s and s.phone == phone else None

    async def delete_session(self, token, /):
        await x("UPDATE users SET session = NULL WHERE id = ?", self.uid)

    async def close(self):
        return None


class MemoryStore(EncryptedStore):
    """Сессия во время входа, пока не известно, чей это аккаунт."""

    def __init__(self):
        self.info = None

    async def save_session(self, session_info):
        self.info = session_info

    async def load_session(self):
        return self.info

    async def delete_session(self, token, /):
        self.info = None
