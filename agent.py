#!/usr/bin/env python3
"""
Агент для отправки логов на сервер Banhammer.
Запускается на каждой ноде VPN.
"""

import os
import sys
import time
import socket
import signal
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Настройка логирования
log_level = os.getenv('LOG_LEVEL', 'INFO')
logging.basicConfig(
    level=getattr(logging, log_level),
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger('agent')


class LogAgent:
    """Агент для отправки логов."""

    def __init__(self):
        # Настройки из ENV
        self.node_name = os.getenv('NODE_NAME', socket.gethostname())
        self.server_host = os.getenv('BANHAMMER_HOST', 'localhost')
        self.server_port = int(os.getenv('BANHAMMER_PORT', '9999'))
        self.log_file = os.getenv('LOG_FILE', '/var/log/xray/access.log')
        self.reconnect_delay = int(os.getenv('RECONNECT_DELAY', '5'))

        self._running = False
        self._socket = None

        logger.info(f"Агент '{self.node_name}' -> {self.server_host}:{self.server_port}")
        logger.info(f"Лог файл: {self.log_file}")

    def start(self):
        """Запуск агента."""
        self._running = True

        while self._running:
            try:
                self._connect()
                self._tail_log()
            except KeyboardInterrupt:
                logger.info("Остановка по Ctrl+C")
                break
            except Exception as e:
                logger.error(f"Ошибка: {e}")
                self._disconnect()
                if self._running:
                    logger.info(f"Переподключение через {self.reconnect_delay} сек...")
                    time.sleep(self.reconnect_delay)

    def stop(self):
        """Остановка агента."""
        self._running = False
        self._disconnect()

    def _connect(self):
        """Подключение к серверу."""
        logger.info(f"Подключение к {self.server_host}:{self.server_port}...")
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.connect((self.server_host, self.server_port))
        self._socket.settimeout(30)
        logger.info("Подключено!")

    def _disconnect(self):
        """Отключение."""
        if self._socket:
            try:
                self._socket.close()
            except:
                pass
            self._socket = None

    def _send_line(self, line: str):
        """Отправка строки на сервер."""
        if not self._socket:
            return

        # Формат: NODE_NAME|строка_лога
        message = f"{self.node_name}|{line}\n"
        self._socket.sendall(message.encode('utf-8'))

    def _tail_log(self):
        """Чтение лога в режиме tail -f."""
        log_path = Path(self.log_file)

        # Ждём пока файл появится
        while not log_path.exists() and self._running:
            logger.warning(f"Файл {self.log_file} не найден, ожидание...")
            time.sleep(5)

        if not self._running:
            return

        logger.info(f"Чтение {self.log_file}...")

        with open(log_path, 'r') as f:
            # Переходим в конец файла
            f.seek(0, 2)

            while self._running:
                line = f.readline()
                if line:
                    line = line.strip()
                    if line and 'email:' in line:  # Фильтруем только нужные строки
                        try:
                            self._send_line(line)
                        except socket.error as e:
                            logger.error(f"Ошибка отправки: {e}")
                            raise
                else:
                    # Проверяем ротацию лога
                    try:
                        if log_path.stat().st_ino != os.fstat(f.fileno()).st_ino:
                            logger.info("Обнаружена ротация лога, переоткрытие...")
                            break
                    except:
                        pass
                    time.sleep(0.1)


def main():
    agent = LogAgent()

    # Обработка сигналов
    def signal_handler(sig, frame):
        logger.info("Получен сигнал остановки")
        agent.stop()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    agent.start()


if __name__ == '__main__':
    main()
