from __future__ import annotations

import sys

from loguru import logger as _logger


def get_logger():
    # 模块导入时统一重置默认 sink，避免重复 add 导致同一条日志输出多次。
    # 这样 API、后台线程和测试环境都能看到同一套日志格式。
    _logger.remove()
    _logger.add(
        sys.stderr,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        enqueue=True,
    )
    return _logger


logger = get_logger()
