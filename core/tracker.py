from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Set, List, Optional, Tuple
from collections import defaultdict

from .parser import LogEntry


def get_subnet_24(ip: str) -> str:
    """Возвращает /24 подсеть для IP адреса.

    Например: 79.137.136.214 -> 79.137.136
    """
    parts = ip.split('.')
    if len(parts) == 4:
        return '.'.join(parts[:3])
    return ip


def group_ips_by_subnet(ips: Set[str]) -> Set[str]:
    """Группирует IP по /24 подсетям.

    Возвращает set уникальных подсетей.
    Например: {79.137.136.214, 79.137.136.215, 8.8.8.8} -> {79.137.136, 8.8.8}
    """
    return {get_subnet_24(ip) for ip in ips}


@dataclass
class RequestLog:
    """Запись одного запроса."""
    timestamp: datetime
    source_ip: str
    destination: str
    dest_port: int
    action: str
    node_name: str = ""  # Имя ноды откуда пришёл запрос


@dataclass
class IPStats:
    """Статистика по IP адресу."""
    last_seen: datetime
    request_count: int = 1  # Количество запросов с этого IP


@dataclass
class UserInfo:
    """Информация о пользователе."""
    email: str
    # IP -> статистика (время + количество запросов)
    ip_stats: Dict[str, IPStats] = field(default_factory=dict)
    # Для обратной совместимости
    ip_timestamps: Dict[str, datetime] = field(default_factory=dict)
    request_count: int = 0
    blocked_count: int = 0
    last_seen: Optional[datetime] = None
    first_seen: Optional[datetime] = None
    # Последние N запросов для детальной статистики
    recent_requests: List['RequestLog'] = field(default_factory=list)
    _max_requests: int = 100  # Хранить последние 100 запросов

    def add_ip(self, ip: str, timestamp: datetime) -> None:
        """Добавляет/обновляет IP-адрес с временем и счётчиком запросов."""
        if ip in self.ip_stats:
            self.ip_stats[ip].last_seen = timestamp
            self.ip_stats[ip].request_count += 1
        else:
            self.ip_stats[ip] = IPStats(last_seen=timestamp, request_count=1)
        # Обратная совместимость
        self.ip_timestamps[ip] = timestamp

    def add_request(self, timestamp: datetime, source_ip: str, destination: str, dest_port: int, action: str, node_name: str = "") -> None:
        """Добавляет запрос в историю."""
        self.recent_requests.append(RequestLog(timestamp, source_ip, destination, dest_port, action, node_name))
        # Ограничиваем размер
        if len(self.recent_requests) > self._max_requests:
            self.recent_requests = self.recent_requests[-self._max_requests:]

    def get_recent_ips(self, window_seconds: int = 60, min_requests: int = 1) -> Set[str]:
        """Получает IP-адреса активные за последние N секунд.

        Args:
            window_seconds: Окно времени в секундах
            min_requests: Минимум запросов с IP для учёта (защита от случайных переподключений)
        """
        if not self.last_seen:
            return set()

        cutoff = self.last_seen - timedelta(seconds=window_seconds)
        result = set()

        for ip, stats in self.ip_stats.items():
            # IP активен если был виден в окне времени И имеет достаточно запросов
            if stats.last_seen >= cutoff and stats.request_count >= min_requests:
                result.add(ip)

        return result

    def cleanup_old_ips(self, window_seconds: int = 60) -> int:
        """Удаляет IP которые не были активны за последние N секунд.

        Returns:
            Количество удалённых IP
        """
        if not self.last_seen:
            return 0

        cutoff = self.last_seen - timedelta(seconds=window_seconds)
        ips_to_remove = [ip for ip, stats in self.ip_stats.items() if stats.last_seen < cutoff]

        for ip in ips_to_remove:
            del self.ip_stats[ip]
            self.ip_timestamps.pop(ip, None)

        return len(ips_to_remove)

    def get_recent_ips_with_counts(self, window_seconds: int = 60) -> Dict[str, int]:
        """Получает IP-адреса с количеством запросов за последние N секунд."""
        if not self.last_seen:
            return {}

        cutoff = self.last_seen - timedelta(seconds=window_seconds)
        return {
            ip: stats.request_count
            for ip, stats in self.ip_stats.items()
            if stats.last_seen >= cutoff
        }

    def get_ip_switch_rate(self, last_n_requests: int = 20) -> float:
        """Вычисляет частоту смены IP в последних N запросах.

        Возвращает процент (0.0-1.0) запросов где IP отличается от предыдущего.

        Примеры:
        - 2 устройства чередуются: ~0.5 (50%)
        - Шаринг (постоянно новые IP): ~0.9 (90%)
        - 1 устройство: ~0.0 (0%)

        Returns:
            float: Процент переключений IP (0.0-1.0)
        """
        if len(self.recent_requests) < 2:
            return 0.0

        # Берём последние N запросов
        requests = self.recent_requests[-last_n_requests:]
        if len(requests) < 2:
            return 0.0

        switches = 0
        for i in range(1, len(requests)):
            if requests[i].source_ip != requests[i-1].source_ip:
                switches += 1

        return switches / (len(requests) - 1)

    def get_ip_diversity(self, last_n_requests: int = 20) -> Tuple[int, int, float]:
        """Вычисляет разнообразие IP в последних N запросах.

        Returns:
            Tuple[unique_ips, total_requests, diversity_ratio]
            - unique_ips: количество уникальных IP
            - total_requests: количество запросов (до last_n_requests)
            - diversity_ratio: unique_ips / total_requests (0.0-1.0)

        Примеры:
        - 2 устройства, 20 запросов: (2, 20, 0.1)
        - Шаринг 10 IP, 20 запросов: (10, 20, 0.5)
        """
        if len(self.recent_requests) < 1:
            return (0, 0, 0.0)

        requests = self.recent_requests[-last_n_requests:]
        unique_ips = len(set(r.source_ip for r in requests))
        total = len(requests)

        return (unique_ips, total, unique_ips / total if total > 0 else 0.0)

    @property
    def all_ips(self) -> Set[str]:
        """Все IP-адреса за всё время."""
        return set(self.ip_stats.keys())

    def recent_ip_count(self, window_seconds: int = 60, min_requests: int = 1, group_by_subnet: bool = False) -> int:
        """Количество уникальных IP за последние N секунд.

        Args:
            window_seconds: Окно времени
            min_requests: Минимум запросов с IP
            group_by_subnet: Группировать IP по /24 подсети
        """
        ips = self.get_recent_ips(window_seconds, min_requests)
        if group_by_subnet:
            return len(group_ips_by_subnet(ips))
        return len(ips)

    def get_recent_subnets(self, window_seconds: int = 60, min_requests: int = 1) -> Set[str]:
        """Получает уникальные /24 подсети за последние N секунд."""
        ips = self.get_recent_ips(window_seconds, min_requests)
        return group_ips_by_subnet(ips)

    def has_multiple_recent_ips(self, window_seconds: int = 30, min_ips: int = 3, min_requests: int = 1, group_by_subnet: bool = False) -> bool:
        """Есть ли подозрительное количество IP за последние N секунд."""
        return self.recent_ip_count(window_seconds, min_requests, group_by_subnet) >= min_ips


