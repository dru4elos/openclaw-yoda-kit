#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""projects — проекты Йоды на GitHub: заготовка репозитория, статус, передача работы Claude.

  projects.py whoami                          кто я на GitHub, какая организация, есть ли доступ
  projects.py list                            репозитории организации, свежие сверху
  projects.py new ИМЯ --desc "…" [--public]   создать репозиторий в организации с заготовкой и склонировать
  projects.py clone РЕПО                      склонировать или обновить в ~/projects
  projects.py status РЕПО                     задачи, PR, последние коммиты, STATUS.md — коротко, для телефона
  projects.py handoff РЕПО ISSUE --to claude "что сделать"    передать задачу Claude (@claude в issue)
  projects.py handoff РЕПО ISSUE --to owner "что нужно"       вопрос владельцу (метка ждёт-владельца)
  projects.py enable-claude РЕПО              включить в репозитории ответы Claude на @claude

Доступ: токен живёт в gh (gh auth status), организация — GH_ORG в ~/.openclaw/.env.
Подключает владелец одной командой на своём компьютере: ssh -t vps yoda-github-login
"""
import argparse
import base64
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys

HOME = os.path.expanduser("~")
PROJ = os.path.join(HOME, "projects")
ENVF = os.path.join(HOME, ".openclaw", ".env")
TPL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "template")
LABELS = (("йода", "5319E7", "задачу ведёт Йода"), ("claude", "D97706", "задачу ведёт Claude"),
          ("ждёт-владельца", "B60205", "нужен ответ или действие владельца"))
LOGIN_HINT = "Скажи владельцу: на компьютере выполнить ssh -t vps yoda-github-login"


def env(name):
    v = os.environ.get(name)
    if v:
        return v
    try:
        for line in open(ENVF, encoding="utf-8"):
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def gh(*args, check=True, cwd=None):
    if not shutil.which("gh"):
        sys.exit("На сервере нет gh (GitHub CLI).")
    r = subprocess.run(["gh", *args], capture_output=True, text=True, cwd=cwd)
    if check and r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip()
        if re.search(r"auth login|not logged|HTTP 401|Bad credentials", msg, re.I):
            sys.exit("Нет доступа к GitHub: токен не подключён или истёк. " + LOGIN_HINT)
        sys.exit(f"gh {' '.join(args[:3])}: {msg[:500]}")
    return r


def git(*args, cwd=None, check=True):
    r = subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd)
    if check and r.returncode != 0:
        sys.exit(f"git {' '.join(args[:2])}: {(r.stderr or r.stdout).strip()[:600]}")
    return r


def org():
    o = env("GH_ORG")
    if not o:
        sys.exit("Организация не подключена (нет GH_ORG). " + LOGIN_HINT)
    return o


def full(repo):
    return repo if "/" in repo else f"{org()}/{repo}"


def cmd_whoami(_a):
    me = gh("api", "user", "--jq", ".login").stdout.strip()
    o = env("GH_ORG") or "не задана"
    seen = gh("api", f"orgs/{o}", "--jq", ".login", check=False).returncode == 0 if o != "не задана" else False
    print(f"GitHub: {me}; организация: {o} ({'видна токену' if seen else 'НЕ видна токену'}); проекты: {PROJ}")


def cmd_list(_a):
    rows = json.loads(gh("repo", "list", org(), "--limit", "40", "--json",
                         "name,description,updatedAt,visibility,url").stdout or "[]")
    rows.sort(key=lambda r: r["updatedAt"], reverse=True)
    if not rows:
        print(f"В организации {org()} пока нет репозиториев.")
    for r in rows:
        print(f"• {r['name']} ({r['visibility'].lower()}, {r['updatedAt'][:10]}) — {(r.get('description') or '')[:80]}\n  {r['url']}")


def render(name, desc, is_public):
    today = dt.date.today().strftime("%d.%m.%Y")
    vals = {"{{name}}": name, "{{desc}}": desc or "описание уточняется", "{{date}}": today, "{{org}}": org(),
            "{{visibility}}": "публичный" if is_public else "приватный"}
    out = {}
    for root, _dirs, files in os.walk(TPL):
        for f in files:
            src = os.path.join(root, f)
            rel = os.path.relpath(src, TPL)
            if os.path.basename(rel).startswith("._") or rel.startswith(".github"):          # ответы на @claude включаются отдельно: без секрета workflow падает
                continue
            text = open(src, encoding="utf-8").read()
            for k, v in vals.items():
                text = text.replace(k, v)
            out[rel] = text
    return out


def cmd_new(a):
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", a.name):
        sys.exit("Имя репозитория: латиница, цифры, точка, дефис, подчёркивание (например kosmo-orbital-risk).")
    repo = f"{org()}/{a.name}"
    path = os.path.join(PROJ, a.name)
    if os.path.exists(path):
        sys.exit(f"Папка {path} уже есть — используй clone/status.")
    gh("repo", "create", repo, "--public" if a.public else "--private", "--description", a.desc or a.name)
    os.makedirs(PROJ, exist_ok=True)
    git("clone", f"https://github.com/{repo}.git", path)
    for rel, text in render(a.name, a.desc, a.public).items():
        dst = os.path.join(path, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        open(dst, "w", encoding="utf-8").write(text)
    git("checkout", "-B", "main", cwd=path)
    git("add", "-A", cwd=path)
    git("commit", "-m", "Заготовка проекта: правила для агентов, STATUS.md, раскрытие ИИ в README", cwd=path)
    git("push", "-u", "origin", "main", cwd=path)
    for name, color, descr in LABELS:
        gh("label", "create", name, "--color", color, "--description", descr, "--force", "-R", repo, check=False)
    print(f"Готово: https://github.com/{repo}\nЛокально: {path}\n"
          f"Дальше: задачи — issues с метками «йода»/«claude»; Claude в облаке открывает этот же репозиторий.")


def cmd_clone(a):
    repo = full(a.repo)
    path = os.path.join(PROJ, repo.split("/")[1])
    if os.path.isdir(os.path.join(path, ".git")):
        r = git("pull", "--ff-only", cwd=path, check=False)
        print(f"Обновил {path}: {(r.stdout or r.stderr).strip()[-200:]}")
    else:
        os.makedirs(PROJ, exist_ok=True)
        git("clone", f"https://github.com/{repo}.git", path)
        print(f"Склонировал в {path}")


def cmd_status(a):
    repo = full(a.repo)
    issues = json.loads(gh("issue", "list", "-R", repo, "--state", "open", "--limit", "20", "--json",
                           "number,title,labels,updatedAt").stdout or "[]")
    prs = json.loads(gh("pr", "list", "-R", repo, "--state", "open", "--limit", "10", "--json",
                        "number,title,headRefName,author,isDraft,updatedAt").stdout or "[]")
    commits = json.loads(gh("api", f"repos/{repo}/commits?per_page=5").stdout or "[]")
    print(f"📦 {repo}")
    print(f"Задачи открыты: {len(issues)}")
    for i in issues[:12]:
        labs = ",".join(l["name"] for l in i.get("labels") or [])
        print(f"  #{i['number']} {i['title'][:70]}" + (f" [{labs}]" if labs else ""))
    print(f"PR открыты: {len(prs)}")
    for p in prs:
        print(f"  #{p['number']} {p['title'][:60]} ← {p['headRefName']} ({p['author']['login']}{', черновик' if p['isDraft'] else ''})")
    print("Последние коммиты:")
    for c in commits:
        msg = c["commit"]["message"].splitlines()[0][:70]
        print(f"  {c['commit']['author']['date'][:16].replace('T', ' ')} {c['commit']['author']['name']}: {msg}")
    st = gh("api", f"repos/{repo}/contents/STATUS.md", "--jq", ".content", check=False)
    if st.returncode == 0 and st.stdout.strip():
        text = base64.b64decode(st.stdout.strip()).decode("utf-8", "replace")
        print("STATUS.md (начало):")
        for line in text.splitlines()[:25]:
            if line.strip():
                print("  " + line[:110])


def has_claude_workflow(repo):
    return gh("api", f"repos/{repo}/contents/.github/workflows/claude.yml", check=False).returncode == 0


def cmd_handoff(a):
    repo = full(a.repo)
    if a.to == "claude":
        body = (f"@claude {a.text}\n\nПеред началом прочитай AGENTS.md и STATUS.md. Работай в отдельной ветке, открой PR, "
                f"в конце обнови STATUS.md: что сделано, что дальше, как проверить.\n\n— Йода")
        gh("issue", "comment", str(a.issue), "-R", repo, "--body", body)
        gh("issue", "edit", str(a.issue), "-R", repo, "--add-label", "claude", "--remove-label", "йода", check=False)
        if has_claude_workflow(repo):
            print(f"Передал Claude: issue #{a.issue} в {repo}. Claude ответит в issue и откроет PR.")
        else:
            print(f"Записал задачу для Claude в issue #{a.issue}, но в {repo} ответы на @claude не включены — "
                  f"сам Claude её не возьмёт. Варианты: projects.py enable-claude {a.repo} (нужен секрет) или "
                  f"владелец открывает репозиторий в приложении Claude → Code и пишет «возьми issue #{a.issue}».")
    else:
        gh("issue", "comment", str(a.issue), "-R", repo, "--body", f"Нужно от владельца: {a.text}\n\n— Йода")
        gh("issue", "edit", str(a.issue), "-R", repo, "--add-label", "ждёт-владельца", check=False)
        print(f"Отметил issue #{a.issue} как «ждёт-владельца». Сообщи владельцу в Telegram, что именно нужно.")


def cmd_enable_claude(a):
    repo = full(a.repo)
    path = os.path.join(PROJ, repo.split("/")[1])
    if not os.path.isdir(os.path.join(path, ".git")):
        git("clone", f"https://github.com/{repo}.git", path)
    git("pull", "--ff-only", cwd=path, check=False)
    wf = os.path.join(path, ".github", "workflows", "claude.yml")
    os.makedirs(os.path.dirname(wf), exist_ok=True)
    shutil.copy(os.path.join(TPL, ".github", "workflows", "claude.yml"), wf)
    git("add", wf, cwd=path)
    if git("diff", "--cached", "--quiet", cwd=path, check=False).returncode != 0:
        git("commit", "-m", "Ответы Claude на @claude в issues и PR", cwd=path)
        git("push", cwd=path)
    print(f"В {repo} добавлен .github/workflows/claude.yml.\nЧтобы заработало, владельцу нужен секрет один раз: "
          f"на Маке `claude setup-token`, затем секрет CLAUDE_CODE_OAUTH_TOKEN в настройках репозитория или организации "
          f"(Settings → Secrets and variables → Actions). И приложение Claude для GitHub на организации.")


def main():
    ap = argparse.ArgumentParser(description="Проекты Йоды на GitHub")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("whoami").set_defaults(func=cmd_whoami)
    sub.add_parser("list").set_defaults(func=cmd_list)
    n = sub.add_parser("new"); n.add_argument("name"); n.add_argument("--desc", default="")
    n.add_argument("--public", action="store_true"); n.set_defaults(func=cmd_new)
    c = sub.add_parser("clone"); c.add_argument("repo"); c.set_defaults(func=cmd_clone)
    s = sub.add_parser("status"); s.add_argument("repo"); s.set_defaults(func=cmd_status)
    h = sub.add_parser("handoff"); h.add_argument("repo"); h.add_argument("issue", type=int)
    h.add_argument("--to", choices=["claude", "owner"], required=True); h.add_argument("text")
    h.set_defaults(func=cmd_handoff)
    e = sub.add_parser("enable-claude"); e.add_argument("repo"); e.set_defaults(func=cmd_enable_claude)
    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
