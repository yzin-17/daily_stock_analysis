# -*- coding: utf-8 -*-
"""数据服务入口：uvicorn server:app --host 0.0.0.0 --port 8000。"""

import logging

from src.mini_racer_runtime import install_mini_racer_serialization

install_mini_racer_serialization()

from api.data_app import create_app
from src.data_config import get_data_config
from src.logging_config import setup_logging

config = get_data_config()
level = getattr(logging, (config.log_level or "INFO").upper(), logging.INFO)
setup_logging(log_prefix="api_server", log_dir=config.log_dir, console_level=level,
              extra_quiet_loggers=["uvicorn", "fastapi"])
app = create_app()

__all__ = ["app"]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
