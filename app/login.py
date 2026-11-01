"""Вход в MAX по QR: сохраняет сессию в MAX_DIR/max.db для MCP-сервера."""
import asyncio
import os

from pymax import ConsoleQrHandler

from cfg import MAX_DIR, MAX_SESSION, make_client, session_exists


class Qr(ConsoleQrHandler):
    async def show_qr(self, qr_url):
        print("\nОтсканируйте QR камерой в приложении MAX на телефоне (вход на другом устройстве):\n")
        await super().show_qr(qr_url)


async def main():
    if session_exists():
        print(f"Сессия MAX уже есть; для повторного входа удалите {os.path.join(MAX_DIR, MAX_SESSION)}")
    c = make_client(qr_provider=Qr())
    try:
        await c.connect()
        me = c.me.contact
        who = next((n.name or n.first_name for n in me.names if n.name or n.first_name), "")
        print(f"Готово: вошли в MAX как {who} (id {me.id})")
    finally:
        await c.close()


asyncio.run(main())
