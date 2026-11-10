"""OKX V5 REST-клиент: публичные данные + подписанные приватные запросы.

Аутентификация: HMAC-SHA256 (OK-ACCESS-KEY/SIGN/TIMESTAMP/PASSPHRASE).
OKX_DEMO=1 → заголовок x-simulated-trading: 1 (демо-счёт OKX).
Без ключей работают только публичные эндпоинты (свечи, инструменты).
"""
import base64
import hashlib
import hmac
import json
import time
import urllib.parse

import requests

BASE = "https://www.okx.com"


class Okx:
    def __init__(self, key=None, secret=None, passphrase=None, demo=False):
        self.key = key or ""
        self.secret = secret or ""
        self.passphrase = passphrase or ""
        self.demo = bool(demo)
        self.s = requests.Session()

    @property
    def authed(self):
        return bool(self.key and self.secret and self.passphrase)

    def public_get(self, path, params=None):
        r = self.s.get(BASE + path, params=params or {}, timeout=20)
        return r.json()

    def _headers(self, method, path, body=""):
        ts = str(int(time.time() * 1000))
        msg = (ts + method + path + body).encode()
        sign = base64.b64encode(
            hmac.new(self.secret.encode(), msg, hashlib.sha256).digest()).decode()
        h = {
            "OK-ACCESS-KEY": self.key,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
        }
        if self.demo:
            h["x-simulated-trading"] = "1"
        return h

    def get(self, path, params=None):
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        return self.s.get(BASE + path + q, headers=self._headers("GET", path + q), timeout=20).json()

    def post(self, path, payload):
        body = json.dumps(payload)
        return self.s.post(BASE + path, data=body,
                           headers=self._headers("POST", path, body), timeout=20).json()
