# Banhammer Lite

Система мониторинга VPN подключений с автоматическим обнаружением тех, кто делится подпиской.

Показывает кто сейчас подключен к VPN, с скольких устройств, и автоматически определяет нарушителей.

**Функционал:**
- Мониторинг подключений в реальном времени
- Автоматическое обнаружение "пидарасов" (превышение лимита устройств)
- Trigger-based детекция (накопление сработок за период)
- Бан-лист с историей (опционально, требует database.py)
- Уведомления в Telegram (опционально, требует telegram.py)
- ISP lookup для IP адресов (опционально, требует whois_lookup.py)
- TUI клиент для удобного просмотра

---

## Установка сервера (где стоит Remnawave)

### Шаг 1: Скачай файлы

```bash
# Перейди в папку где хочешь установить
cd /opt

# Скачай репозиторий (или скопируй файлы вручную)
git clone https://github.com/your-repo/banhammer-lite.git
cd banhammer-lite
```

Или создай папку и скопируй файлы вручную:
```bash
mkdir -p /opt/banhammer-lite
cd /opt/banhammer-lite
# скопируй сюда все файлы из архива
```

### Шаг 2: Узнай имя Docker сети Remnawave

```bash
docker network ls
```

Ты увидишь что-то типа:
```
NETWORK ID     NAME                  DRIVER
abc123def456   remnawave-network     bridge
789ghi012jkl   bridge                bridge
```

**Запомни имя сети где Remnawave** (например `remnawave-network` или `remnawave_default`)

Если не уверен, проверь так:
```bash
docker inspect remnawave | grep -i network
```

### Шаг 3: Получи JWT токен из Remnawave

1. Открой панель Remnawave в браузере
2. Залогинься как админ
3. Настройки Remnawave
4. API-токены
5. Создай новый или скопируй существующий


### Шаг 4: Создай файл настроек

```bash
cp .env.example .env
nano .env
```

Заполни эти строки (остальное не трогай):

```env
# Имя сети из Шага 2
PANEL_NETWORK=remnawave-network

# JWT токен из Шага 3
PANEL_TOKEN=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xxxxx

# Придумай любой пароль для API (запомни его!)
API_TOKEN=mySuperSecretPassword123
```

Сохрани: `Ctrl+O`, `Enter`, `Ctrl+X`

### Шаг 5: Запусти сервер

```bash
docker-compose up -d
```

### Шаг 6: Проверь что работает

```bash
# Посмотри логи
docker-compose logs -f
```

Ты должен увидеть:
```
banhammer-lite  | 2024-01-15 12:00:00 - INFO - Настройки: CONCURRENT_WINDOW=2s, TRIGGER_PERIOD=30s, TRIGGER_COUNT=5...
banhammer-lite  | 2024-01-15 12:00:00 - INFO - Запуск Banhammer сервера на 0.0.0.0:9999
banhammer-lite  | 2024-01-15 12:00:00 - INFO - HTTP API на 0.0.0.0:8080
banhammer-lite  | 2024-01-15 12:00:01 - INFO - Загружено 150 пользователей из панели
banhammer-lite  | [*] TCP сервер запущен на 0.0.0.0:9999
```

Если видишь ошибку - читай раздел "Проблемы" внизу.

Нажми `Ctrl+C` чтобы выйти из логов (сервер продолжит работать).

### Шаг 7: Проверь API

```bash
# Замени mySuperSecretPassword123 на свой API_TOKEN из .env
curl -H "Authorization: Bearer mySuperSecretPassword123" http://localhost:8080/api/stats
```

Должен вернуться JSON:
```json
{"total_users": 0, "total_requests": 0, "violators_count": 0, "connected_nodes": [], "panel_loaded": true}
```

**Сервер готов!** Теперь нужно установить агенты на VPN-ноды.

---

## Установка агента (на каждой VPN-ноде)

Агент читает логи Xray и отправляет их на сервер.

