import hashlib
import hmac
import os
import secrets
import time


class Auth:
    def __init__(self):
        self.password = os.getenv('APP_PASSWORD', '')
        self.secret = os.getenv('SESSION_SECRET', '')
        if len(self.password) < 16 or len(self.secret) < 32:
            raise RuntimeError('APP_PASSWORD 至少16字符，SESSION_SECRET 至少32字符')
        self.attempts = []

    def login(self, password):
        current = time.monotonic()
        self.attempts = [stamp for stamp in self.attempts if current - stamp < 300]
        if len(self.attempts) >= 10:
            raise ValueError('尝试过多，请5分钟后重试')
        if not secrets.compare_digest(password.encode(), self.password.encode()):
            self.attempts.append(current)
            raise ValueError('密码不正确')
        self.attempts.clear()
        body = f'{int(time.time()) + 28800}.{secrets.token_hex(16)}'
        return body + '.' + hmac.new(self.secret.encode(), body.encode(), hashlib.sha256).hexdigest()

    def valid(self, cookie):
        try:
            expiry, nonce, signature = cookie.split('.')
            body = f'{expiry}.{nonce}'
            expected = hmac.new(self.secret.encode(), body.encode(), hashlib.sha256).hexdigest()
            return int(expiry) > time.time() and hmac.compare_digest(signature, expected)
        except (AttributeError, ValueError):
            return False
