# -*- coding: utf-8 -*-
"""海龟汤（turtlesoup）推理互动游戏。

移植自 astrbot 插件 astrbot_plugin_turtlesoup：本地 42 题题库，无外部 API。
答案判定使用关键词简判（不依赖外部 LLM），保持功能可用、无脆弱依赖。

命令：
  开始海龟汤 [题号] / 题库列表 [页] / 题目详情 题号
  海龟汤提问 你的问题 / 公布答案 / 换一题 / 结束海龟汤 / 强制结束海龟汤
  海龟汤帮助

多轮会话由 dispatch 顶层的 consume 拦截处理（每 openid 独立，限同会话/同目标）。
"""

import os
import random
import time

from config import _cfg
from bot.commands import register, ROLE_ALL

# 供 Web 后台「游戏娱乐 → 海龟汤」插件总开关使用的命令名集合
TURTLE_CMD_NAMES = {
    "cmd_turtle_start", "cmd_turtle_question", "cmd_turtle_list",
    "cmd_turtle_detail", "cmd_turtle_help", "cmd_turtle_ctrl",
}

_SESSION_TIMEOUT = int(_cfg("TURTLE_SESSION_TIMEOUT", 600))
_MAX_QUESTIONS = int(_cfg("TURTLE_MAX_QUESTIONS", 20))

_DB = os.path.join(os.path.dirname(__file__), "turtlesoup_data", "questions_database.txt")

# openid -> {question, answer, metadata, question_count, scene, target, expires}
SESSIONS = {}

_STOP_WORDS = {"的", "了", "是", "在", "和", "与", "或", "但", "然后", "因为",
               "所以", "这", "那", "一个", "就", "也", "都"}


def _load_bank():
    """解析题库文件，返回 [(汤面, 汤底, metadata)]。"""
    questions = []
    try:
        with open(_DB, encoding="utf-8") as f:
            content = f.read().strip()
    except FileNotFoundError:
        return []
    except Exception:
        return []

    for block in content.split("---"):
        block = block.strip()
        if not block or block.startswith("#"):
            continue
        info = {}
        for line in block.splitlines():
            line = line.strip()
            if ":" in line and not line.startswith("#"):
                k, _, v = line.partition(":")
                info[k.strip()] = v.strip()
        if "ID" in info and "汤面" in info and "汤底" in info:
            meta = {
                "id": info.get("ID", ""),
                "title": info.get("标题", ""),
                "difficulty": _to_int(info.get("难度", "3"), 3),
                "tags": [t.strip() for t in info.get("标签", "").split(",") if t.strip()],
            }
            questions.append((info["汤面"], info["汤底"], meta))
    return questions


def _to_int(v, default):
    try:
        return int(v)
    except Exception:
        return default


_BANK = _load_bank()


def _get_question(question_id=None):
    if not _BANK:
        return None, None, {}
    if question_id:
        zid = str(question_id).zfill(3)
        for q, a, m in _BANK:
            if m.get("id") == zid:
                return q, a, m
        return None, None, {}
    q, a, m = random.choice(_BANK)
    return q, a, m


# ---------- 简易判定（无 LLM） ----------
def _simple_judge(question, answer):
    """关键词重叠：答案非停用词字符在问题里出现即算“是”，否则“否”。"""
    for ch in answer:
        if ch in _STOP_WORDS:
            continue
        if ch in question:
            return "是"
    return "否"


def _is_answer_correct(guess, answer):
    """简化答案匹配：答案关键词覆盖率 >= 50% 视为答对。"""
    answer_chars = {c for c in answer if c not in _STOP_WORDS and len(c) >= 1}
    if not answer_chars:
        return guess.strip() == answer.strip()
    hit = sum(1 for c in answer_chars if c in guess)
    return hit / len(answer_chars) >= 0.5


# ---------- 消息构造 ----------
def _intro_text(q, a, m):
    stars = "⭐" * int(m.get("difficulty", 3))
    return ("📖 谜题 #%s%s%s\n\n%s\n\n"
            "请用 `海龟汤提问 你的问题` 开始推理喵\n"
            "剩余提问次数：%d" % (m.get("id", ""),
                                  (" - " + m["title"]) if m.get("title") else "",
                                  (" " + stars) if stars else "",
                                  q, _MAX_QUESTIONS))


def _round_text(question, q_count, ai_answer, remaining):
    return ("💭 第 %d 问\n❓ %s\n💡 %s\n📊 剩余: %d 次"
            % (q_count, question, ai_answer, remaining))


