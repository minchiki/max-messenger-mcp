"""Инструменты записи: клиент спрашивает владельца перед вызовом. При READ_ONLY=1 не регистрируются."""
import asyncio
from datetime import timedelta

from pymax import File, Photo, Video
from pymax.api.chats.enums import ChannelPermissions
from pymax.api.self.enums import PrivacyAccess
from pymax.api.self.payloads import PrivacySettingsUpdate
from pymax.files.video import VideoNote
from pymax.files.voice import Voice
from pymax.types.domain.attachments.enums import PollFlags
from pymax.types.domain.attachments.keyboards.enums import ButtonType
from pymax.types.domain.attachments.poll import Poll, PollAnswer
from pymax.types.domain.user import ContactInfo

import core
import media
from core import chat_id, get_chat, log, mx, uname
from safety import name

PERMS = {"members": ChannelPermissions.ADD_REMOVE_MEMBER, "admins": ChannelPermissions.ADD_ADMIN,
         "info": ChannelPermissions.CHANGE_CHAT_INFO, "pin": ChannelPermissions.PIN_MESSAGE,
         "post": ChannelPermissions.POST_MESSAGE, "edit": ChannelPermissions.EDIT_MESSAGE,
         "delete": ChannelPermissions.DELETE_MESSAGE}
ACCESS = {"everybody": PrivacyAccess.ALL, "contacts": PrivacyAccess.CONTACTS, "nobody": PrivacyAccess.NOBODY}
PRIVACY = {"find_by_phone": "search_by_phone", "calls": "incoming_calls", "invites": "chat_invites",
           "phone": "phone_number_visibility"}


def to_attach(data, fname, mime, as_="auto"):
    if as_ == "voice":
        return Voice(raw=data, name=fname)
    if as_ == "video_note":
        return VideoNote(raw=data, name=fname)
    if as_ == "photo" or (as_ == "auto" and mime.startswith("image/") and not mime.endswith("gif")):
        return Photo(raw=data, name=fname)
    if as_ == "video" or (as_ == "auto" and mime.startswith("video/")):
        return Video(raw=data, name=fname)
    return File(raw=data, name=fname)


def is_channel(ch):
    return str(ch.type).endswith("CHANNEL")


async def profile(c, **change):
    """change_profile требует имя целиком: подставляем текущие значения для незаданных полей."""
    n = c.me.contact.names[0] if c.me.contact.names else None
    cur = dict(first_name=(n.first_name if n else "") or uname(c.me.contact),
               last_name=n.last_name if n else None, description=c.me.contact.description)
    cur.update({k: v for k, v in change.items() if v})
    await c.change_profile(**cur)


