from loguru import logger
import sys
from config import LOG_FILE, LOG_LEVEL

def setup_logger():
    """Configures logging system."""
    
    # Remove default handlers
    logger.remove()
    
    # Console handler with colors
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=LOG_LEVEL,
        colorize=True
    )
    
    # File handler for persistence
    logger.add(
        LOG_FILE,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        level=LOG_LEVEL,
        rotation="10 MB",  # Rotate file when it reaches 10MB
        retention="30 days",  # Keep logs for 30 days
        compression="zip"  # Compress old logs
    )
    
    logger.info("Logging system initialized")
    
    return logger

# Initialize logger when module is imported
logger = setup_logger()