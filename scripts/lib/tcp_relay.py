"""一个最小的 TCP 转发：把本机一个端口的连接原样转到另一个地址（M2R4b，VS-24 的断线实验）。

用途：让一个临时网关实例的 ws 经过它去连 ComfyUI，然后**杀掉它** ——
ws 断了，而 ComfyUI 本身和那个任务毫发无损。

    python3 tcp_relay.py --listen 18188 --target 127.0.0.1:8188 --pid-file /tmp/grokgen_ws_relay.pid

被 SIGTERM 时进程直接退出，操作系统关掉它持有的全部连接 —— 对网关而言就是连接突然断开。
"""

import argparse
import asyncio
import os


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """把一个方向的字节原样搬过去，任一端断开就关掉另一端。"""
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--listen", type=int, required=True, help="在 127.0.0.1 上监听的端口")
    parser.add_argument("--target", required=True, help="转发目标 host:port")
    parser.add_argument("--pid-file", required=True, help="把自己的 PID 写到这里，便于精确地杀（不用 pkill -f，见 ContextPack §4.002）")
    args = parser.parse_args()
    target_host, target_port = args.target.rsplit(":", 1)

    async def handle(client_reader, client_writer):
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(target_host, int(target_port)), timeout=5
        )
        await asyncio.gather(pipe(client_reader, upstream_writer), pipe(upstream_reader, client_writer))

    server = await asyncio.start_server(handle, "127.0.0.1", args.listen)
    with open(args.pid_file, "w") as f:
        f.write(str(os.getpid()))
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
