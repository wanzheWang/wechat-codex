from __future__ import annotations

import hashlib
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass


@dataclass(frozen=True)
class WeChatTextMessage:
    to_user: str
    from_user: str
    content: str
    msg_id: str | None


def verify_signature(token: str, signature: str, timestamp: str, nonce: str) -> bool:
    values = [token, timestamp, nonce]
    values.sort()
    digest = hashlib.sha1("".join(values).encode("utf-8")).hexdigest()
    return digest == signature


def parse_text_message(body: bytes) -> WeChatTextMessage:
    root = ET.fromstring(body)
    msg_type = _text(root, "MsgType")
    if msg_type != "text":
        raise ValueError(f"unsupported message type: {msg_type}")
    return WeChatTextMessage(
        to_user=_text(root, "ToUserName"),
        from_user=_text(root, "FromUserName"),
        content=_text(root, "Content").strip(),
        msg_id=_optional_text(root, "MsgId"),
    )


def text_reply(*, to_user: str, from_user: str, content: str) -> bytes:
    xml = (
        "<xml>"
        f"<ToUserName><![CDATA[{to_user}]]></ToUserName>"
        f"<FromUserName><![CDATA[{from_user}]]></FromUserName>"
        f"<CreateTime>{int(time.time())}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{content}]]></Content>"
        "</xml>"
    )
    return xml.encode("utf-8")


def _text(root: ET.Element, name: str) -> str:
    value = _optional_text(root, name)
    if value is None:
        raise ValueError(f"missing XML field: {name}")
    return value


def _optional_text(root: ET.Element, name: str) -> str | None:
    child = root.find(name)
    if child is None:
        return None
    return child.text or ""
