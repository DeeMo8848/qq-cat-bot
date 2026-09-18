# -*- coding: utf-8 -*-
"""BBDown 扫码登录支持。

BBDown 自带两条登录命令：
    login     -> 通过 APP 扫码登录 WEB 账号，登录态写入 BBDown.data
    logintv   -> 通过 APP 扫码登录 TV  账号，登录态写入 BBDownTV.data

关键事实（均已实测确认，见 exe 字符串表与实跑验证）：
1. 两条命令都会在 **当前工作目录** 直接生成 `qrcode.png`，无需自行合成二维码。
   两张图都能被正常解码（WEB 455x455 / TV 287x287）。
2. 两条命令写的是 **同一个文件名** `qrcode.png`，且 BBDown 从 exe 同目录读取
   `.data` 登录态 —— 因此必须把每次登录放到「exe 同目录」之外无法工作，
   正确的做法是给每次登录会话一个独立的临时工作目录，并把 exe 与已有登录态
   复制进去，登录成功后再把新的 `.data` 回收写入 bot 正式目录。
3. 进程会一直阻塞等待扫码，必须靠读取 stdout 判断进度：
       "生成二维码成功: qrcode.png"  -> 二维码已就绪，可以取图
       "登录成功: SESSDATA" / "AccessToken=" -> 登录完成
   注意进程只有在扫码确认后才退出；超时/中断时我们要主动 kill。
"""

import asyncio
import logging
import os
import re
import shutil
import tempfile
import time

_log = logging.getLogger("bili.login")

# 一次登录会话的最长存活时间（秒）。B站二维码有效期约 180 秒，这里留足余量。
SESSION_TTL = 300

# 登录相关标记串（来自 BBDown 1.6.3 的字符串表）
MARK_QR_READY = "生成二维码成功"
MARK_LOGIN_OK_WEB = "登录成功"
MARK_LOGIN_OK_TV = "AccessToken="
MARK_NOT_LOGIN = "你尚未登录B站账号"
MARK_NO_FFMPEG = "找不到可执行的ffmpeg文件"

# 每种登录方式对应的产出文件名
_CRED_FILES = {
    "web": "BBDown.data",
    "tv": "BBDownTV.data",
}

# 会话表：key = 会话 id，value = _Session
_SESSIONS: dict[str, "_Session"] = {}


class LoginResult:
    """一次登录尝试的结果。"""

    def __init__(self, ok, message, qrcode_path=None, session_id=None):
        self.ok = ok
        self.message = message
        self.qrcode_path = qrcode_path
        self.session_id = session_id

    def __repr__(self):
        return f"<LoginResult ok={self.ok} msg={self.message!r} qr={self.qrcode_path!r}>"


class _Session:
    """一次进行中的扫码登录会话。"""

    def __init__(self, kind, proc, workdir, qrcode_path, task):
        self.kind = kind            # "web" / "tv"
        self.proc = proc
        self.workdir = workdir
        self.qrcode_path = qrcode_path
        self.task = task            # 后台等待任务：完成时负责回收登录态并回调通知
        self.created = time.time()
        self.done = False

    def expired(self):
        return time.time() - self.created > SESSION_TTL


def _decode(b: bytes) -> str:
    """BBDown 输出为 GBK；优先按 GBK 解，避免 UTF-8 抢先解出乱码。"""
    if not b:
        return ""
    try:
        return b.decode("gbk")
    except Exception:
        return b.decode("utf-8", errors="replace")


