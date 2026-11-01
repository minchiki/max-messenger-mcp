"""HTML страниц многопользовательского режима: главная, вход в MAX, конфиденциальность. Без внешних скриптов и шрифтов."""
import os
from html import escape

CSS = """
:root{--bg:#0e0820;--bg2:#1a0f3a;--card:rgba(255,255,255,.06);--line:rgba(167,139,250,.28);--text:#f5f3ff;
--muted:#c4b5fd;--dim:#a78bfa;--accent:#8b5cf6;--accent2:#7c3aed;--ok:#34d399;--bad:#fb7185}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;min-height:100vh;font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--text);
background:radial-gradient(900px 500px at 85% -10%,rgba(139,92,246,.35),transparent 70%),linear-gradient(160deg,var(--bg),var(--bg2) 60%,#2a1a52)}
main{max-width:720px;margin:0 auto;padding:32px 16px 48px}
.logo{display:flex;align-items:center;gap:12px;font-weight:700;font-size:18px;color:var(--muted)}
.mark{width:40px;height:40px;border-radius:12px;display:grid;place-items:center;font-weight:900;color:#fff;
background:linear-gradient(135deg,var(--accent),var(--accent2));box-shadow:0 8px 24px rgba(124,58,237,.45)}
h1{font-size:clamp(30px,7vw,48px);line-height:1.1;margin:28px 0 12px;letter-spacing:-.02em}
h2{font-size:20px;margin:0 0 10px}
p{margin:0 0 12px;color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:20px;margin:16px 0;backdrop-filter:blur(8px)}
.url{display:flex;gap:8px;flex-wrap:wrap}
.url code{flex:1 1 260px;min-width:0;overflow-wrap:anywhere;font:15px/1.4 ui-monospace,Consolas,monospace;padding:12px 14px;
border-radius:12px;background:rgba(0,0,0,.35);border:1px solid var(--line);color:#fff}
button,.btn{appearance:none;border:0;border-radius:12px;padding:12px 18px;font:600 16px system-ui,sans-serif;color:#fff;cursor:pointer;
background:linear-gradient(135deg,var(--accent),var(--accent2));text-decoration:none;display:inline-flex;align-items:center;justify-content:center;gap:8px}
.btn.ghost{background:transparent;border:1px solid var(--line);color:var(--muted)}
ol{margin:0;padding-left:22px;color:var(--muted)}li{margin:6px 0}b{color:var(--text)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
.grid .card{margin:0}
.qr{background:#fff;border-radius:18px;padding:14px;width:min(280px,100%);aspect-ratio:1;margin:8px auto 16px;display:grid;place-items:center}
.qr svg{width:100%;height:100%}
.center{text-align:center}
.who{display:flex;gap:12px;align-items:flex-start}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;background:rgba(139,92,246,.18);border:1px solid var(--line);color:var(--muted);font-size:13px}
input{width:100%;padding:13px 14px;border-radius:12px;border:1px solid var(--line);background:rgba(0,0,0,.35);color:#fff;font-size:17px;margin:4px 0 12px}
.err{color:var(--bad)}.ok{color:var(--ok)}
.spin{width:28px;height:28px;border-radius:50%;border:3px solid var(--line);border-top-color:var(--accent);animation:s 1s linear infinite;margin:16px auto}
@keyframes s{to{transform:rotate(360deg)}}
.hidden{display:none}
small,.small{font-size:13px;color:var(--dim)}
footer{margin-top:28px;font-size:13px;color:var(--dim)}footer a{color:var(--muted)}
"""

HEAD = ('<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="dark"><title>{title}</title><style>' + CSS.replace("{", "{{").replace("}", "}}") + '</style></head><body><main>'
        '<div class="logo"><div class="mark">M</div>MAX для ИИ-ассистентов</div>')
CONTACT = os.environ.get("MAX_OPERATOR_CONTACT", "https://t.me/huicoding")
FOOT = ('<footer><a href="/privacy">Конфиденциальность</a> · открытый код: <a href="https://github.com/minchiki/max-messenger-mcp">max-messenger-mcp</a> · '
        'новости: <a href="https://t.me/huicoding">HUICODING</a>. Сервис не связан с VK и MAX.</footer></main></body></html>')