def _end_text(title, q, a, m, q_count=None):
    text = title + "\n\n完整答案：\n" + a + "\n"
    if m.get("tags"):
        text += "🏷️ 标签: %s\n" % ", ".join(m["tags"])
    if q_count is not None:
        text += "共提问 %d 次。\n" % q_count
    text += "用 `开始海龟汤` 可挑战新题目喵~"
    return text


# ---------- 命令入口 ----------
@register(keywords=["开始海龟汤"], help="海龟汤推理小游戏喵", role=ROLE_ALL)
async def cmd_turtle_start(ctx):
    s = SESSIONS.get(ctx.openid)
    if s and time.time() <= s["expires"] and s["scene"] == ctx.scene and s["target"] == ctx.target:
        await ctx.reply("你已有一个进行中的海龟汤游戏喵，先发 `结束海龟汤` 结束它~")
        return

    parts = ctx.args.split() if ctx.args else []
    qid = None
    if len(parts) > 0:
        raw = parts[0].replace("开始海龟汤", "").strip()
        if raw.isdigit():
            qid = raw
        else:
            guess = _get_question(raw)
            if not guess[0]:
                await ctx.reply("题号格式应为数字喵，例如 `开始海龟汤 1`")
                return

    q, a, m = _get_question(qid)
    if not q:
        await ctx.reply("题库为空，无法开始游戏喵~")
        return

    SESSIONS[ctx.openid] = {
        "question": q, "answer": a, "metadata": m,
        "question_count": 0, "scene": ctx.scene, "target": ctx.target,
        "expires": time.time() + _SESSION_TIMEOUT,
    }
    await ctx.reply(_intro_text(q, a, m))


@register(keywords=["海龟汤提问"], help="海龟汤中提问喵", role=ROLE_ALL)
async def cmd_turtle_question(ctx):
    s = SESSIONS.get(ctx.openid)
    if not s or time.time() > s["expires"] or s["scene"] != ctx.scene or s["target"] != ctx.target:
        await ctx.reply("❌ 没有正在进行的游戏，先用 `开始海龟汤` 开始喵")
        return
    await _handle_turn(ctx, s, _strip_prefix(ctx.args or "", "海龟汤提问"))


