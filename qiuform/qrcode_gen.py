"""二维码生成（纯 Python，无第三方依赖）。

只做字节模式、纠错等级 M、版本 1~10，够放下一个网址即可。
按 ISO/IEC 18004 实现：数据编码 → RS 纠错 → 分块交织 → 矩阵排布 →
掩码打分 → 格式/版本信息。输出直接是可以内嵌进 HTML 的 SVG。

之所以自己写而不是引一个库：整站零外部依赖，二维码只是一个很小的附属功能，
而且 SVG 矢量渲染在打印和手机上都更清楚。
"""

import html

# 版本 → (总码字数, 每块纠错码字, [(块数, 每块数据码字), ...])，纠错等级 M
BLOCKS_M = {
    1: (26, 10, [(1, 16)]),
    2: (44, 16, [(1, 28)]),
    3: (70, 26, [(1, 44)]),
    4: (100, 18, [(2, 32)]),
    5: (134, 24, [(2, 43)]),
    6: (172, 16, [(4, 27)]),
    7: (196, 18, [(4, 31)]),
    8: (242, 22, [(2, 38), (2, 39)]),
    9: (292, 22, [(3, 36), (2, 37)]),
    10: (346, 26, [(4, 43), (1, 44)]),
}

# 校正图形中心坐标
ALIGNMENT = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
    6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
}

MASK_FUNCS = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


# ---------------------------------------------------------------- 有限域

_EXP = [0] * 512
_LOG = [0] * 256


def _init_tables():
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_init_tables()


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(nsym: int):
    gen = [1]
    for i in range(nsym):
        gen = _poly_mul(gen, [1, _EXP[i]])
    return gen


def _poly_mul(p, q):
    res = [0] * (len(p) + len(q) - 1)
    for j, qj in enumerate(q):
        for i, pi in enumerate(p):
            res[i + j] ^= _gf_mul(pi, qj)
    return res


def _rs_encode(data, nsym: int, gen):
    out = list(data) + [0] * nsym
    for i in range(len(data)):
        coef = out[i]
        if coef:
            for j in range(1, len(gen)):
                out[i + j] ^= _gf_mul(gen[j], coef)
    return out[len(data):]


# ---------------------------------------------------------------- 位流

def _pick_version(nbytes: int) -> int:
    for version, (total, ec, groups) in BLOCKS_M.items():
        capacity = total - ec * sum(n for n, _ in groups)
        header_bits = 4 + (8 if version < 10 else 16)
        if nbytes * 8 + header_bits <= capacity * 8:
            return version
    raise ValueError("内容太长，放不下（本实现只支持到版本 10）")


def _bit_stream(data: bytes, version: int) -> list:
    total, ec, groups = BLOCKS_M[version]
    capacity = total - ec * sum(n for n, _ in groups)

    bits = []
    _push(bits, 0b0100, 4)                       # 字节模式
    _push(bits, len(data), 8 if version < 10 else 16)
    for byte in data:
        _push(bits, byte, 8)

    remaining = capacity * 8 - len(bits)
    _push(bits, 0, min(4, remaining))            # 结束符
    while len(bits) % 8:                         # 补齐到整字节
        bits.append(0)

    codewords = [int("".join(map(str, bits[i:i + 8])), 2)
                 for i in range(0, len(bits), 8)]
    pad = [0xEC, 0x11]
    i = 0
    while len(codewords) < capacity:
        codewords.append(pad[i % 2])
        i += 1
    return codewords


def _push(bits: list, value: int, length: int):
    for i in range(length - 1, -1, -1):
        bits.append((value >> i) & 1)


def _interleave(codewords, version: int) -> list:
    total, ec_len, groups = BLOCKS_M[version]
    gen = _rs_generator(ec_len)

    blocks = []
    pos = 0
    for count, size in groups:
        for _ in range(count):
            block = codewords[pos:pos + size]
            pos += size
            blocks.append((block, _rs_encode(block, ec_len, gen)))

    result = []
    max_data = max(len(b[0]) for b in blocks)
    for i in range(max_data):
        for data, _ec in blocks:
            if i < len(data):
                result.append(data[i])
    for i in range(ec_len):
        for _data, ec in blocks:
            result.append(ec[i])
    return result


def _final_bits(codewords) -> list:
    bits = []
    for cw in codewords:
        _push(bits, cw, 8)
    return bits


# ---------------------------------------------------------------- 矩阵

def _new_matrix(size: int):
    return [[0] * size for _ in range(size)], [[False] * size for _ in range(size)]


def _set(matrix, reserved, row, col, value):
    if 0 <= row < len(matrix) and 0 <= col < len(matrix):
        matrix[row][col] = 1 if value else 0
        reserved[row][col] = True


def _place_finder(matrix, reserved, row, col):
    for r in range(-1, 8):
        for c in range(-1, 8):
            if r in (-1, 7) or c in (-1, 7):
                _set(matrix, reserved, row + r, col + c, 0)   # 分隔带
                continue
            dark = (0 <= r <= 6 and c in (0, 6)) or (0 <= c <= 6 and r in (0, 6)) \
                   or (2 <= r <= 4 and 2 <= c <= 4)
            _set(matrix, reserved, row + r, col + c, 1 if dark else 0)


