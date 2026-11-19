# robloxSC — AI Chat + прокси-сервер

- `docs/index.html` — приложение (GitHub Pages).
- `server.py` — прокси на Python (Render). Нужен, когда сайт с ИИ не пускает запросы из браузера (ошибка «Load failed» / CORS).
- `render.yaml` — настройки Render.

## 1. Приложение на GitHub Pages
Settings → Pages → Source: Deploy from a branch → Branch `main`, папка `/docs` → Save.
Адрес: `https://<ваш-логин>.github.io/robloxSC/`

## 2. Прокси на Render
1. render.com → New → Blueprint → выбрать репозиторий `robloxSC` (Render прочитает `render.yaml`).
2. После создания открыть сервис → Environment: запомнить `PROXY_TOKEN` (создаётся сам) и при необходимости изменить `ALLOWED_HOSTS` (список сайтов через запятую; сейчас `tooken.club,anymodel.org`).
3. Адрес сервиса будет вида `https://robloxsc-proxy.onrender.com`. Проверка: открыть его в браузере — должно быть «AI Chat proxy: работает».

## 3. Подключить в приложении
Настройки → Модель → «Прокси» → вписать:

    https://robloxsc-proxy.onrender.com/p?t=ВАШ_PROXY_TOKEN&u={url}

Адрес API: `https://tooken.club/v1` (адрес API, а не кабинета). Затем «Проверить подключение».

Бесплатный Render засыпает через 15 минут простоя: первый запрос после паузы идёт ~30–60 секунд.