@register(keywords=["题库列表"], help="查看海龟汤题库喵", role=ROLE_ALL)
async def cmd_turtle_list(ctx):
    if not _BANK:
        await ctx.reply("题库为空喵~")
        return
    page = 1
    parts = ctx.args.split() if ctx.args else []
    if parts and parts[-1].isdigit():
        page = max(1, int(parts[-1]))
    per = 10
    total = len(_BANK)
    pages = max(1, (total + per - 1) // per)
    page = min(page, pages)
    start = (page - 1) * per
    lines = ["📚 海龟汤题库 (第 %d/%d 页)" % (page, pages)]
    for i in range(start, min(start + per, total)):
        q, a, m = _BANK[i]
        stars = "⭐" * int(m.get("difficulty", 1))
        title = m.get("title", "")
        snippet = q[:30] + ("..." if len(q) > 30 else "")
        lines.append("#%s %s%s\n%s" % (m.get("id", ""), title, stars, snippet))
    lines.append("用 `开始海龟汤 题号` 挑战指定题目喵")
    if pages > 1:
        lines.append("用 `题库列表 页数` 翻页喵")
    await ctx.reply("\n\n".join(lines))


@register(keywords=["题目详情"], help="查看海龟汤题目详情喵", role=ROLE_ALL)
async def cmd_turtle_detail(ctx):
    parts = ctx.args.split() if ctx.args else []
    if not parts or not parts[0].isdigit():
        await ctx.reply("请指定题号喵，例如 `题目详情 1`")
        return
    qid = parts[0].zfill(3)
    for q, a, m in _BANK:
        if m.get("id") == qid:
            stars = "⭐" * int(m.get("difficulty", 1))
            title = m.get("title", "")
            await ctx.reply("📖 题目详情 #%s%s%s\n\n题目内容：\n%s\n\n用 `开始海龟汤 %s` 开始挑战喵"
                            % (qid, (" " + title) if title else "", (" " + stars) if stars else "",
                               q, parts[0]))
            return
    await ctx.reply("未找到题号 %s 的题目喵~" % parts[0])


@register(keywords=["海龟汤帮助"], help="海龟汤玩法说明喵", role=ROLE_ALL)
async def cmd_turtle_help(ctx):
    await ctx.reply(
        "🐢 海龟汤推理游戏\n\n"
        "我会给一个看似不合理的情景，你只能提能用 是/否/无关 回答的问题，推理出真相。\n\n"
        "指令：\n"
        "· `开始海龟汤 [题号]` 开局\n"
        "· `海龟汤提问 你的问题` 提问\n"
        "· `公布答案` 提前看答案\n"
        "· `换一题` 换题并重置次数\n"
        "· `结束海龟汤` / `强制结束海龟汤` 结束\n"
        "· `题库列表 [页]` / `题目详情 题号` 查题库\n\n"
        "猜答案时用 `海龟汤提问 答案是...`。每局 %d 次提问、%d 秒超时喵。"
        % (_MAX_QUESTIONS, _SESSION_TIMEOUT))
    return


# ---------- 无进行中游戏时给提示的“结束类”命令 ----------
@register(keywords=["结束海龟汤", "强制结束海龟汤", "公布答案", "换一题"],
          help="", role=ROLE_ALL, exact=False)
async def cmd_turtle_ctrl(ctx):
    await ctx.reply("当前没有正在进行的海龟汤游戏喵~")


# ---------- 多轮步进（dispatch 顶部调用） ----------
def _strip_prefix(text, kw):
    t = (text or "").strip()
    if t.startswith(kw):
        t = t[len(kw):].strip()
    return t


async def _handle_turn(ctx, s, question):
    s["question_count"] += 1
    s["expires"] = time.time() + _SESSION_TIMEOUT

    guess_kw = ["答案是", "真相是", "因为", "所以", "是因为", "原因是",
                "我觉得是", "我认为是", "应该是", "一定是", "肯定是"]
    is_guess = (any(kw in question for kw in guess_kw)
                or (len(question) > 25 and any(w in question for w in ["导致", "造成", "结果", "发生了", "事实是"])))
    if is_guess and _is_answer_correct(question, s["answer"]):
        await ctx.reply(_end_text("🎉 恭喜答对了！", s["question"], s["answer"],
                                  s["metadata"], s["question_count"]))
        SESSIONS.pop(ctx.openid, None)
        return

    if s["question_count"] > _MAX_QUESTIONS:
        await ctx.reply(_end_text("🎯 游戏结束！", s["question"], s["answer"],
                                  s["metadata"], s["question_count"]))
        SESSIONS.pop(ctx.openid, None)
        return

    ans = _simple_judge(question, s["answer"])
    remaining = _MAX_QUESTIONS - s["question_count"]
    await ctx.reply(_round_text(question, s["question_count"], ans, remaining))


async def consume(ctx):
    """处理进行中的海龟汤会话。返回 True 表示该消息已被消费。"""
    s = SESSIONS.get(ctx.openid)
    if not s:
        return False
    if s["scene"] != ctx.scene or s["target"] != ctx.target:
        return False
    if time.time() > s["expires"]:
        SESSIONS.pop(ctx.openid, None)
        return False

    text = (getattr(ctx.message, "content", None) or "").strip()

    if text.startswith("开始海龟汤"):
        await ctx.reply("你已有一个进行中的海龟汤游戏喵，先发 `结束海龟汤` 结束它~")
        return True
    if text.startswith("海龟汤提问"):
        await _handle_turn(ctx, s, _strip_prefix(text, "海龟汤提问"))
        return True
    if text == "结束海龟汤" or text == "强制结束海龟汤":
        await ctx.reply(_end_text("👋 游戏结束", s["question"], s["answer"],
                                  s["metadata"], s["question_count"]))
        SESSIONS.pop(ctx.openid, None)
        return True
    if text == "公布答案":
        await ctx.reply(_end_text("🎯 答案公布\n\n题目：%s" % s["question"],
                                  s["question"], s["answer"], s["metadata"],
                                  s["question_count"]))
        return True
    if text == "换一题":
        current = s["question"]
        for _ in range(10):
            q, a, m = _get_question()
            if q and q != current:
                break
        if not q:
            await ctx.reply("无法获取新题目喵，稍后再试~")
            return True
        s.update({"question": q, "answer": a, "metadata": m, "question_count": 0})
        await ctx.reply("🔄 换题成功！\n\n题目：\n%s\n\n提问次数已重置为 %d 次喵~"
                        % (q, _MAX_QUESTIONS))
        return True
    if text == "海龟汤帮助":
        await cmd_turtle_help(ctx)
        return True

    # 进行中的普通消息：不消费，交还给其它命令处理
    return False