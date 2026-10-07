"""Redis/RQ configuration and enqueue helpers."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from redis import Redis
from rq import Queue, Retry
from rq.job import Job

DEFAULT_REDIS_URL = "redis://localhost:6379/0"
DEFAULT_QUEUE_NAME = "default"
DEFAULT_RETRY = Retry(max=3, interval=[10, 60, 300])
DEFAULT_TIMEOUT = "10m"

def create_queue() -> Queue:
    """Create a queue from environment configuration.

    Redis connections are lazy, so importing this module does not require a
    running Redis server. The first queue operation will establish the
    connection.
    """

    redis_url = os.getenv("REDIS_URL", DEFAULT_REDIS_URL)
    queue_name = os.getenv("RQ_QUEUE_NAME", DEFAULT_QUEUE_NAME)
    connection = Redis.from_url(redis_url)
    return Queue(name=queue_name, connection=connection)

queue = create_queue()

