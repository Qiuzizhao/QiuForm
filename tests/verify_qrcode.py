#!/usr/bin/env python3
"""对照参考实现逐模块校验 qiuform.qrcode_gen。

需要装了 qrcode 包（只用于验证，不是运行时依赖）：
    pip install qrcode --target <某个临时目录>
    set PYTHONPATH=<那个目录>
    python tests/verify_qrcode.py

如果环境里没有 qrcode，脚本会直接跳过并说明原因。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qiuform import qrcode_gen  # noqa: E402

try:
    import qrcode  # type: ignore
    from qrcode.constants import ERROR_CORRECT_M  # type: ignore
except ImportError:
    print("跳过：未安装 qrcode 参考实现")
    raise SystemExit(0)


PAYLOADS = [
    "https://example.com/p/abc123def456/",
    "https://quickform.local/p/k3m9x2qp7w1z/",
    "http://192.168.31.53:8000/p/a1b2c3d4e5f6/",
    "https://example.cn/p/zz99yy88xx77/?from=poster",
    "短链接 https://ex.cn/p/abc/",
    "x",
    "https://example.com/api/abcdefghijkl",
    "https://a-very-long-domain-name-for-testing.example.org/p/"
    "abcdefghijklmnopqrstuvwxyz0123456789/",
    "https://example.com/p/mmmmmmmmmmmm/",
    "https://example.com/p/nnnnnnnnnnnn/",
    # 下面几条用来覆盖版本 7 以上——那些版本会额外写入"版本信息"区块
    "https://example.com/p/abcdefghijkl/?ref=poster&utm_source=classroom"
    "&utm_medium=qr&utm_campaign=2026-spring",
    "https://a-really-quite-long-hostname-for-testing-qr.example.com/"
    "p/abcdefghijklmnopqrstuvwxyz/?" + "k=v&" * 8 + "end=1",
    "https://example.com/p/" + "z" * 150 + "/",
]


def reference_matrix(data: str):
    qr = qrcode.QRCode(
        version=None, error_correction=ERROR_CORRECT_M, box_size=1, border=0
    )
    qr.add_data(data, optimize=0)   # 关掉模式优化，强制走字节模式
    qr.make(fit=True)
    return [[1 if cell else 0 for cell in row] for row in qr.modules]


def main() -> int:
    passed = failed = 0
    for payload in PAYLOADS:
        ref = reference_matrix(payload)

        # 逐个掩码比对：掩码选择属于实现自由，编码正确性要看"是否存在
        # 某个掩码能让两者完全一致"。
        matched_mask = None
        size_mismatch = False
        too_long = False
        for mask in range(8):
            try:
                mine = qrcode_gen.build_matrix(payload, mask=mask)
            except ValueError:
                too_long = True
                break
            inner = [row[4:-4] for row in mine[4:-4]]
            if len(inner) != len(ref):
                size_mismatch = True
                break
            if all(inner[r][c] == ref[r][c]
                   for r in range(len(ref)) for c in range(len(ref))):
                matched_mask = mask
                break

        if too_long:
            print(f"[SKIP] 超出支持范围（本实现到版本 10）  {payload[:36]}")
            continue

        if size_mismatch:
            print(f"[FAIL] 尺寸不一致   {payload[:44]}")
            failed += 1
            continue

        if matched_mask is not None:
            size = len(ref)
            version = (size - 17) // 4
            print(f"[PASS] {size}x{size} 版本{version} 掩码{matched_mask}  "
                  f"{payload[:40]}")
            passed += 1
        else:
            print(f"[FAIL] 8 个掩码都对不上   {payload[:44]}")
            failed += 1

    print(f"\n{passed} 项一致，{failed} 项不一致")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
