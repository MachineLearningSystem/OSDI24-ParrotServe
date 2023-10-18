from dataclasses import dataclass

from parrot.constants import DEFAULT_SERVER_HOST, DEFAULT_OS_SERVER_PORT


@dataclass
class OSConfig:
    host: str = DEFAULT_SERVER_HOST
    port: int = DEFAULT_OS_SERVER_PORT
    max_proc_num: int = 2048
    max_engines_num: int = 2048
