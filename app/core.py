"""Постоянное подключение к MAX, кэш чатов и имён, живая лента событий.

Подключение одно на весь процесс: вызов инструмента не ждёт рукопожатия (~0,1 с вместо ~1 с).
Список чатов держится в памяти и обновляется событиями, поэтому max_list_chats и поиск чата
по названию не ходят в сеть. События (новые, изменённые, удалённые сообщения, реакции)
складываются в ленту: max_get_updates и max_wait_for_message читают её без опроса чатов.
"""
import asyncio
import collections
import inspect
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from cfg import make_client, session_exists
from safety import clean, name

TZ = ZoneInfo(os.environ.get("TZ_NAME", "Europe/Moscow"))
FEED_SIZE = int(os.environ.get("MAX_FEED_SIZE", "2000"))
SAVED = 0  # «Избранное» в MAX — диалог с id 0
STARTED = time.time()

chats = {}               # id -> Chat, свежие данные приходят событиями
feed = collections.deque(maxlen=FEED_SIZE)
_client = None
_lock = asyncio.Lock()
_new = asyncio.Condition()
_keeper = None
_seq = 0


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ---------- подключение ----------

async def mx():
    """Клиент MAX: готовое подключение или новое, если связь оборвалась."""
    global _keeper
    c = _client
    if c is None or not c.is_connected or c.me is None:
        async with _lock:
            if _client is None or not _client.is_connected or _client.me is None:
                await _connect()
        c = _client
    if _keeper is None or _keeper.done():
        _keeper = asyncio.create_task(_keep())
    return c


async def warm():
    try:
        await mx()
        log(f"MAX connected: {len(chats)} chats")
    except Exception as e:
        log(f"MAX warmup failed: {e}")


async def _connect():
    global _client
    if not session_exists():
        raise RuntimeError("Сессия MAX не создана: выполните login.sh")
    if _client is not None:
        try:
            await _client.close()
        except Exception:
            pass
    c = make_client()
    _attach(c)
    await c.connect()
    if c.me is None:
        raise RuntimeError("Не удалось войти в MAX: выполните login.sh заново")
    _client = c
    await load_chats(c)


async def _keep():
    """Следит за связью: переподключает сразу, а не при следующем вызове инструмента."""
    delay = 5
    while True:
        await asyncio.sleep(15)
        if _client is not None and _client.is_connected:
            delay = 5
            continue
        try:
            async with _lock:
                await _connect()
            log("MAX reconnected")
            delay = 5
        except Exception as e:
            log(f"MAX reconnect failed: {e}")
            await asyncio.sleep(min(delay, 300))
            delay *= 2


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
    chats.clear()
    chats.update(got)


# ---------- лента событий ----------

async def push(kind, chat_id, msg=None, **extra):
    global _seq
    _seq += 1
    feed.append(dict(seq=_seq, kind=kind, chat=chat_id, msg=msg, at=time.time(), **extra))
    async with _new:
        _new.notify_all()


def last_seq():
    return _seq


async def wait_feed(pred, since, timeout):
    """События после since, подходящие под pred; ждёт до timeout секунд, если их ещё нет."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    async with _new:
        while True:
            hit = [e for e in feed if e["seq"] > since and pred(e)]
            left = deadline - loop.time()
            if hit or left <= 0:
                return hit
            try:
                await asyncio.wait_for(_new.wait(), left)
            except asyncio.TimeoutError:
                pass


def _attach(c):
    @c.on_message()
    async def _msg(m, *_):
        ch = chats.get(m.chat_id)
        if ch is None:
            asyncio.create_task(_add_chat(c, m.chat_id))
        else:
            try:
                ch.last_event_time, ch.last_message = m.time, m
                if m.sender != me_id(c):
                    ch.new_messages += 1
            except Exception:
                pass
        await push("new", m.chat_id, m)

    @c.on_message_edit()
    async def _edit(m, *_):
        await push("edit", m.chat_id, m)

    @c.on_message_delete()
    async def _delete(e, *_):
        await push("delete", e.chat_id, ids=list(e.message_ids))

    @c.on_reaction_update()
    async def _reaction(e, *_):
        counters = " ".join(f"{x.reaction}{x.count}" for x in (e.counters or []))
        await push("reaction", e.chat_id, ids=[e.message_id], text=counters)

    @c.on_message_read()
    async def _read(e, *_):
        ch = chats.get(e.chat_id)
        if ch is not None and e.user_id == me_id(c) and not e.set_as_unread:
            ch.new_messages = 0

    @c.on_chat_update()
    async def _chat(ch, *_):
        chats[ch.id] = ch


async def _add_chat(c, chat_id):
    try:
        chats[chat_id] = await c.get_chat(chat_id)
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
    if not chats:
        await load_chats(c)
    t = await titles(c, list(chats.values()))
    q = s.lstrip("@").casefold()
    hit = [i for i, n in t.items() if n.casefold() == q] or [i for i, n in t.items() if q in n.casefold()]
    if len(hit) == 1:
        return hit[0]
    if not hit:
        raise ValueError(f"Чат «{s}» не найден, посмотрите max_list_chats")
    raise ValueError("Подходят несколько чатов, укажите id: " + "; ".join(f"{i} {t[i]}" for i in hit[:8]))


async def get_chat(c, cid):
    ch = chats.get(cid)
    if ch is None:
        ch = chats[cid] = await c.get_chat(cid)
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


def fmt(m, who):
    body = " ".join(x for x in (kind(m), clean(m.text)) if x)
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


async def render(c, msgs):
    me = me_id(c)
    who = await names(c, [m.sender for m in msgs if m.sender != me])
    return [fmt(m, "я" if m.sender == me else who.get(m.sender, str(m.sender))) for m in msgs]
