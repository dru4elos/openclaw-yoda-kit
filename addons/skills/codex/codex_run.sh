#!/bin/bash
# Codex CLI как программист и аналитик на одну задачу в проекте Йоды.
# Только внутри ~/projects/<репо>; песочница workspace-write — пишет только в этот репозиторий и /tmp,
# сеть есть (пакеты, данные, API). Модели excash через страж-прокси: ~/.codex/config.toml,
# профили — ~/.codex/{sol,astra,luna,deep}.config.toml.
#   codex_run.sh <репо> "<задача>" [--profile sol|astra|luna|deep] [--timeout СЕК]
#   codex_run.sh --check        проверить, что песочница на сервере работает
set -uo pipefail
source ~/.nvm/nvm.sh >/dev/null 2>&1

if [ "${1:-}" = "--check" ]; then
  T=$(mktemp -d); cd "$T"; P="$HOME/.codex_probe"; echo probe > "$P"
  WS='sandbox_mode="workspace-write"'
  codex sandbox -c "$WS" -- sh -c 'echo ok > inside' 2> "$T/err1"
  codex sandbox -c "$WS" -- sh -c "echo x >> '$P'" 2>/dev/null
  net=$(codex sandbox -c "$WS" -c sandbox_workspace_write.network_access=true -- \
        curl -s -m 10 -o /dev/null -w '%{http_code}' https://pypi.org/simple/ 2>/dev/null)
  if grep -q "bwrap" "$T/err1"; then
    echo "✗ песочница на сервере НЕ включена: $(head -1 "$T/err1")"
    echo "  скажи доктору: нужна команда AppArmor для bubblewrap — одна, на сервере"
    rm -rf "$T" "$P"; exit 1
  fi
  [ -f inside ] && echo "✓ пишет внутри рабочей папки" || echo "✗ не пишет даже внутри рабочей папки"
  [ "$(cat "$P")" = "probe" ] && echo "✓ за пределы рабочей папки не пишет" || echo "✗ ПИШЕТ за пределы рабочей папки"
  [ "$net" = "200" ] && echo "✓ сеть есть" || echo "✗ сети нет (код $net)"
  rm -rf "$T" "$P"; exit 0
fi

usage() { echo "codex_run.sh <репо в ~/projects> \"<задача>\" [--profile sol|astra|luna|deep] [--timeout СЕК]  |  codex_run.sh --check"; exit 2; }
[ $# -ge 2 ] || usage
REPO=$1; TASK=$2; shift 2
PROFILE=sol; TO=3600
while [ $# -gt 0 ]; do case $1 in --profile) PROFILE=$2; shift 2;; --timeout) TO=$2; shift 2;; *) usage;; esac; done
[[ "$REPO" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "имя репозитория: латиница, цифры, точка, дефис"; exit 2; }
case $PROFILE in sol|astra|luna|deep) ;; *) usage;; esac
DIR=$HOME/projects/$REPO
[ -d "$DIR/.git" ] || { echo "нет репозитория $DIR — сначала: projects.py clone $REPO"; exit 2; }
EXCASH_API_KEY=$(grep '^EXCASH_API_KEY=' "$HOME/.openclaw/.env" | cut -d= -f2- | tr -d "\"'")
export EXCASH_API_KEY
# Пакеты для статистики и анализа — в отдельном окружении, оно первым в PATH
[ -d "$HOME/rnd-venv" ] && export PATH="$HOME/rnd-venv/bin:$PATH" VIRTUAL_ENV="$HOME/rnd-venv"
LOGS=$HOME/projects/.codex-logs; mkdir -p "$LOGS"
LOG=$LOGS/$REPO-$(date +%Y%m%d-%H%M%S)
cd "$DIR"
echo "▶ Codex · $REPO · профиль $PROFILE · лимит $TO с · журнал $LOG.log"
timeout "$TO" codex exec -p "$PROFILE" --skip-git-repo-check -o "$LOG.last" "$TASK" < /dev/null > "$LOG.log" 2>&1
code=$?
if grep -q "bwrap: setting up uid map" "$LOG.log"; then
  echo "⛔ песочница на сервере не включена — команды агента не выполнялись. Скажи доктору: нужна команда AppArmor для bubblewrap."
fi
echo "---- итог агента"; if [ -s "$LOG.last" ]; then tail -c 6000 "$LOG.last"; else tail -c 3000 "$LOG.log"; fi
echo; echo "---- изменения в репозитории"; git status --short | head -40; git diff --stat | tail -3
[ $code -eq 124 ] && echo "⏱ вышло время ($TO с), работа прервана"
if [ $code -ne 0 ] && [ $code -ne 124 ]; then echo "⚠️ codex завершился с кодом $code, конец журнала:"; tail -15 "$LOG.log"; fi
exit $code
