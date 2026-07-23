import logging
from cryptography.fernet import Fernet
from app.config import settings

logger = logging.getLogger(__name__)

_dev_fernet_key: str | None = None


def _get_fernet():
    global _dev_fernet_key
    key = settings.encryption_key
    if not key:
        # 开发环境兜底：生成随机临时密钥并告警（生产环境已在 config.py 启动时拒绝）
        # 注意：临时密钥进程重启后会变化，导致此前加密的 API Key 无法解密
        if _dev_fernet_key is None:
            _dev_fernet_key = Fernet.generate_key().decode()
            logger.warning(
                "ENCRYPTION_KEY 未设置，已生成进程内随机临时密钥。"
                "重启后此前加密的数据将无法解密，生产环境务必通过 ENCRYPTION_KEY 注入固定密钥。"
            )
        key = _dev_fernet_key
    return Fernet(key.encode() if isinstance(key, str) else key)

def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode()).decode()

def decrypt(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    return _get_fernet().decrypt(ciphertext.encode()).decode()
