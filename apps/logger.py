from pathlib import Path
import logging

# Project root
BASE_DIR = Path(__file__).resolve().parent.parent

# Logs directory and file
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "app.log"

# Ensure logs directory exists
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Create logger config
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE), # write logs only to file (no terminal output)
        # logging.StreamHandler()
    ]
)

logging.getLogger("uvicorn").setLevel(logging.WARNING)
logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

def get_logger(name: str):
    logger = logging.getLogger(name)

    if not logger.handlers: # prevent duplicate handlers
        log_file = LOG_DIR / f"{name.lower()}.log"

        file_handler = logging.FileHandler(log_file)
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        )

        file_handler.setFormatter(formatter)

        logger.addHandler(file_handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False # avoid duplicate logs in app.log

    return logger
