#!/bin/bash
# DeepSeek Harness как программист на одну задачу в проекте Йоды.
# Только headless: веб-интерфейс не запускается (в нём была CVE-2026-82533).
# Только внутри ~/projects/<репо>; песочница workspace-write — пишет только в этот репозиторий.
set -uo pipefail
usage() { echo "dsh_run.sh <репо в ~/projects> \"<задача>\" [--model flash|luna] [--timeout СЕК]"; exit 2; }
[ $# -ge 2 ] || usage
REPO=$1; TASK=$2; shift 2
MODEL=flash; TO=1800
while [ $# -gt 0 ]; do case $1 in --model) MODEL=$2; shift 2;; --timeout) TO=$2; shift 2;; *) usage;; esac; done
[[ "$REPO" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "имя репозитория: латиница, цифры, точка, дефис"; exit 2; }
DIR=$HOME/projects/$REPO
[ -d "$DIR/.git" ] || { echo "нет репозитория $DIR — сначала: projects.py clone $REPO"; exit 2; }
case $MODEL in flash) export DSH_HOME=$HOME/.dsh;; luna) export DSH_HOME=$HOME/.dsh-luna;; *) usage;; esac
source ~/.nvm/nvm.sh >/dev/null 2>&1
export DSH_PERMISSION_MODE=workspace-write
EXCASH_API_KEY=$(grep '^EXCASH_API_KEY=' "$HOME/.openclaw/.env" | cut -d= -f2- | tr -d "\"'")
export EXCASH_API_KEY
LOGS=$HOME/projects/.dsh-logs; mkdir -p "$LOGS"
LOG=$LOGS/$REPO-$(date +%Y%m%d-%H%M%S)
cd "$DIR"
echo "▶ DeepSeek Harness · $REPO · модель $MODEL · лимит $TO с · журнал $LOG.err"
timeout "$TO" dsh --profile headless "$TASK" > "$LOG.out" 2> "$LOG.err"
code=$?
echo "---- ответ агента"; tail -c 6000 "$LOG.out"
echo; echo "---- изменения в репозитории"; git status --short | head -40; git diff --stat | tail -3
[ $code -eq 124 ] && echo "⏱ вышло время ($TO с), работа прервана"
if [ $code -ne 0 ] && [ $code -ne 124 ]; then echo "⚠️ dsh завершился с кодом $code, конец журнала:"; tail -15 "$LOG.err"; fi
exit $code
