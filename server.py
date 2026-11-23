"""Прокси для AI Chat: принимает запрос из браузера и пересылает его на сайт с ИИ.
Браузер не может обратиться к некоторым сайтам напрямую (CORS) — серверу это можно.

Использование из приложения: https://ВАШ-СЕРВИС.onrender.com/p?t=ТОКЕН&u={url}
"""
import os
from urllib.parse import urlparse

import requests
from flask import Flask, Response, request, stream_with_context

app = Flask(__name__)
from mcp_server import bp as mcp_bp, mcp as mcp_endpoint  # MCP-сервер GitHub на /mcp (и на / для POST, если в адресе забыли /mcp)
app.register_blueprint(mcp_bp)

ALLOWED_HOSTS = {h.strip().lower() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()}
TOKEN = os.environ.get("PROXY_TOKEN", "")
ORIGINS = [o.strip().rstrip("/") for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
ALLOW_HTTP = os.environ.get("ALLOW_HTTP") == "1"  # только для локальных тестов

PASS_REQ = {"authorization", "x-api-key", "anthropic-version", "anthropic-beta", "content-type", "accept", "openai-organization"}
DROP_RESP = {"server", "date", "content-encoding", "content-length", "transfer-encoding", "connection", "keep-alive", "access-control-allow-origin"}


def cors(resp):
    origin = request.headers.get("Origin", "")
    if "*" in ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = "*"
    elif origin.rstrip("/") in ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = request.headers.get("Access-Control-Request-Headers", "*")
    resp.headers["Access-Control-Expose-Headers"] = "*"
    resp.headers["Access-Control-Max-Age"] = "86400"
    return resp


def fail(code, text):
    return cors(Response(text, status=code, mimetype="text/plain; charset=utf-8"))


@app.route("/", methods=["GET", "POST", "OPTIONS"])
def health():
    if request.method in ("POST", "OPTIONS"):
        return mcp_endpoint()
    return cors(Response("AI Chat proxy: работает. Разрешённые сайты: " + (", ".join(sorted(ALLOWED_HOSTS)) or "не заданы"),
                         mimetype="text/plain; charset=utf-8"))


@app.route("/p", methods=["GET", "POST", "OPTIONS"])
def proxy():
    if request.method == "OPTIONS":
        return cors(Response(status=204))
    if TOKEN and request.args.get("t") != TOKEN:
        return fail(401, "Неверный токен прокси (параметр t)")
    target = request.args.get("u", "")
    p = urlparse(target)
    if p.scheme not in (("https", "http") if ALLOW_HTTP else ("https",)) or not p.hostname:
        return fail(400, "Нужен параметр u с адресом https://…")
    if not ALLOWED_HOSTS:
        return fail(403, "На сервере не задан ALLOWED_HOSTS")
    if p.hostname.lower() not in ALLOWED_HOSTS:
        return fail(403, "Сайт " + p.hostname + " не разрешён (ALLOWED_HOSTS)")
    headers = {k: v for k, v in request.headers.items() if k.lower() in PASS_REQ}
    # передаём сайту настоящий адрес и браузер пользователя (как обычный прокси)
    client = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or (request.remote_addr or "")
    if client:
        headers["X-Forwarded-For"] = client
        headers["X-Real-IP"] = client
    headers["User-Agent"] = request.headers.get("User-Agent") or "Mozilla/5.0 (compatible; AIChatProxy/1.0)"
    if request.headers.get("Accept-Language"):
        headers["Accept-Language"] = request.headers["Accept-Language"]
    headers["Accept-Encoding"] = "identity"
    try:
        up = requests.request(request.method, target, data=request.get_data() if request.method == "POST" else None,
                              headers=headers, stream=True, timeout=(15, 900), allow_redirects=False)
    except requests.RequestException as e:
        return fail(502, "Сайт не ответил: " + str(e)[:300])

    def gen():
        try:
            for chunk in up.iter_content(chunk_size=None):
                if chunk:
                    yield chunk
        finally:
            up.close()

    resp = Response(stream_with_context(gen()), status=up.status_code)
    for k, v in up.headers.items():
        if k.lower() not in DROP_RESP:
            resp.headers[k] = v
    resp.headers["X-Accel-Buffering"] = "no"
    resp.headers["Cache-Control"] = "no-cache"
    return cors(resp)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), threaded=True)
