#!/usr/bin/env python3
"""
UI клиент для Banhammer.
Подключается к работающему серверу через HTTP API.
"""

import os
import sys
import asyncio
import argparse
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Часовой пояс (UTC+3)
LOCAL_TZ_OFFSET = 3
from typing import Optional
from dotenv import load_dotenv

# Загружаем .env из директории скрипта
SCRIPT_DIR = Path(__file__).parent.absolute()
ENV_FILE = SCRIPT_DIR / '.env'
load_dotenv(ENV_FILE)

import aiohttp
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, Static, DataTable, TabbedContent, TabPane, Input, Button
from rich.text import Text


def to_local_time(time_str: str) -> str:
    """Конвертирует UTC время в локальное (UTC+3)."""
    if not time_str or time_str == '-':
        return '-'
    try:
        # Пробуем разные форматы
        for fmt in ['%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%d %H:%M:%S.%f']:
            try:
                dt = datetime.strptime(time_str[:19], fmt[:19].replace('.%f', ''))
                dt = dt + timedelta(hours=LOCAL_TZ_OFFSET)
                return dt.strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                continue
        return time_str[:19]
    except Exception:
        return time_str[:19] if len(time_str) >= 19 else time_str


class APIClient:
    """Клиент для HTTP API сервера."""

    def __init__(self, base_url: str, token: str = ''):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {}
            if self.token:
                headers['Authorization'] = f'Bearer {self.token}'
            self._session = aiohttp.ClientSession(headers=headers)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def get(self, endpoint: str, params: dict = None):
        session = await self._get_session()
        url = f"{self.base_url}{endpoint}"
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return await resp.json()
                elif resp.status == 401:
                    return {'error': 'Unauthorized - неверный токен'}
                return None
        except Exception:
            return None

    async def get_stats(self):
        return await self.get('/api/stats')

    async def get_users(self):
        return await self.get('/api/users')

    async def get_violators(self):
        return await self.get('/api/violators')

    async def get_banlist(self, hours: int = 24):
        return await self.get('/api/banlist', {'hours': hours})

    async def get_user_detail(self, email: str):
        return await self.get(f'/api/user/{email}')

    async def get_shared_ips(self):
        return await self.get('/api/shared_ips')

    async def clear_banlist(self):
        """Очистка бан-листа."""
        session = await self._get_session()
        url = f"{self.base_url}/api/banlist/clear"
        try:
            async with session.post(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None
        except Exception:
            return None


class StatsPanel(Static):
    """Панель статистики."""

    def __init__(self):
        super().__init__()
        self._stats = {}

    def update_stats(self, stats: dict):
        self._stats = stats or {}
        self.refresh()

    def render(self):
        if not self._stats:
            return Text("Нет подключения к серверу...", style="bold white on red")

        if 'error' in self._stats:
            return Text(f"Ошибка: {self._stats['error']}", style="bold white on red")

        nodes = self._stats.get('connected_nodes', [])
        nodes_str = ', '.join(nodes) if nodes else 'нет'

        text = Text()
        text.append("Пользователей: ", style="bold white")
        text.append(f"{self._stats.get('total_users', 0)}  ", style="bold cyan")
        text.append("Запросов: ", style="bold white")
        text.append(f"{self._stats.get('total_requests', 0)}  ", style="bold green")
        text.append("Пидарасов: ", style="bold white")
        text.append(f"{self._stats.get('violators_count', 0)}  ", style="bold yellow")
        text.append("В бан-листе: ", style="bold white")
        text.append(f"{self._stats.get('banlist_count', 0)}  ", style="bold red")
        text.append("Ноды: ", style="bold white")
        text.append(nodes_str, style="bold magenta")
        return text


class BanhammerClient(App):
    """TUI клиент для Banhammer."""

    CSS = """
    Screen {
        background: #1e1e1e;
        color: #ffffff;
    }

    StatsPanel {
        height: 5;
        padding: 1 2;
        background: #2d2d2d;
        border: solid #4a9eff;
        color: #ffffff;
    }

    #search-container {
        height: 3;
        padding: 0 1;
    }

    #search-input {
        width: 100%;
    }

    DataTable {
        height: auto;
        color: #ffffff;
    }

    #detail-content {
        padding: 1;
        color: #ffffff;
    }

    #banlist-controls {
        height: 3;
        padding: 0 1;
    }

    #clear-banlist-btn {
        margin-right: 1;
    }
    """

    BINDINGS = [
        ("q", "quit", "Выход"),
        ("r", "refresh", "Обновить"),
        ("/", "focus_search", "Поиск"),
        ("1", "tab_users", "Пользователи"),
        ("2", "tab_violators", "Пидарасы"),
        ("3", "tab_banlist", "Бан-лист"),
        ("4", "tab_shared", "Общие IP"),
    ]

    def __init__(self, server_url: str, token: str = ''):
        super().__init__()
        self.api = APIClient(server_url, token)
        self.server_url = server_url
        self._selected_email: Optional[str] = None  # Текущий открытый пользователь для авто-обновления

    def compose(self) -> ComposeResult:
        yield Header()
        yield StatsPanel()
        with Horizontal(id="search-container"):
            yield Input(placeholder="Введите email для поиска...", id="search-input")
        with TabbedContent(initial="tab-users"):
            with TabPane("Пользователи", id="tab-users"):
                yield DataTable(id="users-table")
            with TabPane("Пидарасы", id="tab-violators"):
                yield DataTable(id="violators-table")
            with TabPane("Бан-лист", id="tab-banlist"):
                with Vertical():
                    with Horizontal(id="banlist-controls"):
                        yield Button("Очистить бан-лист", id="clear-banlist-btn", variant="error")
                    yield DataTable(id="banlist-table")
            with TabPane("Общие IP", id="tab-shared"):
                yield DataTable(id="shared-table")
            with TabPane("Детали", id="tab-detail"):
                yield Static(id="detail-content")
        yield Footer()

    def on_mount(self):
        """При запуске приложения."""
        self.title = f"Banhammer UI - {self.server_url}"
        self._setup_tables()
        # Основное обновление таблиц - каждые 2 сек
        self.set_interval(2, self._refresh_tables)
        # Обновление открытой карточки - каждые 0.5 сек (плавный realtime)
        self.set_interval(0.5, self._refresh_detail_realtime)

    def _setup_tables(self):
        """Настройка колонок таблиц."""
        # Пользователи
        users_table = self.query_one("#users-table", DataTable)
        users_table.show_header = True
        users_table.zebra_stripes = True
        users_table.add_columns("Email", "IP", "Лимит", "Triggers", "Запросы", "Последняя активность")
        users_table.cursor_type = "row"

        # Пидарасы
        violators_table = self.query_one("#violators-table", DataTable)
        violators_table.show_header = True
        violators_table.zebra_stripes = True
        violators_table.add_columns("Email", "IP всего", "IP сейчас", "Лимит", "Triggers", "В нарушении", "До бана")
        violators_table.cursor_type = "row"

        # Бан-лист
        banlist_table = self.query_one("#banlist-table", DataTable)
        banlist_table.show_header = True
        banlist_table.zebra_stripes = True
        banlist_table.add_columns("Email", "TG ID", "Описание", "IP", "Ноды", "Время")
        banlist_table.cursor_type = "row"

        # Общие IP
        shared_table = self.query_one("#shared-table", DataTable)
        shared_table.show_header = True
        shared_table.zebra_stripes = True
        shared_table.add_columns("IP", "Пользователи")

    async def _refresh_tables(self):
        """Обновление таблиц (каждые 2 сек)."""
        # Статистика
        stats = await self.api.get_stats()
        if stats:
            stats_panel = self.query_one(StatsPanel)
            stats_panel.update_stats(stats)

        # Пользователи
        users = await self.api.get_users()
        if users and isinstance(users, list):
            await self._update_users_table(users)

        # Пидарасы
        violators = await self.api.get_violators()
        if violators and isinstance(violators, list):
            await self._update_violators_table(violators)

        # Бан-лист
        banlist = await self.api.get_banlist()
        if banlist and isinstance(banlist, list):
            await self._update_banlist_table(banlist)

        # Общие IP
        shared = await self.api.get_shared_ips()
        if shared and isinstance(shared, list):
            await self._update_shared_table(shared)

    async def _refresh_detail_realtime(self):
        """Обновление открытой карточки в реальном времени (каждые 0.5 сек)."""
        if not self._selected_email:
            return

        detail = await self.api.get_user_detail(self._selected_email)
        if detail:
            await self._render_user_detail(detail)

    async def _refresh_all(self):
        """Полное обновление всех данных (для ручного вызова)."""
        await self._refresh_tables()
        if self._selected_email:
            await self._refresh_user_detail()

    async def _refresh_user_detail(self):
        """Обновление открытой карточки пользователя."""
        if not self._selected_email:
            return

        detail = await self.api.get_user_detail(self._selected_email)
        if detail:
            await self._render_user_detail(detail)

    async def _update_users_table(self, users: list):
        """Обновление таблицы пользователей."""
        table = self.query_one("#users-table", DataTable)
        table.clear()

        for user in users[:100]:
            email = user['email']
            ip_count = user['ip_count']
            ip_count_raw = user.get('ip_count_raw', ip_count)
            limit = user['limit'] if user['limit'] else '-'
            trigger_count = user.get('trigger_count', 0)
            trigger_threshold = user.get('trigger_threshold', 5)
            requests = user['request_count']
            last_seen = to_local_time(user['last_seen'])

            # Показываем подсети/IP если группировка включена
            if user.get('subnet_grouping') and ip_count != ip_count_raw:
                ip_str = f"{ip_count}({ip_count_raw})"  # подсетей(IP)
            else:
                ip_str = str(ip_count)

            # Triggers: показываем count/threshold
            if trigger_count > 0:
                triggers_str = Text(f"{trigger_count}/{trigger_threshold}", style="yellow")
            else:
                triggers_str = "-"

            if user.get('is_violator'):
                email = Text(email, style="bold red")
                ip_str = Text(ip_str, style="bold red")
                triggers_str = Text(f"{trigger_count}/{trigger_threshold}", style="bold red")

            table.add_row(email, ip_str, limit, triggers_str, requests, last_seen, key=user['email'])

    async def _update_violators_table(self, violators: list):
        """Обновление таблицы пидарасов."""
        table = self.query_one("#violators-table", DataTable)
        table.clear()

        for v in violators:
            email = v['email']
            ip_total = v['ip_count']
            ip_total_raw = v.get('ip_count_raw', ip_total)
            ip_current = v.get('concurrent_ip_count', v.get('current_ip_count', ip_total))
            limit = v['limit'] if v['limit'] else '-'
            trigger_count = v.get('trigger_count', 0)
            trigger_threshold = v.get('trigger_threshold', 5)
            time_in = self._format_duration(v['time_in_violation'])
            time_to_ban = self._format_duration(v['time_to_ban'])

            # Показываем подсети(IP) если группировка включена
            if v.get('subnet_grouping') and ip_total != ip_total_raw:
                ip_total_str = f"{ip_total}({ip_total_raw})"
            else:
                ip_total_str = str(ip_total)

            # Подсветка если много IP накопилось
            if ip_total_raw > ip_current + 2:
                ip_total_str = Text(ip_total_str, style="bold yellow")

            triggers_str = f"{trigger_count}/{trigger_threshold}"

            if v['time_to_ban'] < 60:
                email = Text(email, style="bold red")
                time_to_ban = Text(time_to_ban, style="bold red")

            table.add_row(email, ip_total_str, ip_current, limit, triggers_str, time_in, time_to_ban, key=v['email'])

    async def _update_banlist_table(self, banlist: list):
        """Обновление таблицы бан-листа."""
        table = self.query_one("#banlist-table", DataTable)
        table.clear()

        for b in banlist:
            email = b['email']
            tg_id = b.get('telegram_id', '-') or '-'
            description = b.get('description', '-') or '-'
            if len(str(description)) > 20:
                description = str(description)[:20] + '...'
            ip_count = b['ip_count']
            nodes = ', '.join(b['nodes']) if b.get('nodes') else '-'
            detected = to_local_time(b.get('detected_at'))[:16]

            # Используем email как ключ для перехода в карточку
            table.add_row(email, tg_id, description, ip_count, nodes, detected, key=email)

    async def _update_shared_table(self, shared: list):
        """Обновление таблицы общих IP."""
        table = self.query_one("#shared-table", DataTable)
        table.clear()

        for item in shared:
            ip = item['ip']
            emails = ', '.join(item['emails'])
            table.add_row(ip, emails)

    def _format_duration(self, seconds: int) -> str:
        """Форматирование длительности."""
        if seconds < 60:
            return f"{seconds}с"
        elif seconds < 3600:
            return f"{seconds // 60}м {seconds % 60}с"
        else:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours}ч {minutes}м"

    async def on_input_submitted(self, event: Input.Submitted):
        """При нажатии Enter в поле поиска."""
        if event.input.id == "search-input" and event.value:
            await self._show_user_detail(event.value.strip())
            event.input.value = ""

    async def on_data_table_row_selected(self, event: DataTable.RowSelected):
        """При выборе строки в таблице."""
        if event.row_key:
            email = str(event.row_key.value)
            await self._show_user_detail(email)

    async def on_button_pressed(self, event: Button.Pressed):
        """Обработка нажатия кнопок."""
        if event.button.id == "clear-banlist-btn":
            result = await self.api.clear_banlist()
            if result and result.get('success'):
                self.notify(f"Бан-лист очищен: удалено {result.get('deleted', 0)} записей", severity="warning")
                await self._refresh_all()
            else:
                self.notify("Ошибка очистки бан-листа", severity="error")

    async def _show_user_detail(self, email: str):
        """Показать детали пользователя."""
        self._selected_email = email  # Сохраняем для авто-обновления

        detail = await self.api.get_user_detail(email)
        content = self.query_one("#detail-content", Static)

        if not detail:
            content.update(Text(f"Пользователь {email} не найден", style="red"))
            self._selected_email = None
            return

        await self._render_user_detail(detail)

        tabs = self.query_one(TabbedContent)
        tabs.active = "tab-detail"

    async def _render_user_detail(self, detail: dict):
        """Рендеринг карточки пользователя."""
        content = self.query_one("#detail-content", Static)
        text = Text()
        text.append(f"Email: ", style="bold")
        text.append(f"{detail['email']}\n")

        if detail.get('telegram_id'):
            text.append(f"Telegram ID: ", style="bold")
            text.append(f"{detail['telegram_id']}\n")

        if detail.get('description'):
            text.append(f"Описание: ", style="bold")
            text.append(f"{detail['description']}\n")

        # Показываем IP/подсети
        ip_count = detail['ip_count']
        ip_count_raw = detail.get('ip_count_raw', ip_count)
        subnet_grouping = detail.get('subnet_grouping', False)

        if subnet_grouping:
            text.append(f"\nПодсетей: ", style="bold")
            text.append(f"{ip_count}")
            text.append(f" (IP: {ip_count_raw})", style="dim")
        else:
            text.append(f"\nIP адресов: ", style="bold")
            text.append(f"{ip_count}")

        if detail.get('limit'):
            text.append(f" / лимит: {detail['limit']}")
        text.append("\n")

        # Показываем подсети если группировка включена
        subnets = detail.get('subnets', [])
        if subnet_grouping and subnets:
            text.append(f"Подсети: ", style="bold")
            text.append(f"{', '.join(subnets)}.x\n", style="cyan")

        text.append(f"Запросов: ", style="bold")
        text.append(f"{detail['request_count']}\n")

        text.append(f"Заблокировано: ", style="bold")
        text.append(f"{detail['blocked_count']}\n")

        # Получаем информацию о провайдерах
        ip_providers = detail.get('ip_providers', {})

        # Triggers info
        trigger_count = detail.get('trigger_count', 0)
        trigger_threshold = detail.get('trigger_threshold', 5)
        if trigger_count > 0:
            if trigger_count >= trigger_threshold:
                text.append(f"\nТриггеры: ", style="bold red")
                text.append(f"{trigger_count}/{trigger_threshold}\n", style="red")
            else:
                text.append(f"\nТриггеры: ", style="bold yellow")
                text.append(f"{trigger_count}/{trigger_threshold}\n", style="yellow")

        if detail.get('is_violator'):
            time_viol = detail.get('time_in_violation', 0)
            text.append(f"\nНАРУШИТЕЛЬ ", style="bold red")
            text.append(f"(в нарушении {time_viol}с)\n", style="red")

            # Показываем подсети нарушения если группировка включена
            violation_subnets = detail.get('violation_subnets', [])
            if subnet_grouping and violation_subnets:
                text.append(f"\nПодсети за время нарушения ({len(violation_subnets)}):\n", style="bold red")
                for subnet in violation_subnets:
                    text.append(f"  - {subnet}.x\n", style="red")

            # Показываем накопленные IP за время нарушения
            violation_ips = detail.get('violation_ips', [])
            if violation_ips:
                text.append(f"\nIP за время нарушения ({len(violation_ips)}):\n", style="bold red")
                for ip in violation_ips:
                    provider_info = ip_providers.get(ip, {})
                    isp = provider_info.get('isp', '')
                    country = provider_info.get('country_code', '')
                    if isp:
                        isp_str = f" ({isp}" + (f", {country})" if country else ")")
                        text.append(f"  - {ip}", style="red")
                        text.append(f"{isp_str}\n", style="dim red")
                    else:
                        text.append(f"  - {ip}\n", style="red")

        if detail.get('is_banned'):
            text.append("\nВ БАН-ЛИСТЕ\n", style="bold red")

        text.append(f"\nТекущие IP ({len(detail.get('ips', []))}):\n", style="bold")
        for ip in detail.get('ips', []):
            provider_info = ip_providers.get(ip, {})
            isp = provider_info.get('isp', '')
            country = provider_info.get('country_code', '')
            if isp:
                isp_str = f" ({isp}" + (f", {country})" if country else ")")
                text.append(f"  - {ip}")
                text.append(f"{isp_str}\n", style="dim")
            else:
                text.append(f"  - {ip}\n")

        requests = detail.get('recent_requests', [])
        if requests:
            text.append(f"\nПоследние запросы ({len(requests)}):\n", style="bold")
            for req in requests[-20:]:
                ts = to_local_time(req['timestamp'])[11:19]
                text.append(f"  {ts} ", style="dim")
                text.append(f"{req['source_ip']} ")
                text.append(f"-> {req['destination']}:{req['dest_port']} ", style="cyan")
                if req.get('node_name'):
                    text.append(f"[{req['node_name']}]", style="magenta")
                text.append("\n")

        content.update(text)

    def action_refresh(self):
        """Принудительное обновление."""
        asyncio.create_task(self._refresh_all())

    def action_focus_search(self):
        """Фокус на поле поиска."""
        self.query_one("#search-input", Input).focus()

    def action_tab_users(self):
        tabs = self.query_one(TabbedContent)
        tabs.active = "tab-users"

    def action_tab_violators(self):
        tabs = self.query_one(TabbedContent)
        tabs.active = "tab-violators"

    def action_tab_banlist(self):
        tabs = self.query_one(TabbedContent)
        tabs.active = "tab-banlist"

    def action_tab_shared(self):
        tabs = self.query_one(TabbedContent)
        tabs.active = "tab-shared"

    async def action_quit(self):
        """Выход."""
        await self.api.close()
        self.exit()


def main():
    parser = argparse.ArgumentParser(description='Banhammer UI Client')
    parser.add_argument(
        '--server', '-s',
        default=os.getenv('BANHAMMER_API_URL', 'http://localhost:8089'),
        help='URL сервера Banhammer'
    )
    parser.add_argument(
        '--token', '-t',
        default=os.getenv('BANHAMMER_API_TOKEN', ''),
        help='Токен для доступа к API'
    )
    args = parser.parse_args()

    if not args.token:
        print("ОШИБКА: Токен не указан! Используй --token или установи BANHAMMER_API_TOKEN в .env")
        sys.exit(1)

    app = BanhammerClient(args.server, args.token)
    app.run()


if __name__ == '__main__':
    main()
