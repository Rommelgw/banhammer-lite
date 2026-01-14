"""
TCP сервер для приёма логов от агентов.
"""

import asyncio
import logging
from typing import Callable, Optional, Set
from dataclasses import dataclass

from .parser import LogParser, LogEntry

logger = logging.getLogger(__name__)


@dataclass
class NodeConnection:
    """Информация о подключённой ноде."""
    node_name: str
    address: str
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter


class TCPLogServer:
    """TCP сервер для приёма логов от агентов на нодах."""

    def __init__(self, host: str = '0.0.0.0', port: int = 9999):
        self.host = host
        self.port = port
        self._server = None
        self._connections: list = []
        self._on_entry_callback: Optional[Callable[[str, LogEntry], None]] = None
        self._on_connect_callback: Optional[Callable[[str], None]] = None
        self._on_disconnect_callback: Optional[Callable[[str], None]] = None

    def on_entry(self, callback: Callable[[str, LogEntry], None]):
        """Установить callback для новых записей."""
        self._on_entry_callback = callback

    def on_connect(self, callback: Callable[[str], None]):
        """Callback при подключении ноды."""
        self._on_connect_callback = callback

    def on_disconnect(self, callback: Callable[[str], None]):
        """Callback при отключении ноды."""
        self._on_disconnect_callback = callback

    async def start(self):
        """Запуск сервера."""
        logger.info(f"Запуск сервера на {self.host}:{self.port}")
        self._server = await asyncio.start_server(
            self._handle_client,
            self.host,
            self.port
        )
        addr = self._server.sockets[0].getsockname()
        logger.info(f"TCP сервер запущен на {addr[0]}:{addr[1]}")
        print(f"[*] TCP сервер запущен на {addr[0]}:{addr[1]}")

        async with self._server:
            await self._server.serve_forever()

    async def stop(self):
        """Остановка сервера."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()

        for conn in list(self._connections):
            conn.writer.close()
            try:
                await conn.writer.wait_closed()
            except:
                pass

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Обработка подключения клиента."""
        addr = writer.get_extra_info('peername')
        node_name = f"unknown-{addr[0]}"
        logger.info(f"Новое подключение от {addr}")

        conn = NodeConnection(
            node_name=node_name,
            address=f"{addr[0]}:{addr[1]}",
            reader=reader,
            writer=writer
        )
        self._connections.append(conn)

        try:
            while True:
                data = await reader.readline()
                if not data:
                    logger.info(f"Клиент {addr} отключился (EOF)")
                    break

                line = data.decode('utf-8', errors='ignore').strip()
                if not line:
                    continue

                # Формат: NODE_NAME|строка_лога
                if '|' in line:
                    parts = line.split('|', 1)
                    node_name = parts[0]
                    log_line = parts[1]

                    if conn.node_name != node_name:
                        conn.node_name = node_name
                        logger.info(f"Нода идентифицирована: {node_name}")
                        if self._on_connect_callback:
                            try:
                                self._on_connect_callback(node_name)
                            except Exception as e:
                                logger.error(f"Ошибка в on_connect_callback: {e}")

                    entry = LogParser.parse_line(log_line)
                    if entry and self._on_entry_callback:
                        try:
                            self._on_entry_callback(node_name, entry)
                        except Exception as e:
                            logger.error(f"Ошибка в on_entry_callback: {e}")

        except asyncio.CancelledError:
            logger.info(f"Соединение с {addr} отменено")
        except Exception as e:
            logger.error(f"Ошибка от {addr}: {e}", exc_info=True)
        finally:
            if conn in self._connections:
                self._connections.remove(conn)
            if self._on_disconnect_callback:
                try:
                    self._on_disconnect_callback(conn.node_name)
                except Exception as e:
                    logger.error(f"Ошибка в on_disconnect_callback: {e}")
            writer.close()
            try:
                await writer.wait_closed()
            except:
                pass
            logger.info(f"Соединение с {addr} закрыто")

    @property
    def connected_nodes(self) -> Set[str]:
        """Список подключённых нод."""
        return {conn.node_name for conn in self._connections}

    @property
    def connection_count(self) -> int:
        """Количество подключений."""
        return len(self._connections)
