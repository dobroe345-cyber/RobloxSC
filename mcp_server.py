"""MCP-сервер (Streamable HTTP, без сессий) с инструментами GitHub.
Адрес: https://ВАШ-СЕРВИС.onrender.com/mcp?t=ТОКЕН   (или заголовок Authorization: Bearer ТОКЕН)
Нужны переменные окружения: PROXY_TOKEN (пароль к серверу) и GITHUB_TOKEN (токен GitHub с правами repo, workflow).
"""
import base64
import json
import os
import re

import requests
from flask import Blueprint, Response, request

bp = Blueprint("mcp", __name__)
GH_API = os.environ.get("GITHUB_API", "https://api.github.com").rstrip("/")


class GhError(Exception):
    pass


def gh(path, method="GET", body=None, raw=False):
    tok = os.environ.get("GITHUB_TOKEN", "")
    if not tok:
        raise GhError("На сервере не задан GITHUB_TOKEN (Render → Environment)")
    h = {"Authorization": "Bearer " + tok, "X-GitHub-Api-Version": "2022-11-28",
         "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json"}
    r = requests.request(method, path if path.startswith("http") else GH_API + path, headers=h, json=body, timeout=40)
    if not r.ok:
        try:
            m = r.json().get("message", "")
        except Exception:
            m = r.text[:200]
        raise GhError("GitHub %s: %s" % (r.status_code, m or r.reason))
    if raw:
        return r.text
    return r.json() if r.content else {}


def repo_of(a):
    r = str(a.get("repo", "")).strip().replace("https://github.com/", "").removesuffix(".git").strip("/")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", r):
        raise GhError("repo указывайте как владелец/имя, например dobroe345-cyber/My-Game")
    return r


def default_branch(r):
    return gh("/repos/" + r)["default_branch"]


def q(s):
    return requests.utils.quote(str(s), safe="/")


def t_repos(a):
    me = gh("/user")
    l = gh("/user/repos?per_page=100&sort=pushed")
    return "Аккаунт: %s\n" % me["login"] + "\n".join(
        "%s%s  ветка %s  обновлён %s" % (x["full_name"], " (приватный)" if x["private"] else "", x["default_branch"], (x.get("pushed_at") or "")[:10]) for x in l)


def t_tree(a):
    r = repo_of(a)
    ref = a.get("ref") or default_branch(r)
    j = gh("/repos/%s/git/trees/%s?recursive=1" % (r, q(ref)))
    pre = a.get("path") or ""
    f = [x for x in j["tree"] if x["type"] == "blob" and x["path"].startswith(pre)]
    return "Ветка %s, файлов: %d\n" % (ref, len(f)) + "\n".join("%s (%s Б)" % (x["path"], x.get("size", "?")) for x in f[:500])


def t_read(a):
    r = repo_of(a)
    ref = a.get("ref") or default_branch(r)
    txt = gh("/repos/%s/contents/%s?ref=%s" % (r, q(a["path"]), q(ref)), raw=True)
    s, e = a.get("start_line"), a.get("end_line")
    if s or e:
        ls = txt.split("\n")
        s = max(1, int(s or 1)); e = min(len(ls), int(e or len(ls)))
        return "[%s: строки %d-%d из %d]\n" % (a["path"], s, e, len(ls)) + "\n".join(ls[s - 1:e])
    return txt if len(txt) <= 60000 else txt[:60000] + "\n[...обрезано, всего %d символов; используйте start_line/end_line]" % len(txt)


def t_commit(a):
    r = repo_of(a)
    files = a.get("files") or []
    dele = a.get("delete_paths") or []
    if not files and not dele:
        raise GhError("Передайте files [{path, content}] или delete_paths")
    if len(files) > 60:
        raise GhError("Слишком много файлов за раз (максимум 60)")
    branch = a.get("branch") or default_branch(r)
    try:
        ref = gh("/repos/%s/git/ref/heads/%s" % (r, q(branch)))
    except GhError:
        if not a.get("create_branch"):
            raise GhError("Ветки %s нет. Передайте create_branch=true, чтобы создать её от основной." % branch)
        base = gh("/repos/%s/git/ref/heads/%s" % (r, q(default_branch(r))))
        ref = gh("/repos/%s/git/refs" % r, "POST", {"ref": "refs/heads/" + branch, "sha": base["object"]["sha"]})
    parent = ref["object"]["sha"]
    tree_sha = gh("/repos/%s/git/commits/%s" % (r, parent))["tree"]["sha"]
    tree = []
    for f in files:
        b = gh("/repos/%s/git/blobs" % r, "POST", {"content": str(f["content"]), "encoding": "utf-8"})
        tree.append({"path": f["path"].lstrip("/"), "mode": "100644", "type": "blob", "sha": b["sha"]})
    for p in dele:
        tree.append({"path": p.lstrip("/"), "mode": "100644", "type": "blob", "sha": None})
    nt = gh("/repos/%s/git/trees" % r, "POST", {"base_tree": tree_sha, "tree": tree})
    c = gh("/repos/%s/git/commits" % r, "POST", {"message": a.get("message") or "Update via MCP", "tree": nt["sha"], "parents": [parent]})
    gh("/repos/%s/git/refs/heads/%s" % (r, q(branch)), "PATCH", {"sha": c["sha"]})
    return "Коммит %s в %s (%s): файлов %d, удалено %d. Если есть workflow на push, сборка уже запущена: проверьте github_runs." % (c["sha"][:7], r, branch, len(files), len(dele))


