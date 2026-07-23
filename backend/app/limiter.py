"""slowapi 限流器 - 用于保护 Auth 等敏感端点免受暴力破解。"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
