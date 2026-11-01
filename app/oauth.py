"""Многопользовательский режим: OAuth 2.1 для ChatGPT/Claude и вход в MAX по QR на нашей странице.

Поток: клиент регистрируется (DCR) → /authorize → /login/<id> (QR + облачный пароль) →
сессия MAX шифруется и сохраняется → код авторизации → токены. Каждый токен привязан к id
пользователя MAX (subject), по нему core выбирает аккаунт для вызова инструмента.
Вход по номеру и СМС web-версия MAX закрывает капчей, поэтому здесь только QR.
"""
import asyncio
import os
import secrets
import time
from urllib.parse import urlparse

import qrcode
import qrcode.image.svg
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import (AccessToken, AuthorizationCode, AuthorizeError, RefreshToken,
                                      RegistrationError, construct_redirect_uri)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pymax.auth.qr import QrAuthFlow
from pymax.auth.exceptions import PasswordAttemptsExceededError
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

import core
import pages
import store
from cfg import make_client
from safety import clean

BASE = os.environ.get("MAX_PUBLIC_URL", "").rstrip("/")
MAX_USERS = int(os.environ.get("MAX_MAX_USERS", "100"))
ACCESS_TTL = 86400
REFRESH_TTL = 90 * 86400
SCOPES = ["max"]
LOGINS_RUNNING = 20  # одновременных входов (каждый держит подключение к MAX)
# Куда можно вернуть код авторизации. Иначе любой зарегистрирует свой сайт (или javascript:),
# пришлёт жертве ссылку входа и получит доступ к её MAX.
REDIRECT_HOSTS = {h.strip().lower() for h in os.environ.get(
    "MAX_REDIRECT_HOSTS", "chatgpt.com,chat.openai.com,claude.ai,claude.com").split(",") if h.strip()}
APP_SCHEMES = {s.strip().lower() for s in os.environ.get(
    "MAX_REDIRECT_SCHEMES", "cursor,vscode,vscode-insiders,windsurf").split(",") if s.strip()}
LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def redirect_ok(uri):
    """https на разрешённые хосты, http(s) на localhost (Claude Code, MCP Inspector), схемы приложений."""
    u = urlparse(str(uri))
    scheme, host = u.scheme.lower(), (u.hostname or "").lower()
    if scheme in ("http", "https") and host in LOOPBACK:
        return True
    if scheme == "https":
        return host in REDIRECT_HOSTS
    return scheme in APP_SCHEMES

pending = {}   # id входа -> Login
codes = {}     # код авторизации -> AuthorizationCode (живёт 5 минут)
_tokens = {}   # кэш проверенных токенов: токен -> (AccessToken, до какого времени верить)


def settings():
    if not BASE.startswith("https://") and "localhost" not in BASE and "127.0.0.1" not in BASE:
        raise SystemExit("MAX_PUBLIC_URL: нужен публичный https-адрес сервиса, например https://max.example.com")
    return AuthSettings(
        issuer_url=BASE, resource_server_url=f"{BASE}/mcp", validate_token_resource=False,
        client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=SCOPES, default_scopes=SCOPES),
        revocation_options=RevocationOptions(enabled=True), required_scopes=SCOPES,
    )


def resolve():
    """Для core: аккаунт по OAuth-токену текущего запроса."""
    t = get_access_token()
    if t is None or not t.subject:
        raise PermissionError("Нет авторизации: подключите сервер заново")
    uid = t.subject
    return uid, lambda: make_client(store=store.EncryptedStore(uid))


async def _issue(client_id, uid, scopes, resource):
    access, refresh, grant = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_hex(8)
    now = time.time()
    await store.save_token(access, "access", grant, uid, client_id, scopes, resource, now + ACCESS_TTL)
    await store.save_token(refresh, "refresh", grant, uid, client_id, scopes, resource, now + REFRESH_TTL)
    return OAuthToken(access_token=access, token_type="Bearer", expires_in=ACCESS_TTL, refresh_token=refresh,
                      scope=" ".join(scopes))