def t_runs(a):
    r = repo_of(a)
    qs = "?per_page=%d" % min(int(a.get("limit") or 6), 20) + ("&branch=" + q(a["branch"]) if a.get("branch") else "")
    runs = gh("/repos/%s/actions/runs%s" % (r, qs))["workflow_runs"]
    if not runs:
        return "Запусков Actions нет (возможно, нет workflow в .github/workflows)."
    return "\n".join("run_id %s · %s · %s%s · ветка %s · %s · %s" % (
        x["id"], x["name"], x["status"], "/" + x["conclusion"] if x.get("conclusion") else "", x["head_branch"], x["head_sha"][:7], x["created_at"][:16].replace("T", " ")) for x in runs)


def t_run_log(a):
    r = repo_of(a)
    rid = a["run_id"]
    run = gh("/repos/%s/actions/runs/%s" % (r, rid))
    out = ["Запуск %s: %s%s" % (run["name"], run["status"], "/" + run["conclusion"] if run.get("conclusion") else "")]
    for jb in gh("/repos/%s/actions/runs/%s/jobs" % (r, rid))["jobs"]:
        out.append("\nЗадача «%s»: %s%s" % (jb["name"], jb["status"], "/" + jb["conclusion"] if jb.get("conclusion") else ""))
        for s in jb.get("steps", []):
            out.append("  %s %s" % ("ok" if s.get("conclusion") == "success" else ("ОШИБКА" if s.get("conclusion") == "failure" else s.get("conclusion") or s["status"]), s["name"]))
        if jb.get("conclusion") == "failure" or a.get("full"):
            try:
                for x in gh("/repos/%s/check-runs/%s/annotations" % (r, jb["id"]))[:30]:
                    out.append("  аннотация %s:%s %s: %s" % (x["path"], x["start_line"], x["annotation_level"], x["message"]))
            except GhError:
                pass
            try:
                lines = gh("/repos/%s/actions/jobs/%s/logs" % (r, jb["id"]), raw=True).split("\n")
                bad = [re.sub(r"^\S+Z ", "", l)[:300] for l in lines if re.search(r"error|fatal|failed|undefined reference|not found", l, re.I)][-60:]
                out.append("  Лог (строки с ошибками):\n" + "\n".join("    " + l for l in (bad or [re.sub(r'^\S+Z ', '', l)[:300] for l in lines[-40:]])))
            except Exception as e:
                out.append("  Лог недоступен: %s" % e)
    s = "\n".join(out)
    return s if len(s) < 20000 else s[:20000] + "\n[...обрезано]"


def t_dispatch(a):
    r = repo_of(a)
    gh("/repos/%s/actions/workflows/%s/dispatches" % (r, q(a["workflow"])), "POST", {"ref": a.get("ref") or default_branch(r)})
    return "Workflow %s запущен. Смотрите github_runs через 15–30 секунд." % a["workflow"]


def S(props, req=()):
    return {"type": "object", "properties": props, "required": list(req)}


