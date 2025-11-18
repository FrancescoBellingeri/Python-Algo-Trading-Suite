from loguru import logger
import sys
from config import LOG_FILE, LOG_LEVEL

def setup_logger():
    """Configura il sistema di logging."""
    
    # Rimuovi i handler di default
    logger.remove()
    
    # Console handler con colori
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=LOG_LEVEL,
        colorize=True
    )
    
    # File handler per persistenza
    logger.add(
        LOG_FILE,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        level=LOG_LEVEL,
        rotation="10 MB",  # Ruota il file quando raggiunge 10MB
        retention="30 days",  # Mantieni i log per 30 giorni
        compression="zip"  # Comprimi i vecchi log
    )
    
    logger.info("Sistema di logging inizializzato")
    
    return logger

# Inizializza il logger quando il modulo viene importato
logger = setup_logger()