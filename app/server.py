"""MCP-сервер для личного аккаунта MAX (HTTP для ChatGPT/Claude, stdio для локальных клиентов).

Один владелец (по умолчанию): секретный путь /<MAX_SECRET>/mcp, сессия из login.sh.
Много пользователей (MAX_MULTIUSER=1): адрес /mcp, вход через OAuth и QR на странице сервиса,
у каждого свой зашифрованный аккаунт.
Защита: белый список отправки MAX_SEND_ALLOW ("me" = Избранное, id чатов через запятую, "*" = любые),
лимит MAX_SEND_PER_MIN на аккаунт, READ_ONLY=1 — только чтение.
"""
import asyncio
import functools
import os
import sys
import time
import traceback

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

import core
import tools_read
import tools_write
from safety import INSTRUCTIONS

STDIO = "--stdio" in sys.argv
MULTI = os.environ.get("MAX_MULTIUSER", "").lower() in ("1", "true", "yes")
SECRET = os.environ.get("MAX_SECRET", "")
HOST = os.environ.get("MAX_HOST", "127.0.0.1")  # в Docker — 0.0.0.0
PORT = int(os.environ.get("MAX_PORT", "8766"))
SEND_ALLOW = {"0" if x.strip().lower() in ("me", "saved") else x.strip()
              for x in os.environ.get("MAX_SEND_ALLOW", "*" if MULTI else "me").split(",") if x.strip()}
SEND_PER_MIN = int(os.environ.get("MAX_SEND_PER_MIN", "20"))
READ_ONLY = os.environ.get("READ_ONLY", "").lower() in ("1", "true", "yes")

if MULTI:
    import oauth
    mcp = MCPServer("max-messenger-mcp", instructions=INSTRUCTIONS, version="1.1.0",
                    auth_server_provider=oauth.provider, auth=oauth.settings())
    core.resolver = oauth.resolve
else:
    if not STDIO and len(SECRET) < 24:
        raise SystemExit("MAX_SECRET должен быть не короче 24 символов")
    mcp = MCPServer("max-messenger-mcp", instructions=INSTRUCTIONS, version="1.1.0")


def tool(write=False, destructive=False, **kw):
    """mcp.tool с подсказками для клиента; ошибка возвращается модели текстом.

    Инструменты чтения помечены read-only — ChatGPT вызывает их без подтверждения.
    При READ_ONLY=1 инструменты записи не регистрируются.
    """
    def deco(fn):
        if write and READ_ONLY:
            return fn

        @functools.wraps(fn)
        async def wrapper(*a, **k):
            try:
                return await fn(*a, **k)
            except Exception as e:
                traceback.print_exc()
                return f"Ошибка: {type(e).__name__}: {e}"
        ann = ToolAnnotations(read_only_hint=not write, destructive_hint=destructive, open_world_hint=True)
        return mcp.tool(annotations=ann, **kw)(wrapper)
    return deco


def guard(cid, text=None):
    """Белый список, длина и лимит в минуту на аккаунт. None = можно. cid=None — проверить только лимит."""
    if cid is not None and "*" not in SEND_ALLOW and str(cid) not in SEND_ALLOW:
        return f"Отказ: чат {cid} не в белом списке отправки (MAX_SEND_ALLOW)."
    if text is not None and (not text.strip() or len(text) > 4000):
        return "Отказ: пустой или слишком длинный текст."
    sent, now = core.CUR.get().sent, time.time()
    while sent and now - sent[0] > 60:
        sent.popleft()
    if len(sent) >= SEND_PER_MIN:
        return f"Отказ: лимит {SEND_PER_MIN} отправок в минуту."
    return None


def mark_sent(cid, msg_id, what):
    a = core.CUR.get()
    a.sent.append(time.time())
    core.log(f"MAX SEND [{a.key}] chat={cid} id={msg_id} {what}")


tools_read.register(tool, Image)
tools_write.register(tool, guard, mark_sent)
if MULTI:
    oauth.setup(mcp, tool)  # страницы входа, главная и max_logout


# ---------- готовые сценарии (в Claude — слэш-команды) ----------

@mcp.prompt()
def unread_digest() -> str:
    """Сводка непрочитанного в MAX."""
    return ("Вызови max_get_unread и сделай короткую сводку по каждому чату: кто писал, что нужно от меня, "
            "что срочно. В конце — список дел. Ничего не отправляй.")


@mcp.prompt()
def reply_helper(chat: str) -> str:
    """Помочь ответить в чате MAX."""
    return (f"Прочитай последние 30 сообщений чата «{chat}» (max_read_messages), кратко перескажи, "
            "о чём речь, и предложи 2–3 варианта ответа. Отправляй только после моего выбора.")


@mcp.prompt()
def channel_summary(chat: str, limit: int = 300) -> str:
    """Пересказ канала или группы MAX."""
    return (f"Выгрузи {limit} последних сообщений «{chat}» через max_export_chat и перескажи главное: "
            "темы, решения, ссылки, кто активнее всех.")


class Asgi:
    """Подключается к MAX при старте сервера; MAX_DEBUG=1 пишет заголовки и ошибки запросов в журнал."""

    def __init__(self, app, debug):
        self.app, self.debug = app, debug

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            async def recv():
                m = await receive()
                if m["type"] == "lifespan.startup":
                    asyncio.create_task(core.warm())
                return m
            return await self.app(scope, recv, send)
        if scope["type"] != "http" or not self.debug:
            return await self.app(scope, receive, send)
        hdrs = {k.decode(): v.decode() for k, v in scope["headers"] if k not in (b"cookie", b"authorization")}
        core.log(f"DEBUG {scope['method']} headers={hdrs}")
        status = {}

        async def snd(m):
            if m["type"] == "http.response.start":
                status["code"] = m["status"]
            elif m["type"] == "http.response.body" and status.get("code", 200) >= 400:
                core.log(f"DEBUG {status['code']} body={m.get('body', b'')[:400]!r}")
            await send(m)

        await self.app(scope, receive, snd)


if __name__ == "__main__":
    if STDIO:
        mcp.run("stdio")
    else:
        import uvicorn
        app = mcp.streamable_http_app(host=HOST, streamable_http_path="/mcp" if MULTI else f"/{SECRET}/mcp", stateless_http=True,
                                      transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
        uvicorn.run(Asgi(app, bool(os.environ.get("MAX_DEBUG"))), host=HOST, port=PORT)