def home(base, users, limit):
    mcp = f"{base}/mcp"
    full = users >= limit
    return HEAD.format(title="MAX для ChatGPT и Claude") + f"""
<h1>Ваш MAX — в ChatGPT и Claude</h1>
<p>Вставьте ссылку в ассистента, войдите по QR — и он сможет читать чаты, искать, отвечать, делать сводки непрочитанного и ждать ответа собеседника.</p>
<div class="card">
  <h2>Адрес сервера</h2>
  <div class="url"><code id="u">{escape(mcp)}</code><button onclick="navigator.clipboard.writeText(document.getElementById('u').textContent);this.textContent='Скопировано'">Копировать</button></div>
  {'<p class="err" style="margin-top:12px">Сейчас мест нет: сервис заполнен. Можно поставить свой сервер по инструкции на GitHub.</p>' if full else ''}
</div>
<div class="grid">
  <div class="card"><h2>ChatGPT</h2><ol><li>Настройки → <b>Apps / Plugins</b> → <b>Add</b> → <b>Custom MCP server</b></li>
  <li>Вставьте адрес, авторизация — <b>OAuth</b></li><li>Откроется страница входа — отсканируйте QR в MAX</li></ol></div>
  <div class="card"><h2>Claude</h2><ol><li>Settings → <b>Connectors</b> → <b>Add custom connector</b></li>
  <li>Вставьте адрес и нажмите <b>Connect</b></li><li>Войдите в MAX по QR</li></ol></div>
</div>
<div class="card"><h2>Что умеет</h2>
<p>58 инструментов: чаты и поиск, сводка непрочитанного, живая лента новых сообщений, файлы и фото, вся история чата за любой период, опросы, кнопки ботов, группы и каналы, папки, контакты, профиль. Отправка и удаление — только после вашего подтверждения в ассистенте.</p></div>
<div class="card"><h2>Безопасность</h2>
<p>Сессия MAX хранится на сервере в зашифрованном виде, токены доступа — только в виде хешей. Тексты сообщений не сохраняются и не пишутся в журнал.</p>
<p>Выйти и удалить данные: попросите ассистента «выйди из MAX и удали мои данные» или завершите сеанс в MAX в списке устройств.</p>
<p class="small">MAX подключается через неофициальный протокол web-версии: после обновлений MAX что-то может временно не работать, а автоматизация аккаунта может противоречить правилам сервиса.</p></div>
""" + FOOT


