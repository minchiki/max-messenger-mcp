"""Аккаунты MAX: постоянное подключение, кэш чатов и имён, живая лента событий.

Каждый аккаунт держит одно подключение: вызов инструмента не ждёт рукопожатия (~0,1 с вместо ~1 с).
Список чатов хранится в памяти и обновляется событиями, поэтому max_list_chats и поиск чата
по названию не ходят в сеть. События (новые, изменённые, удалённые сообщения, реакции)
складываются в ленту: max_get_updates и max_wait_for_message читают её без опроса чатов.

Режимы: один аккаунт владельца сервера или много пользователей (MAX_MULTIUSER=1) —
тогда аккаунт выбирается по OAuth-токену запроса, а простаивающие подключения закрываются.
"""
import asyncio
import collections
import contextvars
import inspect
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from cfg import make_client, session_exists
from dates import parse_when
from safety import clean, name

TZ = ZoneInfo(os.environ.get("TZ_NAME", "Europe/Moscow"))
FEED_SIZE = int(os.environ.get("MAX_FEED_SIZE", "2000"))
HISTORY_MAX = 5000      # сообщений за один вызов
HISTORY_CHARS = 80000   # символов в ответе: больше ассистент всё равно не прочитает
IDLE_MIN = int(os.environ.get("MAX_IDLE_MIN", "60"))
SAVED = 0  # «Избранное» в MAX — диалог с id 0
STARTED = time.time()

CUR = contextvars.ContextVar("max_account")  # аккаунт текущего вызова инструмента
accounts = {}            # ключ -> Account
resolver = None          # многопользовательский режим: () -> (ключ, фабрика клиента)


def log(*a):
    print(*a, file=sys.stderr, flush=True)


class SessionRevoked(RuntimeError):
    pass


