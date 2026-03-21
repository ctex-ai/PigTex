import uvicorn
import os
import logging
from app.config import get_settings

if __name__ == "__main__":
    raw_level = os.getenv("PIGTEX_APP_LOG_LEVEL", os.getenv("PIGTEX_LOG_LEVEL", "INFO")).strip().upper()
    app_log_level = getattr(logging, raw_level, logging.INFO)

    # Uvicorn config does not always expose application logger output by default.
    # Ensure PigTex app logs (app.* / pigtex.*) are visible for backend observability.
    logging.basicConfig(
        level=app_log_level,
        format="%(levelname)s:%(name)s:%(message)s",
    )
    logging.getLogger("app").setLevel(app_log_level)
    logging.getLogger("pigtex").setLevel(app_log_level)

    settings = get_settings()
    reload_flag = os.getenv("PIGTEX_RELOAD", "0").strip().lower() in {"1", "true", "yes", "on"}
    forwarded_allow_ips = os.getenv("FORWARDED_ALLOW_IPS", "*").strip() or "*"
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=reload_flag,
        proxy_headers=True,
        forwarded_allow_ips=forwarded_allow_ips,
    )
