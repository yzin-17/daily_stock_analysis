import hashlib
import os
import random
import secrets
import threading
import time
import requests
import json
import uuid
import logging
from fake_useragent import UserAgent

logger = logging.getLogger(__name__)

original_request = requests.Session.request

ua = UserAgent()


class AuthCache:
    def __init__(self):
        self.data = None
        self.expire_at = 0
        self.lock = threading.Lock()
        self.ttl = 20


_cache = AuthCache()


class PatchSign:
    def __init__(self):
        self.patched = False

    def set_patch(self, patched):
        self.patched = patched

    def is_patched(self):
        return self.patched


_patch_sign = PatchSign()


EASTMONEY_COOKIE_ENV = "EFINANCE_EASTMONEY_COOKIE"


def _configured_eastmoney_cookie():
    """Return the optional Eastmoney Cookie without exposing its value."""
    cookie = os.getenv(EASTMONEY_COOKIE_ENV, "").strip()
    if not cookie:
        return None
    if "\r" in cookie or "\n" in cookie:
        logger.warning("忽略包含非法换行符的东方财富 Cookie 配置")
        return None
    return cookie


def _merge_cookie_values(*cookie_values):
    """Merge Cookie header values by cookie name, preserving the last value."""
    merged = {}
    for cookie_value in cookie_values:
        if not cookie_value:
            continue
        for item in str(cookie_value).split(";"):
            item = item.strip()
            if not item or "=" not in item:
                continue
            name, value = item.split("=", 1)
            name = name.strip()
            value = value.strip()
            if name:
                merged[name] = value
    return "; ".join(f"{name}={value}" for name, value in merged.items())


def configure_eastmoney_cookie():
    """Inject the optional Cookie into efinance's shared Eastmoney headers."""
    cookie = _configured_eastmoney_cookie()
    if not cookie:
        return False
    try:
        from efinance.common import config as efinance_config

        headers = getattr(efinance_config, "EASTMONEY_REQUEST_HEADERS", None)
        if not isinstance(headers, dict):
            logger.warning("efinance 未提供可配置的东方财富请求头")
            return False
        headers["Cookie"] = _merge_cookie_values(headers.get("Cookie"), cookie)
        return True
    except (ImportError, AttributeError):
        logger.warning("efinance 未加载，暂不注入东方财富 Cookie")
        return False


