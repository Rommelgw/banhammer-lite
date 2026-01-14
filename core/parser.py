import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class LogEntry:
    """Запись лога Xray."""
    timestamp: datetime
    source_ip: str
    protocol: str  # tcp или udp
    destination: str  # IP или домен
    destination_port: int
    action: str  # DIRECT, BLOCK, shadow-out
    email: str
    raw_line: str


class LogParser:
    """Парсер логов Xray."""

    # Паттерн для парсинга строки лога
    PATTERN = re.compile(
        r'(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}\.\d+)\s+'  # timestamp
        r'from\s+(?:tcp:|udp:)?(\d+\.\d+\.\d+\.\d+):\d+\s+'  # source IP
        r'accepted\s+'
        r'(tcp|udp):([^:]+):(\d+)\s+'  # protocol:destination:port
        r'\[.*?(?:>>|->)\s*(\w+(?:-\w+)?)\]\s+'  # action
        r'email:\s*(\S+)'  # email
    )

    @classmethod
    def parse_line(cls, line: str) -> Optional[LogEntry]:
        """Парсит одну строку лога."""
        line = line.strip()
        if not line:
            return None

        match = cls.PATTERN.match(line)
        if not match:
            return None

        try:
            timestamp_str, source_ip, protocol, destination, dest_port, action, email = match.groups()
            timestamp = datetime.strptime(timestamp_str, '%Y/%m/%d %H:%M:%S.%f')

            return LogEntry(
                timestamp=timestamp,
                source_ip=source_ip,
                protocol=protocol,
                destination=destination,
                destination_port=int(dest_port),
                action=action,
                email=email,
                raw_line=line
            )
        except (ValueError, IndexError):
            return None
