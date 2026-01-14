#!/usr/bin/env python3
"""
Headless сервер Banhammer (без UI).
Для запуска в Docker/фоновом режиме.
Включает HTTP API для подключения UI клиента.
"""

import os
import sys
import json
import signal
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Set
from dotenv import load_dotenv
from aiohttp import web

load_dotenv()

# Настройка логирования
log_level = os.getenv('LOG_LEVEL', 'INFO')
logging.basicConfig(
    level=getattr(logging, log_level),
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger('banhammer')

from core.tracker import UserTracker
from core.tcp_server import TCPLogServer
from core.panel_api import panel_api

# Опциональные модули (для lite версии)
try:
    from core.database import db
    HAS_DATABASE = True
except ImportError:
    HAS_DATABASE = False
    db = None
    logger.warning("Модуль database не найден - бан-лист отключен")

try:
    from core.telegram import telegram
    HAS_TELEGRAM = True
except ImportError:
    HAS_TELEGRAM = False
    telegram = None
    logger.warning("Модуль telegram не найден - уведомления отключены")

try:
    from core.whois_lookup import whois_lookup
    HAS_WHOIS = True
except ImportError:
    HAS_WHOIS = False
    whois_lookup = None
    logger.warning("Модуль whois_lookup не найден - ISP lookup отключен")


class BanhammerServer:
    """Headless сервер для обработки логов."""

    def __init__(self):
        # Настройки из ENV
        self.tcp_host = os.getenv('TCP_HOST', '0.0.0.0')
        self.tcp_port = int(os.getenv('TCP_PORT', '9999'))
        self.api_host = os.getenv('API_HOST', '0.0.0.0')
        self.api_port = int(os.getenv('API_PORT', '8080'))
        self.api_token = os.getenv('API_TOKEN', '')

        # Detection settings
        self._concurrent_window = int(os.getenv('CONCURRENT_WINDOW', '2'))  # Окно одновременности (секунды)
        self._trigger_period = int(os.getenv('TRIGGER_PERIOD', '30'))  # Период накопления сработок
        self._trigger_count = int(os.getenv('TRIGGER_COUNT', '5'))  # Сработок для статуса "пидарас"
        self._banlist_threshold = int(os.getenv('BANLIST_THRESHOLD_SECONDS', '300'))  # Время для бан-листа
        self._banlist_ttl = int(os.getenv('BANLIST_TTL_SECONDS', '3600'))
        self._panel_reload_interval = int(os.getenv('PANEL_RELOAD_INTERVAL', '300'))

        # Группировка IP по /24 подсети
        self._subnet_grouping = os.getenv('SUBNET_GROUPING', 'false').lower() in ('true', '1', 'yes')
        # Время хранения данных в памяти
        self._data_retention = int(os.getenv('DATA_RETENTION_SECONDS', '300'))

        # Белый список
        whitelist_str = os.getenv('WHITELIST_EMAILS', '')
        self._whitelist_emails: Set[str] = set(
            e.strip() for e in whitelist_str.split(',') if e.strip()
        )

        # Компоненты
        self.tracker = UserTracker(window_seconds=self._concurrent_window, max_age_seconds=self._data_retention)
        self.tcp_server = TCPLogServer(host=self.tcp_host, port=self.tcp_port)

        # Состояние
        self._connected_nodes: Set[str] = set()
        self._user_triggers: dict = {}  # email -> list[datetime] сработки за период
        self._violator_first_seen: dict = {}  # email -> datetime когда стал пидарасом
        self._violator_ips: dict = {}  # email -> set() все IP за время нарушения
        self._confirmed_violators: set = set()  # email юзеров в бан-листе
        self._user_limits: dict = {}  # email -> limit
        self._current_violators: set = set()  # текущие пидарасы
        self._last_notification: dict = {}  # email -> datetime
        self._notification_interval = 300
        self._running = False
        self._api_runner = None

        logger.info(f"Настройки: CONCURRENT_WINDOW={self._concurrent_window}s, TRIGGER_PERIOD={self._trigger_period}s, "
                    f"TRIGGER_COUNT={self._trigger_count}, BANLIST_THRESHOLD={self._banlist_threshold}s, "
                    f"DATA_RETENTION={self._data_retention}s, SUBNET_GROUPING={self._subnet_grouping}")
        if self._whitelist_emails:
            logger.info(f"Белый список: {self._whitelist_emails}")
        if not self.api_token:
            logger.warning("API_TOKEN не установлен! API будет без авторизации.")

    async def start(self):
        """Запуск сервера."""
        self._running = True
        logger.info(f"Запуск Banhammer сервера на {self.tcp_host}:{self.tcp_port}")
        logger.info(f"HTTP API на {self.api_host}:{self.api_port}")

        # Загружаем пользователей из панели
        await self._load_panel_users()

        # Настраиваем callbacks
        self.tcp_server.on_entry(self._on_entry)
        self.tcp_server.on_connect(self._on_connect)
        self.tcp_server.on_disconnect(self._on_disconnect)

        # Запускаем фоновые задачи
        asyncio.create_task(self._periodic_tasks())

        # Запускаем HTTP API
        asyncio.create_task(self._start_api())

        # Запускаем TCP сервер
        await self.tcp_server.start()

    @web.middleware
    async def _auth_middleware(self, request, handler):
        """Middleware для проверки токена."""
        if self.api_token:
            # Проверяем токен в заголовке Authorization: Bearer <token>
            auth_header = request.headers.get('Authorization', '')
            if auth_header.startswith('Bearer '):
                token = auth_header[7:]
            else:
                token = request.query.get('token', '')

            if token != self.api_token:
                return web.json_response({'error': 'Unauthorized'}, status=401)

        return await handler(request)

    async def _start_api(self):
        """Запуск HTTP API."""
        app = web.Application(middlewares=[self._auth_middleware])
        app.router.add_get('/api/stats', self._api_stats)
        app.router.add_get('/api/users', self._api_users)
        app.router.add_get('/api/violators', self._api_violators)
        app.router.add_get('/api/banlist', self._api_banlist)
        app.router.add_post('/api/banlist/clear', self._api_clear_banlist)
        app.router.add_get('/api/user/{email}', self._api_user_detail)
        app.router.add_get('/api/nodes', self._api_nodes)
        app.router.add_get('/api/shared_ips', self._api_shared_ips)

        self._api_runner = web.AppRunner(app)
        await self._api_runner.setup()
        site = web.TCPSite(self._api_runner, self.api_host, self.api_port)
        await site.start()

    async def _api_stats(self, request):
        """GET /api/stats - общая статистика."""
        data = {
            'total_users': self.tracker.total_users,
            'total_requests': self.tracker.total_requests,
            'total_blocked': self.tracker.total_blocked,
            'connected_nodes': list(self._connected_nodes),
            'violators_count': len(self._current_violators),
            'banlist_count': len(self._confirmed_violators),
            'panel_loaded': panel_api.is_loaded,
            'panel_users_count': len(panel_api._users) if panel_api.is_loaded else 0,
            'concurrent_window': self._concurrent_window,
            'trigger_period': self._trigger_period,
            'trigger_count': self._trigger_count,
            'banlist_threshold': self._banlist_threshold,
        }
        return web.json_response(data)

    async def _api_users(self, request):
        """GET /api/users - список всех пользователей."""
        from core.tracker import group_ips_by_subnet

        users = []
        for user in self.tracker.get_all_users():
            limit = panel_api.get_limit(user.email)
            concurrent_ips = user.get_recent_ips(self._concurrent_window, min_requests=1)

            # IP count с учётом группировки по подсетям
            if self._subnet_grouping:
                ip_count = len(group_ips_by_subnet(concurrent_ips))
                subnets = list(group_ips_by_subnet(concurrent_ips))
            else:
                ip_count = len(concurrent_ips)
                subnets = []

            # Triggers
            triggers = self._user_triggers.get(user.email, [])
            trigger_count = len(triggers)

            users.append({
                'email': user.email,
                'ip_count': ip_count,
                'ip_count_raw': len(concurrent_ips),
                'limit': limit,
                'request_count': user.request_count,
                'blocked_count': user.blocked_count,
                'last_seen': user.last_seen.isoformat() if user.last_seen else None,
                'ips': list(concurrent_ips),
                'subnets': subnets,
                'subnet_grouping': self._subnet_grouping,
                'is_violator': user.email in self._current_violators,
                'trigger_count': trigger_count,
                'trigger_threshold': self._trigger_count,
            })
        # Сортировка по IP count desc
        users.sort(key=lambda x: x['ip_count'], reverse=True)
        return web.json_response(users)

    async def _api_violators(self, request):
        """GET /api/violators - текущие пидарасы."""
        from core.tracker import group_ips_by_subnet

        violators = []
        now = datetime.now()

        # Собираем все IP для batch lookup
        all_violator_ips = set()

        for email in self._current_violators:
            user = self.tracker.get_user(email)
            if not user:
                continue

            violation_ips = self._violator_ips.get(email, set())
            violation_ips.update(user.get_recent_ips(self._concurrent_window, min_requests=1))
            all_violator_ips.update(violation_ips)

        # Делаем batch WHOIS lookup (если доступен)
        isp_info = {}
        if HAS_WHOIS and whois_lookup:
            all_ips_list = list(all_violator_ips)[:20]
            isp_info = await whois_lookup.lookup_batch_async(all_ips_list, max_lookups=20)

        for email in self._current_violators:
            user = self.tracker.get_user(email)
            if not user:
                continue

            limit = panel_api.get_limit(email)
            first_seen = self._violator_first_seen.get(email)
            time_in_violation = int((now - first_seen).total_seconds()) if first_seen else 0
            time_to_ban = max(0, self._banlist_threshold - time_in_violation)

            # Накопленные IP за время нарушения
            violation_ips = self._violator_ips.get(email, set()).copy()
            violation_ips.update(user.get_recent_ips(self._concurrent_window, min_requests=1))

            # Считаем с учётом группировки
            if self._subnet_grouping:
                total_ip_count = len(group_ips_by_subnet(violation_ips))
                subnets = list(group_ips_by_subnet(violation_ips))
            else:
                total_ip_count = len(violation_ips)
                subnets = []

            # Ноды
            nodes = set()
            for req in user.recent_requests:
                if req.node_name:
                    nodes.add(req.node_name)

            # Данные из панели
            panel_info = panel_api.get_user_info(email)

            # ISP информация
            ip_providers = {}
            for ip in violation_ips:
                info = isp_info.get(ip)
                if info:
                    ip_providers[ip] = {
                        'isp': info.get('isp', 'Unknown'),
                        'country_code': info.get('country_code', '')
                    }

            concurrent_ips = user.get_recent_ips(self._concurrent_window, min_requests=1)
            if self._subnet_grouping:
                concurrent_ip_count = len(group_ips_by_subnet(concurrent_ips))
                concurrent_subnets = list(group_ips_by_subnet(concurrent_ips))
            else:
                concurrent_ip_count = len(concurrent_ips)
                concurrent_subnets = []

            # Triggers
            trigger_count = len(self._user_triggers.get(email, []))

            violators.append({
                'email': email,
                'ip_count': total_ip_count,
                'ip_count_raw': len(violation_ips),
                'concurrent_ip_count': concurrent_ip_count,
                'limit': limit,
                'ips': list(violation_ips),
                'subnets': subnets,
                'concurrent_ips': list(concurrent_ips),
                'concurrent_subnets': concurrent_subnets,
                'ip_providers': ip_providers,
                'subnet_grouping': self._subnet_grouping,
                'nodes': list(nodes),
                'time_in_violation': time_in_violation,
                'time_to_ban': time_to_ban,
                'trigger_count': trigger_count,
                'trigger_threshold': self._trigger_count,
                'telegram_id': panel_info.get('telegram_id', '') if panel_info else '',
                'description': panel_info.get('description', '') if panel_info else '',
            })

        violators.sort(key=lambda x: x['time_in_violation'], reverse=True)
        return web.json_response(violators)

    async def _api_banlist(self, request):
        """GET /api/banlist - бан-лист из БД."""
        if not HAS_DATABASE or not db:
            return web.json_response([])
        hours = int(request.query.get('hours', 24))
        banlist = db.get_banlist(hours=hours)
        return web.json_response(banlist)

    async def _api_clear_banlist(self, request):
        """POST /api/banlist/clear - очистка бан-листа."""
        deleted = 0
        if HAS_DATABASE and db:
            deleted = db.clear_banlist()
        # Очищаем также память
        self._confirmed_violators.clear()
        logger.warning(f"Бан-лист очищен через API: удалено {deleted} записей")
        return web.json_response({'deleted': deleted, 'success': True})

    async def _api_user_detail(self, request):
        """GET /api/user/{email} - детали пользователя."""
        from core.tracker import group_ips_by_subnet

        email = request.match_info['email']
        user = self.tracker.get_user(email)

        if not user:
            return web.json_response({'error': 'User not found'}, status=404)

        panel_info = panel_api.get_user_info(email)
        limit = panel_api.get_limit(email)

        # Последние запросы
        requests = []
        for req in user.recent_requests[-50:]:
            requests.append({
                'timestamp': req.timestamp.isoformat(),
                'source_ip': req.source_ip,
                'destination': req.destination,
                'dest_port': req.dest_port,
                'action': req.action,
                'node_name': req.node_name,
            })

        # Если нарушитель - добавляем накопленные IP
        is_violator = email in self._current_violators
        violation_ips = []
        time_in_violation = 0
        if is_violator:
            violation_ips = list(self._violator_ips.get(email, set()))
            first_seen = self._violator_first_seen.get(email)
            if first_seen:
                time_in_violation = int((datetime.now() - first_seen).total_seconds())

        # IP адреса
        all_ips = list(user.all_ips)
        concurrent_ips = list(user.get_recent_ips(self._concurrent_window, min_requests=1))

        # WHOIS lookup (если доступен)
        ip_providers = {}
        if HAS_WHOIS and whois_lookup:
            all_unique_ips = list(set(all_ips + concurrent_ips + violation_ips))[:10]
            isp_info = await whois_lookup.lookup_batch_async(all_unique_ips, max_lookups=10)
            for ip in all_unique_ips:
                info = isp_info.get(ip)
                if info:
                    ip_providers[ip] = {
                        'isp': info.get('isp', 'Unknown'),
                        'org': info.get('org', ''),
                        'country': info.get('country', ''),
                        'country_code': info.get('country_code', '')
                    }

        # IP с количеством запросов
        ip_request_counts = user.get_recent_ips_with_counts(self._concurrent_window)

        # Считаем с учётом группировки
        if self._subnet_grouping:
            ip_count = len(group_ips_by_subnet(set(concurrent_ips)))
            subnets = list(group_ips_by_subnet(set(concurrent_ips)))
            violation_subnets = list(group_ips_by_subnet(set(violation_ips))) if violation_ips else []
        else:
            ip_count = len(concurrent_ips)
            subnets = []
            violation_subnets = []

        # Triggers
        trigger_count = len(self._user_triggers.get(email, []))

        data = {
            'email': email,
            'ip_count': ip_count,
            'ip_count_raw': len(concurrent_ips),
            'limit': limit,
            'request_count': user.request_count,
            'blocked_count': user.blocked_count,
            'first_seen': user.first_seen.isoformat() if user.first_seen else None,
            'last_seen': user.last_seen.isoformat() if user.last_seen else None,
            'ips': concurrent_ips,
            'subnets': subnets,
            'ip_request_counts': ip_request_counts,
            'all_ips': all_ips,
            'ip_providers': ip_providers,
            'subnet_grouping': self._subnet_grouping,
            'is_violator': is_violator,
            'trigger_count': trigger_count,
            'trigger_threshold': self._trigger_count,
            'is_banned': email in self._confirmed_violators,
            'violation_ips': violation_ips,
            'violation_subnets': violation_subnets,
            'violation_ip_count': len(violation_ips),
            'time_in_violation': time_in_violation,
            'telegram_id': panel_info.get('telegram_id', '') if panel_info else '',
            'description': panel_info.get('description', '') if panel_info else '',
            'username': panel_info.get('username', '') if panel_info else '',
            'recent_requests': requests,
        }
        return web.json_response(data)

    async def _api_nodes(self, request):
        """GET /api/nodes - подключенные ноды."""
        return web.json_response(list(self._connected_nodes))

    async def _api_shared_ips(self, request):
        """GET /api/shared_ips - IP используемые несколькими юзерами."""
        shared = self.tracker.get_shared_ips()
        result = [{'ip': ip, 'emails': list(emails)} for ip, emails in shared.items()]
        return web.json_response(result)

    async def stop(self):
        """Остановка сервера."""
        self._running = False
        if self._api_runner:
            await self._api_runner.cleanup()
        await self.tcp_server.stop()
        logger.info("Сервер остановлен")

    async def _load_panel_users(self):
        """Загрузка пользователей из панели."""
        try:
            count = await panel_api.load_all_users()
            logger.info(f"Загружено {count} пользователей из панели")
        except Exception as e:
            logger.error(f"Ошибка загрузки из панели: {e}")

    def _on_entry(self, node_name: str, entry):
        """Обработка записи от агента."""
        user = self.tracker.process_entry(entry, node_name)
        # Проверяем concurrent IPs на каждый запрос
        self._check_concurrent_ips(user, entry.timestamp)

    def _on_connect(self, node_name: str):
        """Нода подключилась."""
        self._connected_nodes.add(node_name)
        logger.info(f"Нода подключена: {node_name} (всего: {len(self._connected_nodes)})")

    def _on_disconnect(self, node_name: str):
        """Нода отключилась."""
        self._connected_nodes.discard(node_name)
        logger.info(f"Нода отключена: {node_name} (осталось: {len(self._connected_nodes)})")

    def _check_concurrent_ips(self, user, timestamp: datetime):
        """Проверка concurrent IPs на каждый запрос."""
        from core.tracker import group_ips_by_subnet

        email = user.email

        # Пропускаем whitelist
        if email in self._whitelist_emails:
            return

        # Получаем лимит
        limit = panel_api.get_limit(email)
        if limit is None or limit == 0:
            return

        self._user_limits[email] = limit

        # Считаем IP за короткое окно (одновременные)
        concurrent_ips = user.get_recent_ips(self._concurrent_window, min_requests=1)

        if self._subnet_grouping:
            ip_count = len(group_ips_by_subnet(concurrent_ips))
        else:
            ip_count = len(concurrent_ips)

        if ip_count > limit:
            # Превышение! Добавляем сработку
            if email not in self._user_triggers:
                self._user_triggers[email] = []

            self._user_triggers[email].append(timestamp)

            # Чистим старые сработки
            cutoff = timestamp - timedelta(seconds=self._trigger_period)
            self._user_triggers[email] = [t for t in self._user_triggers[email] if t >= cutoff]

            trigger_count = len(self._user_triggers[email])

            if trigger_count >= self._trigger_count:
                # ПИДАРАС!
                if email not in self._current_violators:
                    self._violator_first_seen[email] = timestamp
                    self._violator_ips[email] = set()
                    logger.info(f"ПИДАРАС: {email} IP={ip_count} лимит={limit} "
                                f"(triggers: {trigger_count}/{self._trigger_count})")

                self._current_violators.add(email)
                self._violator_ips[email].update(concurrent_ips)

    async def _periodic_tasks(self):
        """Периодические задачи."""
        check_interval = 5  # Проверка каждые 5 секунд
        cleanup_counter = 0
        panel_reload_counter = 0

        while self._running:
            await asyncio.sleep(check_interval)

            # Cleanup старых данных (каждые 30 сек)
            cleanup_counter += check_interval
            if cleanup_counter >= 30:
                cleanup_counter = 0
                self.tracker.cleanup_old_data()

            # Перезагрузка панели
            panel_reload_counter += check_interval
            if panel_reload_counter >= self._panel_reload_interval:
                panel_reload_counter = 0
                await self._load_panel_users()

            # Проверка лимитов
            await self._check_limits()

    async def _check_limits(self):
        """Проверка статусов violators и бан-листа."""
        if not panel_api.is_loaded:
            return

        now = datetime.now()

        # Проверяем каждого нарушителя
        for email in list(self._current_violators):
            user = self.tracker.get_user(email)
            if not user:
                continue

            # Проверяем актуальность triggers
            if email in self._user_triggers:
                cutoff = now - timedelta(seconds=self._trigger_period)
                self._user_triggers[email] = [t for t in self._user_triggers[email] if t >= cutoff]
                trigger_count = len(self._user_triggers[email])

                if trigger_count < self._trigger_count:
                    # Больше не пидарас
                    self._current_violators.discard(email)
                    self._violator_first_seen.pop(email, None)
                    self._violator_ips.pop(email, None)
                    logger.info(f"Больше не пидарас: {email} (triggers: {trigger_count}/{self._trigger_count})")
                    continue

            # Проверяем время в нарушении для бан-листа
            first_seen = self._violator_first_seen.get(email)
            if first_seen:
                time_in_violation = (now - first_seen).total_seconds()
                limit = self._user_limits.get(email, 0)

                if time_in_violation >= self._banlist_threshold:
                    await self._add_to_banlist(user, len(self._violator_ips.get(email, set())), limit, int(time_in_violation))

        # Чистим старые сработки для неактивных пользователей (предотвращает утечку памяти)
        for email in list(self._user_triggers.keys()):
            if email not in self._current_violators:
                triggers = self._user_triggers[email]
                cutoff = now - timedelta(seconds=self._trigger_period)
                triggers = [t for t in triggers if t >= cutoff]
                if not triggers:
                    del self._user_triggers[email]
                else:
                    self._user_triggers[email] = triggers

    async def _add_to_banlist(self, user, ip_count: int, limit: int, violation_duration: int):
        """Добавление или обновление в бан-листе."""
        if not HAS_DATABASE or not db:
            return

        # Используем накопленные IP за всё время нарушения
        all_violation_ips = self._violator_ips.get(user.email, set()).copy()
        all_violation_ips.update(user.get_recent_ips(self._concurrent_window, min_requests=1))
        violation_ips = list(all_violation_ips)

        nodes = set()
        for req in user.recent_requests:
            if req.node_name:
                nodes.add(req.node_name)

        # Данные из панели
        panel_info = panel_api.get_user_info(user.email)
        tg_id = str(panel_info.get('telegram_id', '')) if panel_info else ''
        description = panel_info.get('description', '') if panel_info else ''

        now = datetime.now()

        # Проверяем есть ли уже активный бан
        active_ban = db.get_active_ban(user.email, hours=24)

        # Количество уникальных IP за всё время нарушения
        total_ip_count = len(violation_ips)

        if active_ban:
            # Пользователь уже в бане - обновляем запись
            self._confirmed_violators.add(user.email)
            try:
                db.update_ban_entry(
                    record_id=active_ban['id'],
                    ip_count=total_ip_count,
                    ips=violation_ips,
                    nodes=list(nodes),
                    violation_duration=violation_duration
                )
            except Exception as e:
                logger.error(f"Ошибка обновления БД: {e}")

            logger.info(f"БАН-ЛИСТ (обновлено): {user.email} IP={total_ip_count} (в нарушении {violation_duration}с)")

            # Отправляем уведомление о продолжении (с ограничением частоты)
            if HAS_TELEGRAM and telegram:
                last_notif = self._last_notification.get(user.email)
                if last_notif is None or (now - last_notif).total_seconds() >= self._notification_interval:
                    try:
                        await telegram.send_violation_continues_async(
                            email=user.email,
                            telegram_id=tg_id,
                            description=description,
                            ip_count=total_ip_count,
                            ips=violation_ips,
                            nodes=list(nodes),
                            violation_duration=violation_duration,
                            limit=limit
                        )
                        self._last_notification[user.email] = now
                    except Exception as e:
                        logger.error(f"Ошибка отправки в Telegram: {e}")
        else:
            # Новый бан - создаём запись
            self._confirmed_violators.add(user.email)

            try:
                db.add_to_banlist(
                    email=user.email,
                    telegram_id=tg_id,
                    description=description,
                    ip_count=total_ip_count,
                    ips=violation_ips,
                    nodes=list(nodes),
                    violation_duration=violation_duration,
                    detected_at=now
                )
            except Exception as e:
                logger.error(f"Ошибка сохранения в БД: {e}")

            logger.warning(f"БАН-ЛИСТ (новый): {user.email} IP={total_ip_count} ноды={nodes} (в нарушении {violation_duration}с)")

            # Отправляем первое уведомление
            if HAS_TELEGRAM and telegram:
                try:
                    await telegram.send_violation_async(
                        email=user.email,
                        telegram_id=tg_id,
                        description=description,
                        ip_count=total_ip_count,
                        ips=violation_ips,
                        nodes=list(nodes),
                        violation_duration=violation_duration,
                        limit=limit
                    )
                    self._last_notification[user.email] = now
                except Exception as e:
                    logger.error(f"Ошибка отправки в Telegram: {e}")


async def main():
    server = BanhammerServer()

    # Обработка сигналов
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(server.stop()))

    try:
        await server.start()
    except asyncio.CancelledError:
        pass
    finally:
        await server.stop()


if __name__ == '__main__':
    asyncio.run(main())
