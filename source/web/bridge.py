"""浏览器版入口：在 Pyodide 里跑，把前端的 fetch 变成对 route_* 的直接调用。

**这个文件就是「HTTP 之上的那一层」的全部。** play.py 里的 route_get /
route_post 一行不改地被复用，sim/ 和 game/ 更是完全没碰过——网页版和本地版
共用同一份引擎，不存在「两套数值实现早晚不一致」这个最贵的错误。

它做的事只有三件：把 zip 里的源码摊到虚拟文件系统上、告诉 play.py 美术
资源改由静态站点发（前缀和存在性判断都换掉）、把 (method, path, body)
翻译成一次函数调用。
"""

from __future__ import annotations

import json

_play = None


def setup(art_prefix: str = "art/", art_index_json: str = "[]",
          art_ext: str = ".webp") -> str:
    """一次性初始化。返回一句人话给加载界面显示。"""
    global _play
    import play                                   # noqa: PLC0415
    # 浏览器里没有 assets/art：图片由静态站点按相对路径发，
    # 「这张图存不存在」只能查清单，不能查文件系统。
    play.ART_PREFIX = art_prefix
    play.ART_EXT = art_ext or None
    play.ART_INDEX = set(json.loads(art_index_json or "[]"))
    _play = play
    return "引擎就位"


def handle(method: str, path: str, body_json: str = "") -> str:
    """一次请求。返回 JSON 字符串 {status, ctype, body}。"""
    if _play is None:
        raise RuntimeError("boot() 还没跑")
    if method == "GET":
        code, ctype, data = _play.route_get(path)
    else:
        try:
            body = json.loads(body_json or "{}")
        except Exception:
            body = {}
        code, ctype, data = _play.route_post(path, body)
    return json.dumps({"status": code, "ctype": ctype,
                       "body": data.decode("utf-8", "replace")})


def save_path() -> str:
    return str(_play.SAVE)


def has_save() -> bool:
    return _play.SAVE.exists()
