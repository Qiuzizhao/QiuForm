"""极简 multipart/form-data 解析器（只用标准库）。

Python 3.13 起标准库的 cgi 模块已被移除，而且它也不返回文件的原始字节，
所以自己实现一份。只处理浏览器产生的格式，够用即可。
"""

import re


def _param(header: str, key: str):
    m = re.search(key + r'\s*=\s*"([^"]*)"', header, re.I)
    if m:
        return m.group(1)
    m = re.search(key + r"\s*=\s*([^;]+)", header, re.I)
    return m.group(1).strip() if m else None


def _fix_utf8(text):
    """浏览器按 UTF-8 发头部的 name / filename，而我们用 latin-1 解了头，这里还原。

    中文的字段名（name="姓名"）和文件名（filename="作业.png"）都要走这一步，
    否则存进库的键会变成"å§å"这样的乱码。
    """
    if text is None:
        return None
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def boundary_of(content_type: str):
    if not content_type or "multipart/form-data" not in content_type.lower():
        return None
    value = _param(content_type, "boundary")
    return value.encode("latin-1") if value else None


def is_multipart(content_type: str) -> bool:
    return boundary_of(content_type) is not None


def parse(body: bytes, content_type: str):
    """解析请求体，返回 (fields, files)。

    fields: {字段名: [值, ...]}
    files:  [{field, filename, content_type, data}, ...]
    """
    boundary = boundary_of(content_type)
    if not boundary:
        raise ValueError("不是 multipart/form-data 或缺少 boundary")

    delimiter = b"--" + boundary
    fields: dict = {}
    files: list = []

    for chunk in body.split(delimiter)[1:]:
        if chunk[:2] == b"--":
            break  # 结束标记
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]

        head, sep, data = chunk.partition(b"\r\n\r\n")
        if not sep:
            continue
        if data.endswith(b"\r\n"):
            data = data[:-2]

        headers = {}
        for line in head.split(b"\r\n"):
            if b":" in line:
                key, _, value = line.partition(b":")
                headers[key.strip().lower().decode("latin-1")] = (
                    value.strip().decode("latin-1")
                )

        disposition = headers.get("content-disposition", "")
        name = _fix_utf8(_param(disposition, "name"))
        if name is None:
            continue

        filename = _fix_utf8(_param(disposition, "filename"))
        if filename is not None:
            files.append({
                "field": name,
                "filename": filename,
                "content_type": headers.get("content-type", "application/octet-stream"),
                "data": data,
            })
        else:
            fields.setdefault(name, []).append(data.decode("utf-8", errors="replace"))

    return fields, files