class Provider:
    async def get_client(self, client_id):
        info = await store.load_client(client_id)
        return OAuthClientInformationFull.model_validate_json(info) if info else None

    async def register_client(self, client_info):
        bad = [str(u) for u in client_info.redirect_uris or [] if not redirect_ok(u)]
        if bad:
            raise RegistrationError("invalid_redirect_uri", f"Адрес возврата не разрешён: {bad[0][:100]}")
        await store.save_client(client_info.client_id, client_info.model_dump_json())

    async def authorize(self, client, params):
        if not redirect_ok(params.redirect_uri):
            raise AuthorizeError("invalid_request", "Адрес возврата не разрешён")
        await sweep()
        if len(pending) >= 300:
            raise AuthorizeError("temporarily_unavailable", "Слишком много входов одновременно, попробуйте через минуту")
        p = Login(client, params)
        pending[p.id] = p
        return f"{BASE}/login/{p.id}"

    async def load_authorization_code(self, client, authorization_code):
        c = codes.get(authorization_code)
        return c if c and c.client_id == client.client_id and c.expires_at > time.time() else None

    async def exchange_authorization_code(self, client, authorization_code):
        codes.pop(authorization_code.code, None)
        return await _issue(client.client_id, authorization_code.subject, authorization_code.scopes, authorization_code.resource)

    async def load_refresh_token(self, client, refresh_token):
        r = await store.load_token(refresh_token, "refresh")
        if not r or r["client_id"] != client.client_id:
            return None
        return RefreshToken(token=refresh_token, client_id=r["client_id"], scopes=r["scopes"], expires_at=int(r["expires"]),
                            resource=r["resource"], subject=r["user_id"])

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        r = await store.load_token(refresh_token.token, "refresh")
        if r:
            await store.drop_grant(r["grant_id"])
        _tokens.clear()
        return await _issue(client.client_id, refresh_token.subject, scopes or refresh_token.scopes, refresh_token.resource)

    async def load_access_token(self, token):
        hit = _tokens.get(token)
        if hit and hit[1] > time.time():
            return hit[0]
        r = await store.load_token(token, "access")
        if not r:
            return None
        at = AccessToken(token=token, client_id=r["client_id"], scopes=r["scopes"], expires_at=int(r["expires"]),
                         resource=r["resource"], subject=r["user_id"])
        if len(_tokens) > 5000:
            _tokens.clear()
        _tokens[token] = (at, time.time() + 60)
        return at

    async def revoke_token(self, token):
        r = await store.load_token(token.token, "access") or await store.load_token(token.token, "refresh")
        if r:
            await store.drop_grant(r["grant_id"])
        _tokens.clear()


provider = Provider()


async def sweep():
    """Убрать брошенные входы, просроченные коды и токены."""
    now = time.time()
    for k, p in list(pending.items()):
        if now - p.created > 900:
            await p.stop()
            pending.pop(k, None)
    for k, c in list(codes.items()):
        if c.expires_at < now:
            codes.pop(k, None)
    await store.cleanup()


async def janitor():
    while True:
        await asyncio.sleep(300)
        try:
            await sweep()
        except Exception as e:
            core.log(f"sweep failed: {e}")


# ---------- вход в MAX ----------

class Login:
    """Один вход: QR-сценарий PyMax в фоне, страница опрашивает состояние."""

    def __init__(self, client, params):
        self.id = secrets.token_urlsafe(24)
        self.client, self.params = client, params
        self.created = time.time()
        self.status, self.qr, self.hint, self.error, self.redirect = "new", None, None, None, None
        self.task, self.mx, self._pw, self._pw_tries = None, None, None, 0

    def start(self):
        if self.task is None or self.task.done():
            running = sum(1 for p in pending.values() if p.task is not None and not p.task.done())
            if running >= LOGINS_RUNNING:
                self.status, self.error = "error", "Сейчас много входов одновременно, попробуйте через минуту"
                return
            self.status, self.error = "starting", None
            self.task = asyncio.create_task(self._run())

    async def stop(self):
        if self.task is not None and not self.task.done():
            self.task.cancel()
        if self.mx is not None:
            try:
                await self.mx.close()
            except Exception:
                pass

    async def show_qr(self, qr_url):            # QrHandler для PyMax
        if not str(qr_url).startswith("https://max.ru/"):  # ссылка уходит в href на странице
            raise RuntimeError("MAX прислал неожиданную ссылку входа")
        self.qr, self.status = qr_url, "qr"

    async def get_password(self, hint=None):    # PasswordProvider для PyMax
        self._pw_tries += 1
        self.status, self.hint = "password", hint
        self.error = "Неверный пароль, попробуйте ещё раз" if self._pw_tries > 1 else None
        self._pw = asyncio.get_running_loop().create_future()
        try:
            return await asyncio.wait_for(self._pw, 600)
        finally:
            self.status = "connecting"

    def password(self, value):
        if self._pw is not None and not self._pw.done():
            self._pw.set_result(value)

    async def _run(self):
        mem = store.MemoryStore()
        self.mx = c = make_client(store=mem, auth_flow=QrAuthFlow(self, self), password_attempts=3)
        try:
            await c.connect()
            if c.me is None or mem.info is None:
                raise RuntimeError("MAX не подтвердил вход")
            me = c.me.contact
            uid = str(me.id)
            if not await store.user_exists(uid) and await store.count_users() >= MAX_USERS:
                raise RuntimeError("Сервис заполнен: новых пользователей пока не принимаем")
            await store.save_user(uid, core.uname(me), mem.info)
            await core.forget(uid)  # старое подключение пользователя, если было, переподключится с новой сессией
            p = self.params
            code = AuthorizationCode(code=secrets.token_urlsafe(32), scopes=p.scopes or SCOPES, expires_at=time.time() + 300,
                                     client_id=self.client.client_id, code_challenge=p.code_challenge, redirect_uri=p.redirect_uri,
                                     redirect_uri_provided_explicitly=p.redirect_uri_provided_explicitly,
                                     resource=p.resource, subject=uid)
            codes[code.code] = code
            self.redirect = construct_redirect_uri(str(p.redirect_uri), code=code.code, state=p.state)
            self.status = "done"
            core.log(f"MAX login ok user={uid} client={self.client.client_id}")
        except PasswordAttemptsExceededError:
            self.status, self.error = "error", "Три неверных пароля подряд. Начните вход заново."
        except Exception as e:
            if "expired" in str(e):
                self.status = "expired"
            else:
                self.status, self.error = "error", clean(str(e), 200) or "Не удалось войти, попробуйте снова"
                core.log(f"MAX login failed: {type(e).__name__}: {clean(str(e), 300)}")
        finally:
            try:
                await c.close()
            except Exception:
                pass
            self.mx = None