def login(pid, client_name, redirect_host):
    app = escape(client_name or "Приложение")
    return HEAD.format(title="Вход в MAX") + f"""
<h1>Вход в MAX</h1>
<div class="card who"><div>
  <p style="margin:0"><b>{app}</b> просит доступ к вашему аккаунту MAX: читать чаты и отправлять сообщения от вашего имени.</p>
  <span class="badge">вернёт вас на {escape(redirect_host)}</span>
  <p class="small" style="margin:10px 0 0">Если вы не добавляли этот сервер в ассистента сами — закройте страницу. Входя, вы соглашаетесь с <a href="/privacy" target="_blank">условиями хранения данных</a>.</p>
</div></div>

<div class="card center" id="s-wait"><div class="spin"></div><p>Готовим вход…</p></div>

<div class="card center hidden" id="s-qr">
  <h2>Отсканируйте QR в приложении MAX</h2>
  <div class="qr" id="qr"></div>
  <p>На телефоне откройте MAX, найдите подключение нового устройства или сканер QR и наведите камеру на код.</p>
  <p><a class="btn" id="open" href="#">Я на телефоне — открыть в MAX</a></p>
  <p class="small">Код обновляется сам, если истечёт.</p>
</div>

<div class="card hidden" id="s-pw">
  <h2>Облачный пароль</h2>
  <p>В вашем аккаунте включена двухэтапная проверка. Введите пароль MAX<span id="hint"></span>.</p>
  <form id="pwf"><input id="pw" type="password" autocomplete="current-password" required><button>Продолжить</button></form>
  <p class="err" id="pwerr"></p>
</div>

<div class="card center hidden" id="s-done"><h2 class="ok">Готово</h2><p>Возвращаемся в {app}…</p><p><a class="btn" id="back" href="#">Вернуться вручную</a></p></div>

<div class="card center hidden" id="s-err"><h2 class="err">Не получилось</h2><p id="errt"></p><p><button onclick="start()">Попробовать снова</button></p></div>

<script>
const id={pid!r};let last="",timer;
const show=s=>["wait","qr","pw","done","err"].forEach(k=>document.getElementById("s-"+k).classList.toggle("hidden",k!==s));
async function start(){{show("wait");await fetch(`/login/${{id}}/start`,{{method:"POST"}});poll()}}
async function poll(){{clearTimeout(timer);let st;
 try{{st=await (await fetch(`/login/${{id}}/state`)).json()}}catch(e){{timer=setTimeout(poll,2000);return}}
 if(st.status==="qr"){{show("qr");if(st.qr_link!==last){{last=st.qr_link;document.getElementById("qr").innerHTML=st.qr_svg;document.getElementById("open").href=st.qr_link}}}}
 else if(st.status==="password"){{show("pw");document.getElementById("hint").textContent=st.hint?` (подсказка: ${{st.hint}})`:"";document.getElementById("pwerr").textContent=st.error||""}}
 else if(st.status==="done"){{show("done");document.getElementById("back").href=st.redirect;location.href=st.redirect;return}}
 else if(st.status==="expired"){{start();return}}
 else if(st.status==="error"){{show("err");document.getElementById("errt").textContent=st.error;return}}
 else show("wait");
 timer=setTimeout(poll,1500)}}
document.getElementById("pwf").onsubmit=async e=>{{e.preventDefault();show("wait");
 await fetch(`/login/${{id}}/password`,{{method:"POST",headers:{{"content-type":"application/json"}},body:JSON.stringify({{password:document.getElementById("pw").value}})}});
 document.getElementById("pw").value="";poll()}};
start();
</script>
""" + FOOT


def privacy():
    c = escape(CONTACT)
    return HEAD.format(title="Конфиденциальность") + f"""
<h1>Конфиденциальность</h1>
<div class="card"><h2>Что хранится на сервере</h2><ol>
<li>id и имя вашего аккаунта MAX;</li>
<li>сессия MAX (токен входа и id устройства) — в зашифрованном виде;</li>
<li>токены доступа ассистента — только в виде хешей SHA-256;</li>
<li>регистрация приложения-клиента (название, адрес возврата).</li></ol></div>
<div class="card"><h2>Что не хранится</h2>
<p>Тексты сообщений, файлы, списки чатов и контактов. Они проходят через память сервера только на время запроса ассистента и последние события держатся в памяти до перезапуска или часа без обращений. В журнал пишутся лишь служебные события (вход, выход, факт отправки — id чата и сообщения, без текста).</p></div>
<div class="card"><h2>Кто видит ваши данные</h2>
<p>Ассистент (ChatGPT, Claude и т. п.), которому вы дали доступ: он получает то, что запросит у сервера, и обрабатывает это по правилам своей компании. Оператор сервера технически имеет доступ к серверу — пользуйтесь сервисом, только если доверяете ему, или поставьте свой сервер по инструкции на GitHub.</p></div>
<div class="card"><h2>Как удалить данные</h2>
<p>Попросите ассистента «выйди из MAX и удали мои данные» — сеанс в MAX завершится, а сессия и токены будут удалены с сервера сразу. Либо завершите этот сеанс в MAX в списке устройств: доступ пропадёт, а данные можно удалить по запросу оператору.</p></div>
<div class="card"><h2>Оператор</h2><p>Связь: <a href="{c}">{c}</a></p></div>
""" + FOOT


def gone():
    return HEAD.format(title="Ссылка устарела") + """
<h1>Ссылка входа устарела</h1>
<div class="card"><p>Вернитесь в ChatGPT или Claude и подключите сервер ещё раз — появится новая ссылка.</p></div>""" + FOOT