def _get_nid(user_agent):
    """
    获取东方财富的 NID 授权令牌

    Args:
        user_agent (str): 用户代理字符串，用于模拟不同的浏览器访问

    Returns:
        str: 返回获取到的 NID 授权令牌，如果获取失败则返回 None

    功能说明:
        该函数通过向东方财富的授权接口发送请求来获取 NID 令牌，
        用于后续的数据访问授权。函数实现了缓存机制来避免频繁请求。
    """
    now = time.time()
    # 检查缓存是否有效，避免重复请求
    if _cache.data and now < _cache.expire_at:
        return _cache.data
    # 使用线程锁确保并发安全
    with _cache.lock:
        try:
            def generate_uuid_md5():
                """
                生成 UUID 并对其进行 MD5 哈希处理
                :return: MD5 哈希值（32位十六进制字符串）
                """
                # 生成 UUID
                unique_id = str(uuid.uuid4())
                # 对 UUID 进行 MD5 哈希
                md5_hash = hashlib.md5(unique_id.encode('utf-8')).hexdigest()
                return md5_hash

            def generate_st_nvi():
                """
                生成 st_nvi 值的方法
                :return: 返回生成的 st_nvi 值
                """
                HASH_LENGTH = 4  # 截取哈希值的前几位

                def generate_random_string(length=21):
                    """
                    生成指定长度的随机字符串
                    :param length: 字符串长度，默认为 21
                    :return: 随机字符串
                    """
                    charset = "useandom-26T198340PX75pxJACKVERYMINDBUSHWOLF_GQZbfghjklqvwyzrict"
                    return ''.join(secrets.choice(charset) for _ in range(length))

                def sha256(input_str):
                    """
                    计算 SHA-256 哈希值
                    :param input_str: 输入字符串
                    :return: 哈希值（十六进制）
                    """
                    return hashlib.sha256(input_str.encode('utf-8')).hexdigest()

                random_str = generate_random_string()
                hash_prefix = sha256(random_str)[:HASH_LENGTH]
                return random_str + hash_prefix

            url = "https://anonflow2.eastmoney.com/backend/api/webreport"
            # 随机选择屏幕分辨率，增加请求的真实性
            screen_resolution = random.choice(['1920X1080', '2560X1440', '3840X2160'])
            payload = json.dumps({
                "osPlatform": "Windows",
                "sourceType": "WEB",
                "osversion": "Windows 10.0",
                "language": "zh-CN",
                "timezone": "Asia/Shanghai",
                "webDeviceInfo": {
                    "screenResolution": screen_resolution,
                    "userAgent": user_agent,
                    "canvasKey": generate_uuid_md5(),
                    "webglKey": generate_uuid_md5(),
                    "fontKey": generate_uuid_md5(),
                    "audioKey": generate_uuid_md5()
                }
            })
            headers = {
                'Cookie': f'st_nvi={generate_st_nvi()}',
                'Content-Type': 'application/json'
            }
            # 增加超时，防止无限等待
            response = requests.request("POST", url, headers=headers, data=payload, timeout=30)
            response.raise_for_status()  # 对 4xx/5xx 响应抛出 HTTPError

            data = response.json()
            nid = data['data']['nid']

            _cache.data = nid
            _cache.expire_at = now + _cache.ttl
            return nid
        except requests.exceptions.RequestException as e:
            logger.warning(f"请求东方财富授权接口失败: {e}")
            _cache.data = None
            # 该接口请求失败时，方案可能已失效，后续大概率会继续失败，因无法成功获取，下次会继续请求，设置较长过期时间，可避免频繁请求
            _cache.expire_at = now + 5 * 60
            return None
        except (KeyError, json.JSONDecodeError) as e:
            logger.warning(f"解析东方财富授权接口响应失败: {e}")
            _cache.data = None
            # 该接口请求失败时，方案可能已失效，后续大概率会继续失败，因无法成功获取，下次会继续请求，设置较长过期时间，可避免频繁请求
            _cache.expire_at = now + 5 * 60
            return None


def eastmoney_patch():
    configure_eastmoney_cookie()
    if _patch_sign.is_patched():
        return

    def patched_request(self, method, url, **kwargs):
        # 排除非目标域名
        is_target = any(
            d in (url or "")
            for d in [
                "fund.eastmoney.com",
                "push2.eastmoney.com",
                "push2his.eastmoney.com",
            ]
        )
        if not is_target:
            return original_request(self, method, url, **kwargs)
        # 获取一个随机的 User-Agent
        user_agent = ua.random
        # 处理 Headers：确保不破坏业务代码传入的 headers
        headers = dict(kwargs.get("headers") or {})
        headers["User-Agent"] = user_agent
        nid = _get_nid(user_agent)
        configured_cookie = _configured_eastmoney_cookie()
        existing_cookie = headers.get("Cookie") or headers.get("cookie")
        merged_cookie = _merge_cookie_values(existing_cookie, configured_cookie)
        if nid:
            merged_cookie = _merge_cookie_values(merged_cookie, f"nid18={nid}")
        if merged_cookie:
            headers["Cookie"] = merged_cookie
        kwargs["headers"] = headers
        # 随机休眠，降低被封风险
        sleep_time = random.uniform(1, 4)
        time.sleep(sleep_time)
        return original_request(self, method, url, **kwargs)

    # 全局替换 Session 的 request 入口
    requests.Session.request = patched_request
    _patch_sign.set_patch(True)
