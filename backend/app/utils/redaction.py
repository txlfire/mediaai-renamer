"""对嵌入普通文本的 URL 凭据进行脱敏。"""

import re

_URL_USERINFO = re.compile(r"(https?://)[^\s/\"'<>?#]*@", re.IGNORECASE)


def redactUrlCredentials(text: str) -> str:
    return _URL_USERINFO.sub(r"\1******@", text)