def _svg(link):
    img = qrcode.make(link, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=1)
    return img.to_string(encoding="unicode")


# Страницы без внешних ресурсов: CSP не даст подгрузить чужой скрипт, даже если что-то просочится в HTML
PAGE_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                               "connect-src 'self'; img-src data:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}


def setup(mcp, tool):
    @mcp.custom_route("/", methods=["GET"])
    async def home(request: Request):
        return HTMLResponse(pages.home(BASE, await store.count_users(), MAX_USERS), headers=PAGE_HEADERS)

    @mcp.custom_route("/privacy", methods=["GET"])
    async def privacy(request: Request):
        return HTMLResponse(pages.privacy(), headers=PAGE_HEADERS)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request):
        return JSONResponse({"ok": True, "online": sum(a.alive() for a in core.accounts.values())})

    @mcp.custom_route("/login/{pid}", methods=["GET"])
    async def login_page(request: Request):
        p = pending.get(request.path_params["pid"])
        if p is None:
            return HTMLResponse(pages.gone(), status_code=404, headers=PAGE_HEADERS)
        u = urlparse(str(p.params.redirect_uri))
        return HTMLResponse(pages.login(p.id, p.client.client_name, u.hostname or f"приложение {u.scheme}"), headers=PAGE_HEADERS)

    @mcp.custom_route("/login/{pid}/start", methods=["POST"])
    async def login_start(request: Request):
        p = pending.get(request.path_params["pid"])
        if p is None:
            return JSONResponse({"status": "error", "error": "Ссылка входа устарела"}, status_code=404)
        p.start()
        return JSONResponse({"status": p.status})

    @mcp.custom_route("/login/{pid}/state", methods=["GET"])
    async def login_state(request: Request):
        p = pending.get(request.path_params["pid"])
        if p is None:
            return JSONResponse({"status": "error", "error": "Ссылка входа устарела, подключите сервер заново"})
        out = {"status": p.status, "hint": p.hint, "error": p.error}
        if p.status == "qr":
            out |= {"qr_link": p.qr, "qr_svg": _svg(p.qr)}
        if p.status == "done":
            out["redirect"] = p.redirect
            pending.pop(p.id, None)
        return JSONResponse(out, headers={"Cache-Control": "no-store"})

    @mcp.custom_route("/login/{pid}/password", methods=["POST"])
    async def login_password(request: Request):
        p = pending.get(request.path_params["pid"])
        if p is None:
            return JSONResponse({"status": "error"}, status_code=404)
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"status": "error"}, status_code=400)
        p.password(str(body.get("password", "") if isinstance(body, dict) else "")[:256])
        return JSONResponse({"status": "ok"})

    @tool(write=True, destructive=True)
    async def max_logout() -> str:
        """Выйти из MAX на этом сервисе и удалить все данные о вас: сессию MAX, токены доступа.

        Ассистент потеряет доступ; чтобы вернуть его, подключите сервер заново."""
        uid = get_access_token().subject
        try:
            await (await core.mx()).logout()  # завершает сеанс этого устройства в самом MAX
        except Exception:
            pass
        await core.forget(uid)
        await store.delete_user(uid)
        _tokens.clear()
        core.log(f"MAX logout user={uid}")
        return "Готово: сеанс в MAX завершён, данные о вас удалены с сервера."
