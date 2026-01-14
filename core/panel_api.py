"""
Клиент API панели Remnawave для получения лимитов устройств.
"""

import os
import logging
import time
from typing import Optional, Dict
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class PanelAPI:
    """Клиент API панели."""

    def __init__(self):
        self.base_url = os.getenv('PANEL_URL', 'http://127.0.0.1:3000')
        self.token = os.getenv('PANEL_TOKEN', '')
        self._users: Dict[str, Dict] = {}
        self._loaded = False
        self._last_load_time = 0
        self._load_interval = 300

        logger.info(f"PanelAPI инициализирован: {self.base_url}")

    def load_all_users_sync(self) -> int:
        """Загружает всех пользователей из панели (синхронно)."""
        logger.info("=== Загрузка пользователей из панели ===")

        headers = {
            'Authorization': f'Bearer {self.token}',
            'X-Forwarded-For': '127.0.0.1',
            'X-Forwarded-Proto': 'https',
            'X-Forwarded-Host': 'localhost',
        }

        all_users = []
        start = 0
        page_size = 500

        while True:
            url = f"{self.base_url}/api/users?start={start}&size={page_size}"
            logger.info(f"GET {url}")

            try:
                resp = requests.get(url, headers=headers, timeout=30)

                if resp.status_code != 200:
                    logger.error(f"Ошибка загрузки: HTTP {resp.status_code}")
                    break

                data = resp.json()
                response = data.get('response', {})
                if isinstance(response, dict):
                    users = response.get('users', [])
                else:
                    users = response if response else []

                if not users:
                    break

                all_users.extend(users)
                logger.info(f"Загружено {len(users)} юзеров (всего: {len(all_users)})")

                if len(users) < page_size:
                    break

                start += page_size
                time.sleep(0.1)

            except Exception as e:
                logger.error(f"Ошибка загрузки страницы {start}: {e}")
                break

        self._users.clear()
        for user in all_users:
            user_id = str(user.get('id', ''))
            if user_id:
                self._users[user_id] = {
                    'limit': user.get('hwidDeviceLimit', 1),
                    'telegram_id': user.get('telegramId'),
                    'description': user.get('description', ''),
                    'username': user.get('username', ''),
                    'short_uuid': user.get('shortUuid', ''),
                }

        self._loaded = True
        self._last_load_time = time.time()

        logger.info(f"=== Загружено {len(self._users)} пользователей ===")
        return len(self._users)

    async def load_all_users(self) -> int:
        """Async обёртка для совместимости."""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.load_all_users_sync)

    def get_limit(self, user_id: str) -> Optional[int]:
        """Получает лимит устройств из кэша."""
        user = self._users.get(user_id)
        return user['limit'] if user else None

    def get_user_info(self, user_id: str) -> Optional[Dict]:
        """Получает полную информацию о пользователе."""
        return self._users.get(user_id)

    def needs_reload(self) -> bool:
        """Нужно ли перезагрузить данные."""
        if not self._loaded:
            return True
        return time.time() - self._last_load_time > self._load_interval

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def user_count(self) -> int:
        return len(self._users)


# Глобальный экземпляр
panel_api = PanelAPI()