class Account:
    def __init__(self, key, factory):
        self.key, self.factory = key, factory
        self.client = None
        self.chats = {}                       # id -> Chat, свежие данные приходят событиями
        self.feed = collections.deque(maxlen=FEED_SIZE)
        self.seq = 0
        self.sent = collections.deque()       # время отправок для лимита в минуту
        self.used = time.time()
        self.revoked = False
        self._new = asyncio.Condition()
        self._lock = asyncio.Lock()
        self._keeper = None

    def alive(self):
        c = self.client
        return c is not None and c.is_connected and c.me is not None

    async def get(self):
        """Клиент MAX: готовое подключение или новое, если связь оборвалась."""
        self.used = time.time()
        if self.revoked:
            raise SessionRevoked("Сессия MAX завершена (выход на всех устройствах?): войдите заново")
        if not self.alive():
            async with self._lock:
                if not self.alive():
                    await self._connect()
        if self._keeper is None or self._keeper.done():
            self._keeper = asyncio.create_task(self._keep())
        return self.client

    async def _connect(self):
        await self._drop()
        c = self.factory()
        c.acc = self
        _attach(self, c)
        try:
            await c.connect()
        except Exception as e:
            if any(x in str(e) for x in ("FAIL_LOGIN_TOKEN", "FAIL_LOGOUT_ALL", "login.token")):
                self.revoked = True
                raise SessionRevoked("Сессия MAX недействительна: войдите заново") from e
            raise
        if c.me is None:
            raise RuntimeError("Не удалось войти в MAX: войдите заново")
        self.client = c
        await load_chats(c)

    async def _drop(self):
        if self.client is not None:
            try:
                await self.client.close()
            except Exception:
                pass
            self.client = None

    async def _keep(self):
        """Следит за связью: переподключает сразу, а не при следующем вызове инструмента."""
        delay = 5
        while not self.revoked:
            await asyncio.sleep(15)
            if self.alive():
                delay = 5
                continue
            try:
                async with self._lock:
                    await self._connect()
                log(f"MAX reconnected [{self.key}]")
                delay = 5
            except SessionRevoked:
                log(f"MAX session revoked [{self.key}]")
                return
            except Exception as e:
                log(f"MAX reconnect failed [{self.key}]: {e}")
                await asyncio.sleep(min(delay, 300))
                delay *= 2

    async def close(self):
        if self._keeper is not None:
            self._keeper.cancel()
        await self._drop()

    # ---------- лента событий ----------

    async def push(self, kind, chat_id, msg=None, **extra):
        self.seq += 1
        self.feed.append(dict(seq=self.seq, kind=kind, chat=chat_id, msg=msg, at=time.time(), **extra))
        async with self._new:
            self._new.notify_all()

    async def wait(self, pred, since, timeout):
        """События после since, подходящие под pred; ждёт до timeout секунд, если их ещё нет."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        async with self._new:
            while True:
                hit = [e for e in self.feed if e["seq"] > since and pred(e)]
                left = deadline - loop.time()
                if hit or left <= 0:
                    return hit
                try:
                    await asyncio.wait_for(self._new.wait(), left)
                except asyncio.TimeoutError:
                    pass


def _local():
    if not session_exists():
        raise RuntimeError("Сессия MAX не создана: выполните login.sh")
    return make_client()


async def current():
    """Аккаунт текущего вызова: владельца сервера или пользователя OAuth-токена."""
    if resolver is None:
        key, factory = "local", _local
    else:
        key, factory = resolver()
    a = accounts.get(key)
    if a is None:
        a = accounts[key] = Account(key, factory)
    CUR.set(a)
    return a


async def mx():
    return await (await current()).get()


async def forget(key):
    a = accounts.pop(key, None)
    if a is not None:
        await a.close()


async def warm():
    """Однопользовательский режим: подключиться при старте. Многопользовательский: закрывать простаивающих."""
    if resolver is None:
        try:
            await mx()
            log(f"MAX connected: {len(accounts['local'].chats)} chats")
        except Exception as e:
            log(f"MAX warmup failed: {e}")
        return
    while True:
        await asyncio.sleep(300)
        for key, a in list(accounts.items()):
            if time.time() - a.used > IDLE_MIN * 60:
                await forget(key)
                log(f"MAX idle closed [{key}]")


def me_id(c):
    return c.me.contact.id


async def load_chats(c):
    """Все чаты постранично (маркер — время последнего события)."""
    got, marker = {}, None
    for _ in range(100):
        page = await c.fetch_chats(marker)
        fresh = [ch for ch in page if ch.id not in got]
        if not fresh:
            break
        got.update((ch.id, ch) for ch in fresh)
        marker = min(ch.last_event_time for ch in page)
    c.acc.chats.clear()
    c.acc.chats.update(got)


def _attach(a, c):
    @c.on_message()
    async def _msg(m, *_):
        ch = a.chats.get(m.chat_id)
        if ch is None:
            asyncio.create_task(_add_chat(c, m.chat_id))
        else:
            try:
                ch.last_event_time, ch.last_message = m.time, m
                if m.sender != me_id(c):
                    ch.new_messages += 1
            except Exception:
                pass
        await a.push("new", m.chat_id, m)

    @c.on_message_edit()
    async def _edit(m, *_):
        await a.push("edit", m.chat_id, m)

    @c.on_message_delete()
    async def _delete(e, *_):
        await a.push("delete", e.chat_id, ids=list(e.message_ids))

    @c.on_reaction_update()
    async def _reaction(e, *_):
        counters = " ".join(f"{x.reaction}{x.count}" for x in (e.counters or []))
        await a.push("reaction", e.chat_id, ids=[e.message_id], text=counters)

    @c.on_message_read()
    async def _read(e, *_):
        ch = a.chats.get(e.chat_id)
        if ch is not None and e.user_id == me_id(c) and not e.set_as_unread:
            ch.new_messages = 0

    @c.on_chat_update()
    async def _chat(ch, *_):
        a.chats[ch.id] = ch


async def _add_chat(c, chat_id):
    try:
        c.acc.chats[chat_id] = await c.get_chat(chat_id)
    except Exception:
        pass


# ---------- имена и чаты ----------

def uname(u):
    if u is None:
        return "?"
    for n in u.names:
        s = n.name or " ".join(x for x in (n.first_name, n.last_name) if x)
        if s:
            return name(s)
    return str(u.id)


async def names(c, ids):
    """Имена пользователей; PyMax кэширует их, так что повторные запросы бесплатны."""
    ids = [i for i in dict.fromkeys(ids) if i]
    try:
        return {u.id: uname(u) for u in await c.get_users(ids)} if ids else {}
    except Exception:
        return {}


def peer(c, ch):
    return next((p for p in ch.participants if p != me_id(c)), None)


def ctype(ch):
    return {"DIALOG": "личный", "CHAT": "группа", "CHANNEL": "канал"}.get(str(ch.type).split(".")[-1], str(ch.type))


async def titles(c, chs):
    who = await names(c, [peer(c, ch) for ch in chs if not ch.title])
    return {ch.id: "Избранное" if ch.id == SAVED else name(ch.title) if ch.title else who.get(peer(c, ch), "диалог")
            for ch in chs}


async def chat_id(c, chat):
    """id чата из id, «me»/«избранное» или названия (точного или части)."""
    s = str(chat).strip()
    if s.lower() in ("me", "saved", "избранное", "0"):
        return SAVED
    try:
        return int(s)
    except ValueError:
        pass
    if not c.acc.chats:
        await load_chats(c)
    t = await titles(c, list(c.acc.chats.values()))
    q = s.lstrip("@").casefold()
    hit = [i for i, n in t.items() if n.casefold() == q] or [i for i, n in t.items() if q in n.casefold()]
    if len(hit) == 1:
        return hit[0]
    if not hit:
        raise ValueError(f"Чат «{s}» не найден, посмотрите max_list_chats")
    raise ValueError("Подходят несколько чатов, укажите id: " + "; ".join(f"{i} {t[i]}" for i in hit[:8]))


async def get_chat(c, cid):
    ch = c.acc.chats.get(cid)
    if ch is None:
        ch = c.acc.chats[cid] = await c.get_chat(cid)
    return ch


async def maybe(x):
    return await x if inspect.isawaitable(x) else x


# ---------- сообщения ----------

KINDS = {"PHOTO": "фото", "VIDEO": "видео", "STICKER": "стикер", "AUDIO": "голосовое", "CONTACT": "контакт",
         "CALL": "звонок", "INLINE_KEYBOARD": "кнопки"}


def atype(a):
    return str(getattr(a, "type", "")).split(".")[-1]


def kind(m):
    out = []
    for a in m.attaches:
        t = atype(a)
        if t == "POLL":
            out.append(f"<опрос: {clean(a.title, 300)}>")
        elif t == "FILE":
            out.append(f"<файл: {name(a.name)}, {max(1, a.size // 1024)} КБ>")
        elif t == "SHARE":
            out.append(f"<ссылка: {clean(getattr(a, 'url', '') or getattr(a, 'title', ''), 300)}>")
        elif t == "CONTROL":
            out.append(f"<{clean(getattr(a, 'event', '') or 'служебное', 60)}>")
        elif t:
            out.append(f"<{KINDS.get(t, t.lower())}>")
    return " ".join(out)


def when(ms):
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%d.%m.%y %H:%M")


def fmt(m, who, text_limit=4000):
    body = " ".join(x for x in (kind(m), clean(m.text, text_limit)) if x)
    link, pre = getattr(m, "link", None), ""
    if link is not None:
        if str(link.type).endswith("REPLY"):
            pre = f"↩{link.message.id} "
        else:
            src = name(getattr(link, "chat_name", None) or link.chat_id)
            pre = f"[переслано из {src}] "
            body = body or " ".join(x for x in (kind(link.message), clean(link.message.text)) if x)
    ri = m.reaction_info
    react = f" [{' '.join(f'{x.reaction}{x.count}' for x in ri.counters)}]" if ri and ri.counters else ""
    return f"#{m.id} [{when(m.time)}] {who}: {pre}{body}{react}"


async def fetch(c, cid, limit, from_time=None, forward=0):
    """История чата постранично: limit сообщений назад от from_time (или от конца)."""
    out, seen = [], set()
    t = from_time
    while len(out) < limit:
        page = await c.fetch_history(cid, backward=min(100, limit - len(out)), forward=forward,
                                     **({"from_time": t} if t else {}))
        forward = 0
        fresh = [m for m in page if m.id not in seen]
        if not fresh:
            break
        seen.update(m.id for m in fresh)
        out += fresh
        t = min(m.time for m in fresh)
        if len(page) < 2:
            break
    return sorted(out, key=lambda m: m.time)


async def render(c, msgs, text_limit=4000):
    me = me_id(c)
    who = await names(c, [m.sender for m in msgs if m.sender != me])
    return [fmt(m, "я" if m.sender == me else who.get(m.sender, str(m.sender)), text_limit) for m in msgs]


async def pages(c, cid, lo=None, hi=None, before_id=0):
    """Страницы истории от новых к старым (по 100, внутри страницы тоже от новых к старым).

    lo / hi — границы по времени (datetime), before_id — идти от сообщения с этим id назад."""
    lo_ms, hi_ms = (int(lo.timestamp() * 1000) if lo else None), (int(hi.timestamp() * 1000) if hi else None)
    t = hi_ms
    if before_id:
        m = await c.get_message(cid, before_id)
        if m:
            t = m.time if t is None else min(t, m.time)
    seen = {before_id}
    while True:
        page = await c.fetch_history(cid, backward=100, **({"from_time": t} if t else {}))
        fresh = sorted((m for m in page if m.id not in seen), key=lambda m: m.time, reverse=True)
        if not fresh:
            return
        seen.update(m.id for m in fresh)
        t = fresh[-1].time
        yield [m for m in fresh if (hi_ms is None or m.time <= hi_ms) and (lo_ms is None or m.time >= lo_ms)]
        if (lo_ms and t < lo_ms) or len(page) < 2:
            return


async def history(c, cid, limit, date_from="", date_to="", before_id=0, text_limit=1000):
    """Сообщения с отсечкой по датам и размеру: (строки от старых к новым, подсказка для продолжения)."""
    lo, hi = parse_when(date_from, TZ), parse_when(date_to, TZ, end=True)
    if lo and hi and lo > hi:
        raise ValueError("date_from позже date_to")
    limit = max(1, min(limit, HISTORY_MAX))
    got, size, stopped = [], 0, False
    async for page in pages(c, cid, lo, hi, before_id):
        for m in page:
            cost = min(len(m.text or ""), text_limit) + 40
            if got and (len(got) >= limit or size + cost > HISTORY_CHARS):
                stopped = True
                break
            got.append(m)
            size += cost
        if stopped:
            break
    lines = await render(c, list(reversed(got)), text_limit)
    hint = ""
    if stopped:
        hint = f"… показано {len(got)}, есть более старые. Продолжить: before_id={got[-1].id}" + (f", date_from={date_from}" if date_from else "")
    return lines, hint


async def scan(c, cid, hit, lo, hi, depth, limit, before_id=0):
    """Просмотр истории подряд (MAX не ищет на сервере): (найденные от новых к старым, просмотрено, остановились по limit)."""
    found, scanned, stopped = [], 0, False
    async for page in pages(c, cid, lo, hi, before_id):
        for m in page:
            scanned += 1
            if hit(m):
                found.append(m)
                if len(found) >= limit:
                    stopped = True
                    break
        if stopped or scanned >= depth:
            break
    return found, scanned, stopped