def register(tool, guard, mark_sent):
    # ---------- отправка ----------

    @tool(write=True)
    async def max_send_message(chat: str, text: str, reply_to: int = 0, silent: bool = False, send_in_minutes: int = 0) -> str:
        """Отправить сообщение от имени владельца. Поддерживается Markdown: **жирный**, *курсив*, `код`, [ссылка](url).

        reply_to — id сообщения для ответа; silent — без звука у получателя; send_in_minutes>0 — отложенная отправка."""
        c = await mx()
        cid = await chat_id(c, chat)
        if err := guard(cid, text):
            return err
        m = await c.send_message(cid, text, reply_to=reply_to or None, notify=not silent,
                                 send_at=timedelta(minutes=send_in_minutes) if send_in_minutes > 0 else None)
        mark_sent(cid, m.id, f"len={len(text)}")
        return f"{'Запланировано' if send_in_minutes > 0 else 'Отправлено'} (id {m.id})."

    @tool(write=True, meta=media.FILE_META)
    async def max_send_file(chat: str, file: str | dict, caption: str = "", kind: str = "auto", reply_to: int = 0) -> str:
        """Отправить файл: ссылка http(s) или файл, прикреплённый в чате ChatGPT.

        kind: auto (по типу файла), photo, video, file (документом), voice (голосовое), video_note (кружок)."""
        c = await mx()
        cid = await chat_id(c, chat)
        if err := guard(cid):
            return err
        data, fname, mime = await media.fetch(file)
        m = await c.send_message(cid, caption or None, reply_to=reply_to or None,
                                 attachments=[to_attach(data, fname, mime, kind)])
        mark_sent(cid, m.id, "file")
        return f"Отправлено (id {m.id})."

    @tool(write=True)
    async def max_send_album(chat: str, files: list[str], caption: str = "") -> str:
        """Отправить несколько фото/видео/файлов (2–10 ссылок http(s)) одним сообщением. Файлы качаются параллельно."""
        c = await mx()
        cid = await chat_id(c, chat)
        if err := guard(cid):
            return err
        if not 2 <= len(files) <= 10:
            return "Отказ: нужно от 2 до 10 файлов."
        got = await asyncio.gather(*(media.fetch(f) for f in files))
        m = await c.send_message(cid, caption or None, attachments=[to_attach(*g) for g in got])
        mark_sent(cid, m.id, f"album={len(got)}")
        return f"Отправлено {len(got)} файлов (id {m.id})."

    @tool(write=True)
    async def max_send_poll(chat: str, question: str, options: list[str], revote: bool = True) -> str:
        """Создать опрос (2–12 вариантов); revote — можно переголосовать.

        Анонимность, мультивыбор и викторину MAX сейчас отклоняет («Invalid poll settings»)."""
        c = await mx()
        cid = await chat_id(c, chat)
        if err := guard(cid, question):
            return err
        if not 2 <= len(options) <= 12:
            return "Отказ: нужно от 2 до 12 вариантов."
        flags = PollFlags.FLAG_SETTINGS_REVOTE if revote else PollFlags(0)
        poll = Poll(title=question, answers=[PollAnswer(text=o) for o in options], settings=flags)
        m = await c.send_message(cid, None, attachments=[poll])
        mark_sent(cid, m.id, "poll")
        return f"Опрос отправлен (id {m.id})."

    @tool(write=True)
    async def max_vote_poll(chat: str, message_id: int, options: list[int]) -> str:
        """Проголосовать в опросе: номера вариантов с 0."""
        c = await mx()
        cid = await chat_id(c, chat)
        m = await c.get_message(cid, message_id)
        poll = next((a for a in (m.attaches if m else []) if core.atype(a) == "POLL"), None)
        if poll is None:
            return "Это не опрос."
        await c.vote_poll(cid, message_id, poll.poll_id, [poll.answers[i].answer_id for i in options])
        return "Голос учтён."

    @tool(write=True)
    async def max_forward_messages(from_chat: str, message_ids: list[int], to_chat: str) -> str:
        """Переслать сообщения в другой чат."""
        c = await mx()
        src, dst = await chat_id(c, from_chat), await chat_id(c, to_chat)
        if err := guard(dst):
            return err
        ids = []
        for mid in message_ids:
            ids.append((await c.forward_message(dst, mid, src)).id)
        mark_sent(dst, ",".join(map(str, ids)), "forward")
        return f"Переслано: {len(ids)}."

    @tool(write=True)
    async def max_press_button(chat: str, message_id: int, index: int) -> str:
        """Нажать кнопку под сообщением бота (номер из max_list_buttons)."""
        c = await mx()
        cid = await chat_id(c, chat)
        m = await c.get_message(cid, message_id)
        kb = next((a for a in (m.attaches if m else []) if core.atype(a) == "INLINE_KEYBOARD"), None)
        if kb is None:
            return "Кнопок нет."
        b = [b for row in kb.keyboard.buttons for b in row][index]
        t = b.type
        if t == ButtonType.LINK:
            return f"Это ссылка: {b.url}"
        if t == ButtonType.MESSAGE:
            if err := guard(cid, b.text):
                return err
            sent = await c.send_message(cid, b.text)
            mark_sent(cid, sent.id, "button")
            return f"Отправлен текст кнопки (id {sent.id})."
        if t not in (ButtonType.CALLBACK, ButtonType.CHAT, ButtonType.CLIPBOARD):
            return f"Кнопку типа {str(t).split('.')[-1]} отсюда нажать нельзя."
        r = await c.send_callback(kb.callback_id, t, getattr(b, "payload", None))
        reply = (await core.render(c, [r.message]))[0] if r and r.message else "нет"
        return f"Нажато. Ответ: {reply}"

    @tool(write=True)
    async def max_send_comment(chat: str, post_id: int, text: str, reply_to: int = 0) -> str:
        """Написать комментарий к посту канала."""
        c = await mx()
        cid = await chat_id(c, chat)
        if err := guard(cid, text):
            return err
        m = await c.send_comment(cid, post_id, text, reply_to=reply_to or None)
        mark_sent(cid, m.id, f"comment post={post_id}")
        return f"Комментарий отправлен (id {m.id})."

    # ---------- сообщения ----------

    @tool(write=True)
    async def max_edit_message(chat: str, message_id: int, text: str) -> str:
        """Изменить текст своего сообщения (Markdown поддерживается)."""
        c = await mx()
        await c.edit_message(await chat_id(c, chat), message_id, text)
        return "Изменено."

    @tool(write=True, destructive=True)
    async def max_delete_messages(chat: str, message_ids: list[int], for_everyone: bool = True) -> str:
        """Удалить сообщения (for_everyone=false — только у себя)."""
        c = await mx()
        cid = await chat_id(c, chat)
        await c.delete_message(cid, message_ids, for_me=not for_everyone)
        log(f"MAX DELETE chat={cid} ids={message_ids}")
        return f"Удалено: {len(message_ids)}."

    @tool(write=True)
    async def max_pin_message(chat: str, message_id: int, notify: bool = False) -> str:
        """Закрепить сообщение (notify — уведомить участников)."""
        c = await mx()
        await c.pin_message(await chat_id(c, chat), message_id, notify_pin=notify)
        return "Закреплено."

    @tool(write=True)
    async def max_react(chat: str, message_id: int, emoji: str = "👍") -> str:
        """Поставить реакцию (пустая строка — убрать свою)."""
        c = await mx()
        cid = await chat_id(c, chat)
        await (c.add_reaction(cid, message_id, emoji) if emoji else c.remove_reaction(cid, message_id))
        return "Готово."

    @tool(write=True)
    async def max_mark_read(chat: str) -> str:
        """Отметить чат прочитанным."""
        c = await mx()
        cid = await chat_id(c, chat)
        last = await c.fetch_history(cid, backward=1)
        if last:
            await c.read_message(last[-1].id, cid)
        if cid in c.acc.chats:
            c.acc.chats[cid].new_messages = 0
        return "Прочитано."

    # ---------- группы и каналы ----------

    @tool(write=True)
    async def max_create_group(title: str, user_ids: list[int]) -> str:
        """Создать группу с пользователями (id пользователей)."""
        c = await mx()
        r = await c.create_group(title, user_ids)
        if not r:
            return "MAX не вернул данные группы."
        c.acc.chats[r[0].id] = r[0]
        return f"Группа создана: id={r[0].id}"

    @tool(write=True)
    async def max_add_members(chat: str, user_ids: list[int], show_history: bool = True) -> str:
        """Добавить пользователей в группу или канал."""
        c = await mx()
        cid = await chat_id(c, chat)
        ch = await get_chat(c, cid)
        fn = c.invite_users_to_channel if is_channel(ch) else c.invite_users_to_group
        await fn(cid, user_ids, show_history=show_history)
        return f"Добавлено: {len(user_ids)}."

    @tool(write=True, destructive=True)
    async def max_remove_members(chat: str, user_ids: list[int], delete_their_messages_minutes: int = 0) -> str:
        """Удалить пользователей из группы; можно стереть их сообщения за последние N минут."""
        c = await mx()
        await c.remove_users_from_group(await chat_id(c, chat), user_ids, delete_their_messages_minutes * 60)
        return f"Удалено из группы: {len(user_ids)}."

    @tool(write=True)
    async def max_set_admin(chat: str, user_id: int, permissions: list[str] | None = None) -> str:
        """Назначить администратора. permissions: members, admins, info, pin, post, edit, delete (по умолчанию все)."""
        c = await mx()
        perms = [PERMS[p] for p in (permissions or PERMS)]
        await c.add_admin(await chat_id(c, chat), user_id, perms)
        return "Назначен администратором."

    @tool(write=True, meta={"openai/fileParams": ["photo"]})
    async def max_edit_chat(chat: str, title: str = "", description: str = "", photo: str | dict = "") -> str:
        """Изменить название, описание и/или фото группы или канала (photo — ссылка или файл из чата ChatGPT)."""
        c = await mx()
        pic = None
        if photo:
            data, fname, _ = await media.fetch(photo)
            pic = Photo(raw=data, name=fname)
        await c.change_group_profile(await chat_id(c, chat), name=title or None, description=description or None, photo=pic)
        return "Чат обновлён."

    @tool(write=True)
    async def max_chat_settings(chat: str, all_can_pin: bool | None = None, only_admins_add_members: bool | None = None,
                                only_owner_edits_info: bool | None = None, only_admins_call: bool | None = None,
                                members_see_invite_link: bool | None = None) -> str:
        """Права в группе: кто закрепляет, добавляет участников, меняет название/фото, звонит, видит ссылку."""
        c = await mx()
        await c.change_group_settings(await chat_id(c, chat), all_can_pin_message=all_can_pin,
                                      only_admin_can_add_member=only_admins_add_members,
                                      only_owner_can_change_icon_title=only_owner_edits_info,
                                      only_admin_can_call=only_admins_call, members_can_see_private_link=members_see_invite_link)
        return "Настройки сохранены."

    @tool(write=True)
    async def max_invite_link(chat: str, reset: bool = False) -> str:
        """Ссылка-приглашение группы или канала; reset — выпустить новую (старая перестанет работать)."""
        c = await mx()
        cid = await chat_id(c, chat)
        ch = await c.rework_invite_link(cid) if reset else await c.get_chat(cid)
        return ch.link or "Ссылки нет (нужны права администратора)."

    @tool(write=True)
    async def max_answer_join_requests(chat: str, user_ids: list[int], approve: bool = True) -> str:
        """Принять или отклонить заявки на вступление (id из max_get_join_requests)."""
        c = await mx()
        cid = await chat_id(c, chat)
        await (c.confirm_join_requests(cid, user_ids) if approve else c.decline_join_requests(cid, user_ids))
        return f"{'Принято' if approve else 'Отклонено'}: {len(user_ids)}."

    @tool(write=True)
    async def max_set_comments(chat: str, enabled: bool = True) -> str:
        """Включить или выключить комментарии в канале."""
        c = await mx()
        await c.set_channel_comments(await chat_id(c, chat), enabled)
        return "Комментарии включены." if enabled else "Комментарии выключены."

    @tool(write=True)
    async def max_join_by_link(link: str) -> str:
        """Вступить в группу или канал по ссылке-приглашению."""
        c = await mx()
        ch = await (c.join_channel(link) if "/channel" in link else c.join_group(link))
        c.acc.chats[ch.id] = ch
        return f"Вступил: {ch.id} | {name(ch.title)}"

    @tool(write=True, destructive=True)
    async def max_leave_chat(chat: str) -> str:
        """Выйти из группы или канала."""
        c = await mx()
        cid = await chat_id(c, chat)
        ch = await get_chat(c, cid)
        await (c.leave_channel(cid) if is_channel(ch) else c.leave_group(cid))
        c.acc.chats.pop(cid, None)
        return "Вышел."

    @tool(write=True, destructive=True)
    async def max_delete_chat(chat: str, for_everyone: bool = False) -> str:
        """Удалить чат (историю) у себя или у всех."""
        c = await mx()
        cid = await chat_id(c, chat)
        ch = await get_chat(c, cid)
        await c.delete_chat(cid, last_event_time=ch.last_event_time, for_all=for_everyone)
        c.acc.chats.pop(cid, None)
        log(f"MAX DELETE CHAT {cid}")
        return "Чат удалён."

    # ---------- папки и контакты ----------

    @tool(write=True)
    async def max_manage_folder(action: str, title: str = "", chat_ids: list[int] | None = None, folder_id: str = "") -> str:
        """Папки чатов. action: create (title, chat_ids), update (folder_id, title, chat_ids), delete (folder_id)."""
        c = await mx()
        if action == "create":
            r = await c.create_folder(title, chat_ids or [])
            return f"Папка создана: {r.folder.id if r.folder else '?'}"
        if action == "update":
            await c.update_folder(folder_id, title, chat_include=chat_ids)
            return "Папка обновлена."
        if action == "delete":
            await c.delete_folder(folder_id)
            return "Папка удалена."
        return "action: create, update или delete."

    @tool(write=True)
    async def max_manage_contact(action: str, user_id: int = 0, phone: str = "", first_name: str = "", last_name: str = "") -> str:
        """Контакты. action: add (user_id), remove (user_id), import (phone, first_name, last_name — добавить по номеру)."""
        c = await mx()
        if action == "add":
            return f"Добавлен: {uname(await c.add_contact(user_id))}"
        if action == "remove":
            await c.remove_contact(user_id)
            return "Удалён из контактов."
        if action == "import":
            us = await c.import_contacts([ContactInfo(phone=phone, first_name=first_name or phone, last_name=last_name or None)])
            return "\n".join(f"{u.id} | {uname(u)}" for u in us) or "Номер не зарегистрирован в MAX."
        return "action: add, remove или import."

    # ---------- профиль ----------

    @tool(write=True)
    async def max_update_profile(first_name: str = "", last_name: str = "", description: str = "") -> str:
        """Изменить имя, фамилию или описание (пустые поля не меняются)."""
        await profile(await mx(), first_name=first_name, last_name=last_name, description=description)
        return "Профиль обновлён."

    @tool(write=True, meta=media.FILE_META)
    async def max_set_avatar(file: str | dict) -> str:
        """Поставить новую аватарку: ссылка на картинку или файл из чата ChatGPT."""
        data, fname, _ = await media.fetch(file)
        await profile(await mx(), photo=Photo(raw=data, name=fname))
        return "Аватарка обновлена."

    @tool(write=True)
    async def max_set_privacy(setting: str, value: str) -> str:
        """Приватность. setting: find_by_phone, calls, invites, phone (value: everybody, contacts, nobody)
        или hide_online, safe_content (value: on, off)."""
        c = await mx()
        if setting in PRIVACY:
            upd = PrivacySettingsUpdate(**{PRIVACY[setting]: ACCESS[value]})
        elif setting in ("hide_online", "safe_content"):
            upd = PrivacySettingsUpdate(**{"hide_online_status" if setting == "hide_online" else "safe_content_only": value == "on"})
        else:
            return "Неизвестная настройка."
        await c.change_profile_settings(upd)
        return f"{setting}: {value}."

    @tool(write=True, meta=media.FILE_META)
    async def max_post_story(file: str | dict, link: str = "") -> str:
        """Опубликовать историю из фото или видео (ссылка или файл из чата ChatGPT); link — ссылка на истории."""
        c = await mx()
        if err := guard(None):
            return err
        data, fname, mime = await media.fetch(file)
        s = await c.send_story(Video(raw=data, name=fname) if mime.startswith("video/") else Photo(raw=data, name=fname),
                               link=link or None)
        mark_sent("story", s.id, "file")
        return f"История опубликована (id {s.id})."

    @tool(write=True, destructive=True)
    async def max_delete_story(story_id: int) -> str:
        """Удалить свою историю."""
        await (await mx()).delete_story(story_id)
        return "История удалена."
