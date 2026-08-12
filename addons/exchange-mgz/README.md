# exchange-mgz — корпоративная почта Exchange (EWS + NTLM)

Чтение и отправка через Exchange Web Services с NTLM-авторизацией — для
корпоративных ящиков (OWA), у которых нет IMAP.

Особенность: exchangelib капризна к окружению — если в venv агента падает
ConvertId-проба, запускайте через отдельный venv + sudo-хелпер (как здесь:
`yoda_mgz.sh` + sudoers). Креды — MGZ_USER/MGZ_PASSWORD из env, endpoint — в config.py
вашего окружения.

Зависимости: `pip install exchangelib`