class UserTracker:
    """Трекер пользователей и их IP-адресов."""

    def __init__(self, window_seconds: int = 30, min_ips_for_alert: int = 3, max_age_seconds: int = 120):
        """
        Args:
            window_seconds: Окно времени для отслеживания IP (по умолчанию 30 секунд)
            min_ips_for_alert: Минимум IP для пометки как подозрительный (по умолчанию 3)
            max_age_seconds: Максимальный возраст данных (по умолчанию 120 секунд)
        """
        self._users: Dict[str, UserInfo] = {}
        self._total_requests: int = 0
        self._total_blocked: int = 0
        self.window_seconds = window_seconds
        self.min_ips_for_alert = min_ips_for_alert
        self.max_age_seconds = max_age_seconds
        self._latest_timestamp: Optional[datetime] = None

    def process_entry(self, entry: LogEntry, node_name: str = "") -> UserInfo:
        """Обрабатывает запись лога."""
        email = entry.email

        if email not in self._users:
            self._users[email] = UserInfo(email=email)

        user = self._users[email]

        # Обновляем IP с временной меткой
        user.add_ip(entry.source_ip, entry.timestamp)
        user.request_count += 1

        # Сохраняем запрос в историю
        user.add_request(
            entry.timestamp,
            entry.source_ip,
            entry.destination,
            entry.destination_port,
            entry.action,
            node_name
        )

        if user.first_seen is None:
            user.first_seen = entry.timestamp
        user.last_seen = entry.timestamp

        if entry.action == 'BLOCK':
            user.blocked_count += 1
            self._total_blocked += 1

        self._total_requests += 1

        # Обновляем последний timestamp
        if self._latest_timestamp is None or entry.timestamp > self._latest_timestamp:
            self._latest_timestamp = entry.timestamp

        return user

    def cleanup_old_data(self) -> int:
        """Удаляет устаревшие данные.

        Returns:
            Количество удалённых пользователей
        """
        if self._latest_timestamp is None:
            return 0

        cutoff = self._latest_timestamp - timedelta(seconds=self.max_age_seconds)
        users_to_remove = []

        for email, user in self._users.items():
            if user.last_seen and user.last_seen < cutoff:
                users_to_remove.append(email)
            else:
                # Очищаем старые IP у активных пользователей
                user.cleanup_old_ips(self.window_seconds)

        for email in users_to_remove:
            del self._users[email]

        return len(users_to_remove)

    def get_user(self, email: str) -> Optional[UserInfo]:
        """Получает информацию о пользователе."""
        return self._users.get(email)

    def get_all_users(self) -> List[UserInfo]:
        """Получает список всех пользователей."""
        return list(self._users.values())

    def get_users_with_multiple_ips(self) -> List[UserInfo]:
        """Получает пользователей с подозрительным количеством IP (3+) за последние N секунд."""
        return [u for u in self._users.values()
                if u.has_multiple_recent_ips(self.window_seconds, self.min_ips_for_alert)]

    def get_shared_ips(self) -> Dict[str, Set[str]]:
        """Получает IP-адреса, которые используются несколькими пользователями за последние N секунд."""
        ip_to_emails: Dict[str, Set[str]] = defaultdict(set)

        for user in self._users.values():
            for ip in user.get_recent_ips(self.window_seconds):
                ip_to_emails[ip].add(user.email)

        return {ip: emails for ip, emails in ip_to_emails.items() if len(emails) > 1}

    @property
    def total_users(self) -> int:
        """Общее количество пользователей."""
        return len(self._users)

    @property
    def total_requests(self) -> int:
        """Общее количество запросов."""
        return self._total_requests

    @property
    def total_blocked(self) -> int:
        """Общее количество заблокированных запросов."""
        return self._total_blocked

    def clear(self):
        """Очищает все данные."""
        self._users.clear()
        self._total_requests = 0
        self._total_blocked = 0
