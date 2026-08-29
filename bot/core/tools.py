# -*- coding: utf-8 -*-
"""通用工具层。

为「未来功能」预留的通用能力：
    1. 调用本地脚本/命令（如下载视频、跑某个工具脚本）-> run_script
    2. 请求 HTTP 接口（如调用某个 API 拿数据）        -> http_get / http_post
    3. 预留 AI / 文件处理等能力的挂载点

今后新功能里需要「让另一个脚本干活」时，直接 import tools 调用即可。
"""

import asyncio


async def run_script(command, timeout=300):
    """在子进程中运行一段命令/脚本，返回 (stdout, stderr, returncode)。

    Args:
        command: 要执行的命令字符串，例如 'yt-dlp -o out.mp4 "https://..."'
                或 'python D:/xxx/downloader.py "arg1" "arg2"'
        timeout: 最大等待秒数，超时则杀掉进程。

    Example:
        out, err, code = await tools.run_script('python script.py')
    """
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(f"命令执行超时（>{timeout}s）: {command}")

    def _decode(b):
        if not b:
            return ""
        for enc in ("utf-8", "gbk"):
            try:
                return b.decode(enc)
            except Exception:
                continue
        return b.decode("utf-8", errors="replace")

    return _decode(stdout), _decode(stderr), proc.returncode


async def http_get(url, timeout=20, headers=None):
    """GET 一个 HTTP 接口，自动把 JSON 或文本返回。"""
    import aiohttp

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            text = await resp.text()
            try:
                return resp.status, resp.json()
            except Exception:
                return resp.status, text


async def http_post(url, json_body=None, headers=None, timeout=20):
    """POST 一个 JSON body 到 HTTP 接口，返回 (status, data)。"""
    import aiohttp

    async with aiohttp.ClientSession() as session:
        async with session.post(
            url, json=json_body, headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            text = await resp.text()
            try:
                return resp.status, resp.json()
            except Exception:
                return resp.status, text


# 预留：AI 调用 / 文件处理等都可在下面继续扩展