def _place_alignment(matrix, reserved, version):
    centers = ALIGNMENT[version]
    size = len(matrix)
    for r in centers:
        for c in centers:
            # 跳过与定位图形重叠的位置
            if (r < 8 and c < 8) or (r < 8 and c > size - 9) or (r > size - 9 and c < 8):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    dark = max(abs(dr), abs(dc)) != 1
                    _set(matrix, reserved, r + dr, c + dc, 1 if dark else 0)


def _place_timing(matrix, reserved):
    size = len(matrix)
    for i in range(8, size - 8):
        val = 1 if i % 2 == 0 else 0
        _set(matrix, reserved, 6, i, val)
        _set(matrix, reserved, i, 6, val)


def _reserve_format(matrix, reserved):
    size = len(matrix)
    for i in range(9):
        for r, c in ((8, i), (i, 8)):
            if not reserved[r][c]:
                reserved[r][c] = True
    for i in range(8):
        reserved[8][size - 1 - i] = True
        reserved[size - 1 - i][8] = True
    _set(matrix, reserved, size - 8, 8, 1)   # 固定的深色模块


def _reserve_version(matrix, reserved, version):
    if version < 7:
        return
    size = len(matrix)
    for i in range(18):
        reserved[i // 3][i % 3 + size - 11] = True
        reserved[i % 3 + size - 11][i // 3] = True


def _place_data(matrix, reserved, bits):
    size = len(matrix)
    idx = 0
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1
        for i in range(size):
            row = size - 1 - i if upward else i
            for c in (col, col - 1):
                if not reserved[row][c]:
                    matrix[row][c] = bits[idx] if idx < len(bits) else 0
                    idx += 1
        upward = not upward
        col -= 2


def _apply_mask(matrix, reserved, mask_index):
    size = len(matrix)
    func = MASK_FUNCS[mask_index]
    out = [row[:] for row in matrix]
    for r in range(size):
        for c in range(size):
            if not reserved[r][c] and func(r, c):
                out[r][c] ^= 1
    return out


def _bch_format(data5: int) -> int:
    poly, val, rem = 0x537, data5 << 10, data5 << 10
    for i in range(14, 9, -1):
        if rem & (1 << i):
            rem ^= poly << (i - 10)
    return val | rem


def _bch_version(version: int) -> int:
    poly, val, rem = 0x1F25, version << 12, version << 12
    for i in range(17, 11, -1):
        if rem & (1 << i):
            rem ^= poly << (i - 12)
    return val | rem


def _place_format(matrix, mask_index, ec_bits=0b00):
    size = len(matrix)
    bits = _bch_format((ec_bits << 3) | mask_index) ^ 0x5412
    for i in range(15):
        bit = (bits >> i) & 1
        if i < 6:
            matrix[i][8] = bit
        elif i < 8:
            matrix[i + 1][8] = bit
        else:
            matrix[size - 15 + i][8] = bit

        if i < 8:
            matrix[8][size - 1 - i] = bit
        elif i < 9:
            matrix[8][15 - i] = bit
        else:
            matrix[8][14 - i] = bit
    matrix[size - 8][8] = 1


def _place_version(matrix, version):
    if version < 7:
        return
    size = len(matrix)
    bits = _bch_version(version)
    for i in range(18):
        bit = (bits >> i) & 1
        matrix[i // 3][i % 3 + size - 11] = bit
        matrix[i % 3 + size - 11][i // 3] = bit


# ---------------------------------------------------------------- 掩码打分

def _penalty(matrix) -> int:
    size = len(matrix)
    score = 0

    # 规则 1：同色连续
    for line in list(matrix) + [list(col) for col in zip(*matrix)]:
        run, prev = 1, line[0]
        for value in line[1:]:
            if value == prev:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run, prev = 1, value
        if run >= 5:
            score += 3 + (run - 5)

    # 规则 2：2x2 同色块
    for r in range(size - 1):
        for c in range(size - 1):
            if matrix[r][c] == matrix[r][c + 1] == matrix[r + 1][c] == matrix[r + 1][c + 1]:
                score += 3

    # 规则 3：1:1:3:1:1 图形
    pattern = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    reverse = pattern[::-1]
    for line in list(matrix) + [list(col) for col in zip(*matrix)]:
        for i in range(size - 10):
            window = line[i:i + 11]
            if window == pattern or window == reverse:
                score += 40

    # 规则 4：深色比例偏离 50%
    dark = sum(sum(row) for row in matrix)
    percent = dark * 100.0 / (size * size)
    score += 10 * int(abs(percent - 50) // 5)
    return score


# ---------------------------------------------------------------- 对外接口

def build_matrix(data: str, mask: int = None) -> list:
    """返回二维码的 0/1 矩阵（含静默区）。

    mask 传 0~7 可以强制使用某个掩码；不传则按罚分自动挑一个最优的。
    自己挑和别的实现挑的可能不同——这完全不影响扫码，因为解码方会从
    格式信息里读出用的是哪个掩码。
    """
    raw = data.encode("utf-8")
    version = _pick_version(len(raw))
    size = 17 + 4 * version

    matrix, reserved = _new_matrix(size)
    _place_finder(matrix, reserved, 0, 0)
    _place_finder(matrix, reserved, 0, size - 7)
    _place_finder(matrix, reserved, size - 7, 0)
    _place_alignment(matrix, reserved, version)
    _place_timing(matrix, reserved)
    _reserve_format(matrix, reserved)
    _reserve_version(matrix, reserved, version)

    bits = _final_bits(_interleave(_bit_stream(raw, version), version))
    _place_data(matrix, reserved, bits)

    best, best_score = None, None
    candidates = range(8) if mask is None else [mask % 8]
    for mask_index in candidates:
        candidate = _apply_mask(matrix, reserved, mask_index)
        _place_format(candidate, mask_index)
        _place_version(candidate, version)
        score = _penalty(candidate)
        if best_score is None or score < best_score:
            best, best_score = candidate, score

    border = 4
    full = [[0] * (size + border * 2) for _ in range(size + border * 2)]
    for r in range(size):
        for c in range(size):
            full[r + border][c + border] = best[r][c]
    return full


def render_svg(data: str, *, scale: int = 6, dark: str = "#141821",
               light: str = "#ffffff", radius: int = 0) -> str:
    """把二维码渲染成 SVG 字符串，可以直接塞进 HTML。"""
    matrix = build_matrix(data)
    size = len(matrix)
    px = size * scale

    parts = []
    for r, row in enumerate(matrix):
        for c, value in enumerate(row):
            if value:
                parts.append(
                    f'<rect x="{c * scale}" y="{r * scale}"'
                    f' width="{scale}" height="{scale}"/>'
                )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {px} {px}"'
        f' width="{px}" height="{px}" shape-rendering="crispEdges"'
        f' role="img" aria-label="二维码">'
        f'<rect width="{px}" height="{px}" fill="{html.escape(light)}"'
        + (f' rx="{radius}"' if radius else "")
        + f'/><g fill="{html.escape(dark)}">'
        + "".join(parts)
        + "</g></svg>"
    )


def render_data_uri(data: str, **kwargs) -> str:
    from urllib.parse import quote
    svg = render_svg(data, **kwargs)
    return "data:image/svg+xml;charset=utf-8," + quote(svg)


def render_pair_svg(items, *, scale: int = 6, gap: int = 56, margin: int = 26,
                    label_gap: int = 18, label_size: int = None,
                    dark: str = "#141821", light: str = "#ffffff") -> str:
    """把多个二维码并排画进同一张 SVG，每个下面带一行文字标签。

    items: [(数据, 标签), ...]，通常是学生端和教师端两个地址。
    这样"下载二维码"一个文件就能把两个都带走，直接打印也行。
    """
    blocks = []
    for data, label in items:
        matrix = build_matrix(data)
        size = len(matrix)
        px = size * scale
        rects = "".join(
            f'<rect x="{c * scale}" y="{r * scale}"'
            f' width="{scale}" height="{scale}"/>'
            for r, row in enumerate(matrix)
            for c, value in enumerate(row) if value
        )
        blocks.append((px, rects, label))

    # 两个二维码的模块数通常不一样（地址越长版本越高），
    # 统一外框、各自缩放到同一个尺寸，看起来才一样大。
    qr_w = max(b[0] for b in blocks)
    font = label_size or max(18, int(qr_w * 0.12))
    label_h = font + 6
    width = margin * 2 + qr_w * len(blocks) + gap * (len(blocks) - 1)
    height = margin * 2 + qr_w + label_gap + label_h

    font_family = ('-apple-system, "PingFang SC", "Microsoft YaHei", '
                   '"Segoe UI", sans-serif')
    parts = []
    for i, (px, rects, label) in enumerate(blocks):
        x = margin + i * (qr_w + gap)
        y = margin
        # 每个码都塞进同样大的外框：地址长度不同会导致二维码版本不同，
        # 这里统一缩放到同一尺寸（矢量缩放，打印也不糊）。
        parts.append(
            f'<svg x="{x:g}" y="{y:g}" width="{qr_w}" height="{qr_w}"'
            f' viewBox="0 0 {px} {px}">'
            f'<g fill="{html.escape(dark)}">{rects}</g></svg>')
        parts.append(
            f'<text x="{x + qr_w / 2:g}" y="{y + qr_w + label_gap + font:g}"'
            f' text-anchor="middle" font-size="{font}" fill="#12161f"'
            f' font-family="{html.escape(font_family, quote=True)}">'
            f'{html.escape(label)}</text>'
        )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}"'
        f' width="{width}" height="{height}" shape-rendering="crispEdges"'
        f' role="img"'
        f' aria-label="二维码">'
        f'<rect width="{width}" height="{height}" fill="{html.escape(light)}"/>'
        + "".join(parts)
        + "</svg>"
    )
