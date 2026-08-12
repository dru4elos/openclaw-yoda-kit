# science-monitor — журнальные алерты со смыслом

Демон: Gmail (алерты Scholar/журналов) → LLM-разбор каждой статьи по фиксированному
формату (название RU/EN, журнал, резюме, оценка 🔥/⭐/📌/⬜) → **одно** сообщение
в Telegram со ссылками PubMed и кнопками [🔗 открыть] [📖 глубокий разбор].

- Анализ: быстрая модель (DeepSeek flash) с резервом; футер честно пишет, кто анализировал.
- Резолв статей: точный поиск по названию в PubMed + добор свободным поиском.
- Впишите свои научные интересы в RESEARCH_INTERESTS в начале файла.

## Установка
```bash
pip install requests
# env: SCIENCE_GMAIL_USER, SCIENCE_GMAIL_PASSWORD (app-password), ADMIN_ID, DEEPSEEK_API_KEY
cp science_monitor.service /etc/systemd/system/   # поправьте пути/EnvironmentFile
systemctl enable --now science_monitor
```
