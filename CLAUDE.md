# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Banhammer Lite - облегчённая версия системы мониторинга VPN. Анализирует логи Xray в реальном времени, сравнивает количество уникальных IP с лимитом устройств из панели Remnawave.

**Отличия от полной версии:**
- Опциональные модули: database, telegram, whois_lookup
- При отсутствии модуля функционал отключается автоматически
- Меньше зависимостей, проще деплой

## Commands

```bash
# Установка зависимостей
pip install -r requirements.txt

# Запуск сервера (TCP + HTTP API)
python server.py

# Запуск UI клиента (подключается к серверу)
python ui_client.py --token your_secret_token
python ui_client.py --server http://remote-server:8080 --token your_secret_token

# Запуск агента (на VPN-нодах)
python agent.py

# Docker
docker-compose up -d                              # сервер
docker-compose -f docker-compose.agent.yml up -d  # агент
```

## Architecture

```
Агенты (agent.py) → TCP:9999 → Сервер (server.py) → Panel API + [SQLite] + [Telegram]
                                    ↓
                              HTTP API:8080
                                    ↓
                            UI клиент (ui_client.py)
```

### Core Components

- **`core/parser.py`** - Regex парсер логов Xray. Извлекает timestamp, source_ip, protocol, destination, action, email
- **`core/tracker.py`** - `UserTracker` хранит `UserInfo` для каждого email с историей IP и временными метками. `get_recent_ips(window_seconds)` возвращает IP за последние N секунд
- **`core/tcp_server.py`** - TCP сервер принимает данные от агентов в формате `NODE_NAME|log_line`
- **`core/panel_api.py`** - Клиент API панели Remnawave. Важно: требует заголовки `X-Forwarded-For`, `X-Forwarded-Proto`, `X-Forwarded-Host`

**Опциональные модули (автоматически отключаются если отсутствуют):**
- **`core/database.py`** - SQLite для персистентности бан-листа
- **`core/telegram.py`** - Уведомления в Telegram (MarkdownV2)
- **`core/whois_lookup.py`** - ISP lookup для IP адресов

### Entry Points

- **`server.py`** - Headless сервер для Docker. Проверяет наличие опциональных модулей при старте
- **`agent.py`** - Агент на VPN-нодах. Tail лога Xray + отправка на сервер
- **`ui_client.py`** - TUI интерфейс (Textual). Вкладки: Пользователи, Пидарасы, Бан-лист, Общие IP, Детали

### Detection Logic

1. **Concurrent Window**: Считаем IP за короткое окно (default: 2 сек) - ловим реально одновременные подключения
2. **Trigger System**: Если IP > лимита в concurrent window = сработка. Сработки накапливаются за TRIGGER_PERIOD (default: 30 сек)
3. **Пидарасы**: TRIGGER_COUNT (default: 5) сработок за период = пидарас
4. **Бан-лист**: пользователь в "Пидарасах" непрерывно BANLIST_THRESHOLD_SECONDS секунд

Исключения: `hwidDeviceLimit == 0` (безлимит), email в `WHITELIST_EMAILS`

### Optional Modules Check

В `server.py` проверка опциональных модулей:
```python
try:
    from core.database import db
    HAS_DATABASE = True
except ImportError:
    HAS_DATABASE = False
    db = None
```

Аналогично для `telegram` и `whois_lookup`.

## Configuration

Все настройки через `.env` (см. `.env.example`):
- `PANEL_URL`, `PANEL_TOKEN` - API панели
- `CONCURRENT_WINDOW` - окно одновременности (default: 2)
- `TRIGGER_PERIOD` - период накопления сработок (default: 30)
- `TRIGGER_COUNT` - сработок для статуса пидарас (default: 5)
- `BANLIST_THRESHOLD_SECONDS` - порог для бан-листа (default: 300)
- `SUBNET_GROUPING` - группировка IP по /24 подсети (default: false)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` - уведомления (опционально)

## Log Format

Парсер ожидает Xray access log:
```
2026/01/09 17:02:18.183921 from 176.14.30.189:61352 accepted tcp:17.248.172.113:443 [pl_tpc >> DIRECT] email: user@example.com
```

## Panel API

Endpoint `/api/users?start=0&size=500` с пагинацией. Ответ содержит `hwidDeviceLimit`, `telegramId`, `description` для каждого пользователя.

## UI Client Features

- **Dual refresh intervals**: Таблицы обновляются каждые 2 сек, открытая карточка пользователя - каждые 0.5 сек
- **Real-time detail view**: При открытой карточке последние запросы и IP обновляются в реальном времени
- **Click navigation**: Клик по строке в любой таблице открывает карточку пользователя
- **Clear banlist**: Кнопка очистки бан-листа в соответствующей вкладке
