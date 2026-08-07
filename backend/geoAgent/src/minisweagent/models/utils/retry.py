"""用于模型查询的重试实用工具。"""

import logging
import os

from tenacity import Retrying, before_sleep_log, retry_if_not_exception_type, stop_after_attempt, wait_exponential


def retry(*, logger: logging.Logger, abort_exceptions: list[type[Exception]]) -> Retrying:
    """对 tenacity.Retrying 的薄封装，以利用全局配置等。

    参数：
        logger：用于报告重试的记录器
        abort_exceptions：要中止的异常列表。

    返回：
        一个 tenacity.Retrying 对象。
    """
    return Retrying(
        reraise=True,
        stop=stop_after_attempt(int(os.getenv("MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT", "10"))),
        wait=wait_exponential(multiplier=1, min=4, max=60),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        retry=retry_if_not_exception_type(tuple(abort_exceptions)),
    )