STR = {"type": "string"}
TOOLS = [
    {"name": "github_list_repos", "description": "Список репозиториев GitHub пользователя.", "inputSchema": S({}), "fn": t_repos},
    {"name": "github_tree", "description": "Список файлов репозитория (ветка ref, папка path).", "inputSchema": S({"repo": STR, "ref": STR, "path": STR}, ["repo"]), "fn": t_tree},
    {"name": "github_read_file", "description": "Прочитать текстовый файл из репозитория; start_line/end_line для больших файлов.", "inputSchema": S({"repo": STR, "path": STR, "ref": STR, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, ["repo", "path"]), "fn": t_read},
    {"name": "github_commit_files", "description": "Закоммитить файлы одним коммитом: files = [{path, content}], delete_paths = [пути]. Запускает сборку, если настроен workflow на push.", "inputSchema": S({"repo": STR, "branch": STR, "message": STR, "files": {"type": "array", "items": S({"path": STR, "content": STR}, ["path", "content"])}, "delete_paths": {"type": "array", "items": STR}, "create_branch": {"type": "boolean"}}, ["repo", "message"]), "fn": t_commit},
    {"name": "github_runs", "description": "Последние запуски GitHub Actions: статус и результат сборки.", "inputSchema": S({"repo": STR, "branch": STR, "limit": {"type": "integer"}}, ["repo"]), "fn": t_runs},
    {"name": "github_run_log", "description": "Разбор запуска Actions: шаги, ошибки компилятора, лог упавшей задачи.", "inputSchema": S({"repo": STR, "run_id": {"type": "integer"}, "full": {"type": "boolean"}}, ["repo", "run_id"]), "fn": t_run_log},
    {"name": "github_dispatch", "description": "Запустить workflow вручную (нужен on: workflow_dispatch), workflow — имя файла, например build.yml.", "inputSchema": S({"repo": STR, "workflow": STR, "ref": STR}, ["repo", "workflow"]), "fn": t_dispatch},
]
# Подсказки клиентам (MCP annotations): какие инструменты только читают, а какие меняют репозиторий.
READ_ONLY = {"github_list_repos", "github_tree", "github_read_file", "github_runs", "github_run_log"}
for _t in TOOLS:
    _ro = _t["name"] in READ_ONLY
    _t["title"] = _t["name"].replace("github_", "GitHub: ").replace("_", " ")
    _t["annotations"] = {"title": _t["title"], "readOnlyHint": _ro, "openWorldHint": True,
                         **({} if _ro else {"destructiveHint": _t["name"] == "github_commit_files", "idempotentHint": False})}
BY_NAME = {t["name"]: t for t in TOOLS}


def authorized():
    tok = os.environ.get("PROXY_TOKEN", "")
    if not tok:
        return False  # без пароля сервер закрыт
    got = request.args.get("t") or request.headers.get("X-Proxy-Token") or ""
    ah = request.headers.get("Authorization", "")
    if ah.lower().startswith("bearer "):
        got = got or ah[7:].strip()
    return got == tok


def rpc_error(mid, code, msg):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": msg}}


def handle(msg):
    mid, method, p = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:
        return None  # уведомление: ответа не нужно
    try:
        if method == "initialize":
            res = {"protocolVersion": p.get("protocolVersion") or "2025-03-26", "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": "robloxsc-github", "version": "1.0.0"},
                   "instructions": "Инструменты GitHub: читать репозитории, коммитить файлы, смотреть сборки Actions и их логи."}
        elif method == "ping":
            res = {}
        elif method == "tools/list":
            res = {"tools": [{k: v for k, v in t.items() if k != "fn"} for t in TOOLS]}
        elif method == "tools/call":
            t = BY_NAME.get(p.get("name"))
            if not t:
                return rpc_error(mid, -32602, "Неизвестный инструмент: %s" % p.get("name"))
            try:
                txt, err = t["fn"](p.get("arguments") or {}), False
            except GhError as e:
                txt, err = str(e), True
            except KeyError as e:
                txt, err = "Не хватает параметра %s" % e, True
            except requests.RequestException as e:
                txt, err = "Сеть: %s" % str(e)[:200], True
            res = {"content": [{"type": "text", "text": txt}], "isError": err}
        else:
            return rpc_error(mid, -32601, "Метод не поддерживается: %s" % method)
        return {"jsonrpc": "2.0", "id": mid, "result": res}
    except Exception as e:  # не роняем сервер
        return rpc_error(mid, -32603, "Внутренняя ошибка: %s" % str(e)[:200])


def cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS, DELETE"
    resp.headers["Access-Control-Allow-Headers"] = request.headers.get("Access-Control-Request-Headers", "*")
    resp.headers["Access-Control-Expose-Headers"] = "Mcp-Session-Id"
    return resp


def reply(body):
    """Ответ JSON-RPC. Клиентам, которые принимают только text/event-stream, отдаём SSE; остальным обычный JSON."""
    txt = json.dumps(body, ensure_ascii=False)
    acc = request.headers.get("Accept", "").lower()
    if "text/event-stream" in acc and "application/json" not in acc:
        r = Response("event: message\ndata: " + txt + "\n\n", mimetype="text/event-stream")
        r.headers["Cache-Control"] = "no-cache"
        return cors(r)
    r = Response(txt, mimetype="application/json")
    r.headers["Cache-Control"] = "no-store"
    return cors(r)


@bp.route("/mcp/", methods=["POST", "GET", "DELETE", "OPTIONS"])
@bp.route("/mcp", methods=["POST", "GET", "DELETE", "OPTIONS"])
def mcp():
    if request.method == "OPTIONS":
        return cors(Response(status=204))
    if not authorized():
        return cors(Response('{"error":"unauthorized"}', status=401, mimetype="application/json"))
    if request.method != "POST":
        r = Response(json.dumps(rpc_error(None, -32000, "Method not allowed: use POST")), status=405, mimetype="application/json")
        r.headers["Allow"] = "POST"
        return cors(r)
    data = request.get_json(silent=True)
    if data is None:
        return reply(rpc_error(None, -32700, "Parse error"))
    msgs = data if isinstance(data, list) else [data]
    out = [x for x in (handle(m) for m in msgs if isinstance(m, dict)) if x is not None]
    if not out:
        return cors(Response("{}", status=202, mimetype="application/json"))
    return reply(out if isinstance(data, list) else out[0])
