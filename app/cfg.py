"""Настройки PyMax: сессия в MAX_DIR/max.db, без «в сети» и без телеметрии."""
import os

from pymax import ExtraConfig, WebClient
from pymax.api.messages import payloads as _p

# PyMax 2.5 шлёт id пересылаемого сообщения строкой, MAX отвечает «Expected number»
_p.ForwardLink.model_fields["message_id"].annotation = int
for _m in (_p.ForwardLink, _p.ForwardMessagePayloadMessage, _p.ForwardMessagePayload):
    _m.model_rebuild(force=True)

MAX_DIR = os.environ.get("MAX_DIR", ".")
MAX_SESSION = "max.db"


def session_exists() -> bool:
    return os.path.exists(os.path.join(MAX_DIR, MAX_SESSION))


class QuietWebClient(WebClient):
    """Постоянное подключение не должно держать аккаунт «в сети»."""

    async def _prepare_config(self):
        config = await super()._prepare_config()
        config.interactive = False
        return config


def make_client(store=None, password_attempts=None, **kw) -> WebClient:
    """store — своё хранилище сессии (многопользовательский режим), иначе MAX_DIR/max.db."""
    extra = ExtraConfig(reconnect=False, relogin=False, telemetry=False, log_level="WARNING",
                        store=store, password_max_attempts=password_attempts)
    # переподключением занимается core.Account: так обработчики событий не теряются
    return QuietWebClient(session_name=MAX_SESSION, work_dir=MAX_DIR, extra_config=extra, **kw)