### Шаг 1: Скопируй файлы на ноду

Тебе нужны только 3 файла:
- `agent.py`
- `Dockerfile.agent`
- `docker-compose.agent.yml`

```bash
# На ноде создай папку
mkdir -p /opt/banhammer-agent
cd /opt/banhammer-agent

# Скопируй файлы (через scp, sftp или вручную)
```

### Шаг 2: Создай файл настроек

```bash
nano .env
```

Вставь это (замени значения на свои):

```env
# Уникальное имя этой ноды (любое, для отображения)
NODE_NAME=Germany-1

# IP адрес или домен сервера где установил Banhammer Lite
BANHAMMER_HOST=123.45.67.89

# Порт (не меняй если не менял на сервере)
BANHAMMER_PORT=9999

# Путь к папке с логами Xray на этой ноде
LOG_DIR=/var/lib/remnanode/xray

# Путь к файлу лога внутри этой папки
LOG_FILE=/var/lib/remnanode/xray/access.log
```

Сохрани: `Ctrl+O`, `Enter`, `Ctrl+X`

### Шаг 3: Проверь что лог-файл существует

```bash
# Проверь путь к логам (может отличаться!)
ls -la /var/lib/remnanode/xray/

# Или найди где лежит access.log
find / -name "access.log" 2>/dev/null | grep -i xray
```

Если путь другой - исправь `LOG_DIR` и `LOG_FILE` в `.env`

### Шаг 4: Открой порт на сервере

На сервере (где Banhammer Lite) открой порт 9999:

```bash
# Если используешь ufw
ufw allow 9999/tcp

# Если используешь iptables
iptables -A INPUT -p tcp --dport 9999 -j ACCEPT
```

### Шаг 5: Запусти агента

```bash
cd /opt/banhammer-agent
docker-compose -f docker-compose.agent.yml up -d
```

### Шаг 6: Проверь что агент подключился

```bash
# Логи агента
docker-compose -f docker-compose.agent.yml logs -f
```

Должен увидеть:
```
banhammer-agent | 2024-01-15 12:05:00 - INFO - Агент 'Germany-1' -> 123.45.67.89:9999
banhammer-agent | 2024-01-15 12:05:00 - INFO - Подключение к 123.45.67.89:9999...
banhammer-agent | 2024-01-15 12:05:01 - INFO - Подключено!
banhammer-agent | 2024-01-15 12:05:01 - INFO - Чтение /var/lib/remnanode/xray/access.log...
```

**Повтори шаги для каждой ноды** (с разными NODE_NAME).

---

## Запуск UI клиента

UI клиент - терминальный интерфейс для просмотра данных.

```bash
# Установи зависимости (если запускаешь без Docker)
pip install textual aiohttp python-dotenv

# Запусти
python ui_client.py --server http://ip-сервера:8080 --token твой_API_TOKEN
```

**Вкладки в UI:**
- **Пользователи** - все активные пользователи с количеством IP
- **Пидарасы** - нарушители (IP > лимита)
- **Бан-лист** - пользователи попавшие в бан (если есть модуль database)
- **Общие IP** - IP адреса используемые несколькими пользователями
- **Детали** - подробная карточка выбранного пользователя

**Горячие клавиши:**
- `1-4` - переключение вкладок
- `/` - поиск по email
- `r` - обновить данные
- `q` - выход

---

## Логика детекции

### Как работает обнаружение

1. **Concurrent Window (2 сек)** - считаем IP за короткое окно. Ловим реально одновременные подключения.

2. **Trigger System** - если IP > лимита в concurrent window = сработка. Сработки накапливаются за TRIGGER_PERIOD (30 сек).

3. **Пидарас** - если за 30 секунд накопилось 5+ сработок, пользователь становится "пидарасом".

4. **Бан-лист** - если пидарас остаётся в статусе 5+ минут непрерывно, он попадает в бан-лист.

### Защита от ложных срабатываний