async def _read_until(proc, marks, timeout):
    """边读 proc.stdout 边匹配标记串，返回 (命中标记, 已读全部文本)。

    命中返回；超时返回 (None, 文本)。用于「等二维码就绪」与「等登录成功」。
    """
    buf = []
    deadline = time.time() + timeout
    found = None
    while time.time() < deadline:
        if proc.returncode is not None and proc.stdout.at_eof():
            break
        try:
            chunk = await asyncio.wait_for(proc.stdout.read(4096), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        except Exception:
            break
        if not chunk:
            if proc.returncode is not None:
                break
            continue
        text = _decode(chunk)
        buf.append(text)
        joined = "".join(buf)
        for m in marks:
            if m in joined:
                found = m
                return found, joined
    return found, "".join(buf)


async def _drain(proc):
    """把进程剩余输出读完（避免管道写满导致子进程卡死）。"""
    try:
        while True:
            chunk = await asyncio.wait_for(proc.stdout.read(8192), timeout=2.0)
            if not chunk:
                break
    except Exception:
        pass


def _kill(proc):
    """安全终止进程。"""
    try:
        if proc.returncode is None:
            proc.kill()
    except Exception:
        pass


async def _watch_login(session, on_success, on_fail):
    """后台任务：等扫码完成 -> 回收新登录态 -> 回调通知用户。

    BBDown 登录成功后会把 cookie 写到工作目录的 `.data` 文件，但那个目录是临时的，
    必须把新文件复制回正式的 BBDown 目录，否则重启后又变回未登录。
    """
    proc = session.proc
    try:
        hit, text = await _read_until(
            proc, [MARK_LOGIN_OK_WEB, MARK_LOGIN_OK_TV], timeout=SESSION_TTL
        )
        if not hit:
            # 没等到成功标记：可能是超时或被中断
            await _drain(proc)
            _kill(proc)
            if not session.done:
                session.done = True
                await on_fail(session, "二维码已过期或登录被中断，请重新发送登录命令喵")
            return

        # 等进程自然退出（它会自己写盘并打印收尾信息）
        try:
            await asyncio.wait_for(proc.wait(), timeout=20)
        except asyncio.TimeoutError:
            _kill(proc)
        await _drain(proc)

        # 回收登录态
        saved = _harvest(session.kind, session.workdir)
        session.done = True
        if saved:
            await on_success(session, saved)
        else:
            await on_fail(session, "登录已完成，但没能读取到登录凭证，请重试")
    except asyncio.CancelledError:
        _kill(proc)
        raise
    except Exception as e:
        _log.warning("等待 BBDown 登录结果出错: %s", e)
        _kill(proc)
        if not session.done:
            session.done = True
            await on_fail(session, f"登录过程出错：{e}")
    finally:
        _cleanup_workdir(session.workdir)


def _looks_like_cred(kind, blob: bytes) -> bool:
    """粗校验：判断这份产物**明显不是**有效登录凭证时返回 False。

    ★ 设计取向：**宁可放行，不可误拦**。
      这里的唯一目的是挡住空文件 / 明显是占位或测试数据的垃圾
      （开发期真发生过：测试用的假 cookie 覆盖了用户真实登录态）。
      但 BBDown 各版本写盘格式并不完全一致（字段名、分隔符、值长度都可能变），
      所以**绝不能**用「字段格式必须精确匹配」来判定——
      否则会把用户真实扫码得到的凭证拒掉，表现为
      「登录已完成，但没能读取到登录凭证」这种最糟糕的失败模式。

    因此判据只保留两条最稳的：
      - 内容长度足够（真实凭证至少几十字节）
      - 不是明确的白名单垃圾串
    仅在「字段完全缺失且内容极短」时才拒绝。
    """
    if not blob:
        return False
    # 明确的占位/测试数据
    lowered = blob.lower()
    for bad in (b"faketoken", b"for_test", b"dummy", b"placeholder"):
        if bad in lowered:
            return False
    # 长度下限：真实 WEB 凭证 ~400B、TV ~45B，给个宽松的下限
    if len(blob) < 24:
        return False
    # 有 credential 特征字段就放行（容忍大小写与不同字段名）
    for field in (b"sessdata", b"access_token", b"dedeuserid", b"cookie"):
        if field in lowered:
            return True
    # 没有识别到已知字段：内容又不短，仍然放行（格式未知但大概率是有效产物）
    return len(blob) >= 40


def _cred_kind_hint(kind, blob: bytes) -> str:
    """返回校验结论的辅助描述，仅用于日志，不参与放行决策。"""
    text = blob.decode("utf-8", errors="replace").lower()
    if kind == "web" and "sessdata" not in text:
        return "（未发现 SESSDATA 字段，WEB 登录可能不完整）"
    if kind == "tv" and "access_token" not in text:
        return "（未发现 access_token 字段，TV 登录可能不完整）"
    return ""


def _harvest(kind, workdir):
    """把登录成功产生的 .data 文件复制回正式 BBDown 目录。返回目标路径或 None。

    ★ 安全约束：只回收**不是垃圾**的产物（空文件、明显占位/测试数据会被拒）。
      开发期真发生过：测试用的假 cookie 覆盖了用户的真实登录态。
      但校验刻意保持宽松——宁可放行可疑内容，也不能拒掉用户真实扫码拿到的凭证，
      否则会表现为「登录已完成，但没能读取到登录凭证」，比不校验更糟。

    同时回写用户的「源」BBDown 目录（BBDOWN_COOKIE_SRC），让两边登录态一致：
    否则下次 _sync_bbdown_cookie() 会因为源目录 mtime 更旧而被判定为「无需同步」，
    或者反过来用旧 cookie 覆盖新登录的。
    """
    from config import BBDOWN_DIR, BBDOWN_COOKIE_SRC

    name = _CRED_FILES.get(kind)
    if not name or not workdir:
        return None
    src = os.path.join(workdir, name)
    if not os.path.isfile(src):
        # 没生成就明确说来，方便定位
        try:
            present = [f for f in os.listdir(workdir) if f.endswith(".data")]
        except Exception:
            present = []
        _log.warning("登录产物 %s 不存在于 %s（目录内 .data: %s）", name, workdir, present)
        return None
    if os.path.getsize(src) == 0:
        _log.warning("登录产物为空文件，拒绝写回: %s", src)
        return None
    try:
        blob = open(src, "rb").read()
    except Exception as e:
        _log.warning("读取登录产物失败 %s: %s", src, e)
        return None
    if not _looks_like_cred(kind, blob):
        _log.warning("登录产物校验不通过（疑似占位/测试数据），拒绝写回: %s", src)
        return None
    hint = _cred_kind_hint(kind, blob)
    if hint:
        _log.warning("登录产物已保存，但%s: %s", hint, src)

    targets = []
    if BBDOWN_DIR:
        targets.append(os.path.join(BBDOWN_DIR, name))
    if BBDOWN_COOKIE_SRC and os.path.abspath(BBDOWN_COOKIE_SRC) != os.path.abspath(BBDOWN_DIR or ""):
        targets.append(os.path.join(BBDOWN_COOKIE_SRC, name))

    saved = None
    for dst in targets:
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            _log.info("BBDown %s 登录态已保存: %s", kind, dst)
            saved = saved or dst
        except Exception as e:
            _log.warning("保存 %s 登录态到 %s 失败: %s", kind, dst, e)
    return saved


def _cleanup_workdir(workdir):
    if workdir:
        shutil.rmtree(workdir, ignore_errors=True)


def _purge_old_qr(stable_dir, keep_seconds=1800):
    """清理过期的二维码临时图片，避免长期运行堆积。"""
    try:
        now = time.time()
        for name in os.listdir(stable_dir):
            if not name.startswith("qr_") or not name.endswith(".png"):
                continue
            p = os.path.join(stable_dir, name)
            try:
                if now - os.path.getmtime(p) > keep_seconds:
                    os.remove(p)
            except Exception:
                pass
    except Exception:
        pass


def _prepare_workdir(qrcode_name="qrcode.png"):
    """建立独立的登录临时目录，放入 exe 与已有登录态。

    BBDown 从 exe 同目录读 `.data`；把所有东西放临时目录可以让登录互不干扰，
    也避免把 qrcode.png 落到项目里有价值的目录。
    """
    from config import BBDOWN_EXE, BBDOWN_DIR, FFMPEG_EXE

    _purge_stale_workdirs()          # 顺手清掉历史遗留（每份含 18MB exe，不清理会堆积）
    workdir = tempfile.mkdtemp(prefix="bbdown_login_")
    exe_name = os.path.basename(BBDOWN_EXE)
    dst_exe = os.path.join(workdir, exe_name)
    shutil.copy2(BBDOWN_EXE, dst_exe)

    # 带上已有登录态（TV 登录也带上 WEB 的，行为与手工使用一致）
    if BBDOWN_DIR and os.path.isdir(BBDOWN_DIR):
        for name in _CRED_FILES.values():
            src = os.path.join(BBDOWN_DIR, name)
            if os.path.isfile(src):
                try:
                    shutil.copy2(src, os.path.join(workdir, name))
                except Exception:
                    pass
    return workdir, dst_exe


def _purge_stale_workdirs(keep_seconds=3600):
    """清理遗留的登录临时目录。

    每个目录里都有一份 18MB 的 BBDown.exe；如果 bot 在等待扫码期间重启，
    `_watch_login` 的 finally 就不会执行，目录会永久残留（实测攒了 9 个 ≈160MB）。
    因此在每次新建会话前顺手清一遍旧的。
    """
    try:
        tmp = tempfile.gettempdir()
        now = time.time()
        for name in os.listdir(tmp):
            if not name.startswith("bbdown_login_"):
                continue
            p = os.path.join(tmp, name)
            if not os.path.isdir(p):
                continue
            try:
                if now - os.path.getmtime(p) > keep_seconds:
                    shutil.rmtree(p, ignore_errors=True)
            except Exception:
                pass
    except Exception:
        pass


async def is_logged_in(kind="web", timeout=60):
    """探测当前 BBDown 是否已登录。

    判断依据取 BBDown 自己打印的日志行：
        已登录 -> "检测账号登录..." 后跟 "加载本地cookie..."
        未登录 -> "你尚未登录B站账号, 解析可能受到限制"
    注意：未登录时进程返回码仍为 0、视频标题也照样能解析出来，
    所以**不能**用返回码或标题是否存在来判断，必须匹配这行标记串。
    """
    from config import BBDOWN_EXE, BBDOWN_DIR, FFMPEG_EXE

    if not os.path.isfile(BBDOWN_EXE):
        return False, "未找到 BBDown 可执行文件"

    # 用 --only-show-info 触发一次「检测账号登录」，随便取一个知名 BV
    probe_bv = "BV1GJ411x7h7"
    cmd = [BBDOWN_EXE, probe_bv, "--only-show-info", "--ffmpeg-path", FFMPEG_EXE]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=BBDOWN_DIR or None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except Exception as e:
        return False, f"无法启动 BBDown：{e}"

    try:
        text = ""
        deadline = time.time() + timeout
        buf = []
        while time.time() < deadline:
            try:
                chunk = await asyncio.wait_for(proc.stdout.read(4096), timeout=1.0)
            except asyncio.TimeoutError:
                if proc.returncode is not None:
                    break
                continue
            if not chunk:
                break
            buf.append(_decode(chunk))
            text = "".join(buf)
            # 两类结果标记任一出现即可下结论，不必等进程跑完
            if MARK_NOT_LOGIN in text:
                _kill(proc)
                return False, "未登录"
            if "加载本地cookie" in text:
                _kill(proc)
                return True, "已登录"
        text = "".join(buf)
        if proc.returncode is not None:
            await proc.wait()
        if MARK_NOT_LOGIN in text:
            return False, "未登录"
        if "加载本地cookie" in text:
            return True, "已登录"
        if MARK_NO_FFMPEG in text:
            return False, "BBDown 找不到 ffmpeg（请检查 --ffmpeg-path 配置）"
        return False, "无法确认登录状态"
    finally:
        _kill(proc)
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except Exception:
            pass


async def start_login(kind, on_success, on_fail, wait_qr=60):
    """发起一次扫码登录，返回 LoginResult。

    Args:
        kind: "web" -> login（B站网页账号）; "tv" -> logintv（B站TV账号）
        on_success(session, cred_path) / on_fail(session, msg): 后台回调，用于给用户发通知
        wait_qr: 等待「二维码生成成功」的最长秒数
    """
    from config import BBDOWN_EXE

    if not os.path.isfile(BBDOWN_EXE):
        return LoginResult(False, "未找到 BBDown 可执行文件，请先在后台配置 BBDOWN_EXE")

    # 同一时间只保留一种登录会话，避免两个进程抢写同名 qrcode.png
    for sid in list(_SESSIONS):
        s = _SESSIONS[sid]
        if s.expired():
            _kill(s.proc)
            _cleanup_workdir(s.workdir)
            _SESSIONS.pop(sid, None)
    if _SESSIONS:
        return LoginResult(False, "已经有一个登录流程在进行中喵，请先完成或稍等一会儿再试")

    workdir, exe_path = _prepare_workdir()
    subcmd = "login" if kind == "web" else "logintv"

    try:
        proc = await asyncio.create_subprocess_exec(
            exe_path, subcmd,
            cwd=workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except Exception as e:
        _cleanup_workdir(workdir)
        return LoginResult(False, f"启动 BBDown 登录失败：{e}")

    # 等二维码生成
    hit, text = await _read_until(proc, [MARK_QR_READY], timeout=wait_qr)
    if not hit:
        _kill(proc)
        await _drain(proc)
        _cleanup_workdir(workdir)
        if MARK_NO_FFMPEG in text:
            return LoginResult(False, "BBDown 找不到 ffmpeg，请检查配置")
        tail = text.strip().splitlines()[-1:] or [""]
        return LoginResult(False, f"生成二维码失败：{tail[0][:120] or '未知错误'}")

    # 二维码是异步写盘的，稍微等它落盘
    qr_path = os.path.join(workdir, "qrcode.png")
    for _ in range(30):
        if os.path.isfile(qr_path) and os.path.getsize(qr_path) > 0:
            break
        await asyncio.sleep(0.1)
    if not os.path.isfile(qr_path) or os.path.getsize(qr_path) == 0:
        _kill(proc)
        await _drain(proc)
        _cleanup_workdir(workdir)
        return LoginResult(False, "二维码图片没有生成，请重试")

    # 把二维码复制到一个稳定路径后交给调用方发送（原目录会被清理）
    stable_dir = os.path.join(tempfile.gettempdir(), "bbdown_qr")
    os.makedirs(stable_dir, exist_ok=True)
    _purge_old_qr(stable_dir)
    stable = os.path.join(stable_dir, f"qr_{kind}_{int(time.time())}.png")
    shutil.copy2(qr_path, stable)

    session_id = f"{kind}_{int(time.time() * 1000)}"
    task = asyncio.create_task(_watch_login(
        _Session(kind, proc, workdir, stable, None), on_success, on_fail
    ))
    session = _Session(kind, proc, workdir, stable, task)
    _SESSIONS[session_id] = session
    session.session_id = session_id

    label = "B站网页账号" if kind == "web" else "B站TV账号"
    return LoginResult(
        True,
        f"请使用哔哩哔哩手机APP扫描二维码登录{label}喵（约 3 分钟内有效）",
        qrcode_path=stable,
        session_id=session_id,
    )


def get_session(session_id):
    return _SESSIONS.get(session_id)


def drop_session(session_id):
    """移除会话记录（会话结束后调用，防止无限增长）。"""
    _SESSIONS.pop(session_id, None)
