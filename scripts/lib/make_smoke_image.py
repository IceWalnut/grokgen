"""生成冒烟测试用的首帧图。

**为什么现造而不是往仓库里放一张 PNG**：这个仓库目前一个二进制文件都没有，
保持这样。图是确定性生成的，每次内容完全一致，不会产生 diff 噪音。

**为什么是 1280x720**：网关不指定宽高时按图片比例推画布，算法是把总像素
压到 736*416=306176 这个预算内并贴到 32 的倍数。
1280x720 推出来正好是 **736x416** —— 与 M1R3 的基线逐项对齐，
所以冒烟报告里的帧数、时长、分辨率可以直接和历史读数并排看。

**为什么画成高饱和几何图形**：首帧是否生效只能靠人眼判定（VS-8）。
M1R4 的经验是画风必须「不可能含糊」—— 用一张写实照片做首帧，
人眼判定会退化成「好像是吧」。这里用不对称的色块加对角条纹，一眼认得出。

用法：
    python3 scripts/lib/make_smoke_image.py <输出路径>
"""

import struct
import sys
import zlib

WIDTH = 1280
HEIGHT = 720


def pixel(x: int, y: int) -> tuple[int, int, int]:
    """算出一个像素的颜色。

    图案是刻意不对称的：左上角一个品红方块、右下角一个青色方块、
    背景是黄黑对角条纹。不对称是为了让人眼能判断首帧有没有被翻转或错位。

    Args:
        x: 横坐标，像素。
        y: 纵坐标，像素。

    Returns:
        `(r, g, b)`，每个分量 0–255。
    """
    # 左上角的品红方块 —— 位置不对称，用来判断画面有没有被镜像。
    if x < WIDTH // 4 and y < HEIGHT // 4:
        return (255, 0, 200)
    # 右下角的青色方块。
    if x > WIDTH * 3 // 4 and y > HEIGHT * 3 // 4:
        return (0, 220, 255)
    # 背景：黄黑对角条纹，条纹方向本身也是一条方位信息。
    return (255, 220, 0) if ((x + y) // 60) % 2 == 0 else (20, 20, 20)


def png_bytes() -> bytes:
    """把图案编码成一个最小但合法的 PNG。

    流程说明：
        1. 逐行拼出原始像素，每行前面加一个 0 表示「不用滤波」；
        2. zlib 压缩；
        3. 按 PNG 规范拼 IHDR / IDAT / IEND 三个块，每块带 CRC32。

    只用标准库，不依赖 Pillow —— 开发机上不一定有。

    Returns:
        完整的 PNG 文件字节。
    """
    raw = bytearray()
    for y in range(HEIGHT):
        raw.append(0)  # 滤波类型 0 = None
        for x in range(WIDTH):
            raw.extend(pixel(x, y))

    def chunk(tag: bytes, data: bytes) -> bytes:
        """拼一个带长度和 CRC 的 PNG 块。"""
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    # 位深 8、颜色类型 2（真彩色）、其余三项按规范固定为 0。
    ihdr = struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def main() -> int:
    """把图写到命令行给的路径。

    Returns:
        进程退出码。
    """
    if len(sys.argv) != 2:
        print(f"用法：{sys.argv[0]} <输出路径>", file=sys.stderr)
        return 2
    with open(sys.argv[1], "wb") as f:
        f.write(png_bytes())
    print(f"{WIDTH}x{HEIGHT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