- Короткое окно (2 сек) отсеивает случайные переподключения WiFi→LTE
- Накопление сработок исключает единичные совпадения
- При выходе из превышения лимита счётчик сработок очищается

---

## Проверка что всё работает

На сервере:

```bash
# Проверь подключенные ноды
curl -H "Authorization: Bearer твой_API_TOKEN" http://localhost:8080/api/nodes
```

Ответ: `["Germany-1", "Netherlands-2"]`

```bash
# Проверь текущих пидарасов
curl -H "Authorization: Bearer твой_API_TOKEN" http://localhost:8080/api/violators
```

---

## Частые проблемы

### Ошибка: "Ошибка загрузки из панели"

**Причина:** Сервер не может подключиться к Remnawave

**Решение:**
1. Проверь `PANEL_NETWORK` в `.env` - должно совпадать с сетью Remnawave
2. Проверь `PANEL_TOKEN` - токен должен быть валидным
3. Проверь что контейнер remnawave запущен: `docker ps | grep remna`

```bash
# Перезапусти после исправления
docker-compose down
docker-compose up -d
docker-compose logs -f
```

### Ошибка: "Connection refused" на агенте

**Причина:** Агент не может подключиться к серверу

**Решение:**
1. Проверь что сервер запущен: `docker ps | grep banhammer`
2. Проверь IP адрес в `BANHAMMER_HOST`
3. Проверь что порт 9999 открыт на сервере
4. Проверь firewall на сервере

```bash
# На сервере проверь что порт слушается
netstat -tlnp | grep 9999
```

### Ошибка: "Файл не найден" на агенте

**Причина:** Неправильный путь к логам

**Решение:**
```bash
# Найди где лежит access.log
find / -name "access.log" 2>/dev/null

# Исправь LOG_DIR и LOG_FILE в .env
```

### Нет пользователей в списке

**Причина:** Никто не подключен к VPN или агент не отправляет данные

**Решение:**
1. Подключись к VPN сам
2. Проверь логи агента - должны быть строки с "email:"
3. Проверь что access.log обновляется: `tail -f /путь/к/access.log`

---

## Команды управления

### Сервер

```bash
cd /opt/banhammer-lite

# Запуск
docker-compose up -d

# Остановка
docker-compose down

# Перезапуск
docker-compose restart

# Логи
docker-compose logs -f

# Пересборка после изменений
docker-compose up -d --build
```

### Агент

```bash
cd /opt/banhammer-agent

# Запуск
docker-compose -f docker-compose.agent.yml up -d

# Остановка
docker-compose -f docker-compose.agent.yml down

# Логи
docker-compose -f docker-compose.agent.yml logs -f
```

---

## API

| Метод | URL | Описание |
|-------|-----|----------|
| GET | /api/stats | Общая статистика |
| GET | /api/users | Список пользователей с количеством IP |
| GET | /api/violators | Текущие нарушители |
| GET | /api/banlist | Бан-лист (требует database) |
| POST | /api/banlist/clear | Очистить бан-лист |
| GET | /api/user/{email} | Детали пользователя |
| GET | /api/nodes | Подключенные ноды |
| GET | /api/shared_ips | IP с несколькими пользователями |

Все запросы требуют заголовок:
```
Authorization: Bearer ВАШ_API_TOKEN
```

---

## Настройки детекции

В `.env` можно настроить чувствительность:

```env
# Окно одновременности (секунды)
# Меньше = строже, но может быть больше ложных срабатываний
CONCURRENT_WINDOW=2

# Период накопления сработок
TRIGGER_PERIOD=30

# Количество сработок для статуса "пидарас"
# Больше = мягче, меньше ложных срабатываний
TRIGGER_COUNT=5

# Время в пидарасах для бан-листа (секунды)
BANLIST_THRESHOLD_SECONDS=300

# Группировка IP по подсетям /24
# true = 79.137.136.1 и 79.137.136.2 считаются как 1 источник
SUBNET_GROUPING=false
```
