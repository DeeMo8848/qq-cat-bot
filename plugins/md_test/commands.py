# -*- coding: utf-8 -*-
"""Markdown / 按钮能力实测插件。

用来**逐项验证** QQ 开放平台的两类消息交互能力（依据官方文档 2026-04/07 更新）：

| 能力 | 官方说法 | 本插件的用途 |
|---|---|---|
| markdown 消息（msg_type=2） | 2026/04/23 起群聊/单聊自定义 markdown **全量开放**，无需申请 | 逐条验证每种语法是否真的渲染 |
| 消息按钮（keyboard） | 官方标注「模板=**【申请使用】**／自定义=**【内邀开通】**」 | 实测群聊到底能不能发自定义按钮 |
| 文字链 | `<qqbot-cmd-input>` / `<qqbot-cmd-enter>` / `<qqbot-at-user>` | 验证哪些在群聊可用（文档注明 cmd-enter 群聊不支持） |

**触发词刻意取长且不常见**（`md测试` / `markdown测试`），避免与日常闲聊冲突。
匹配规则与 sv_card 一致：**触发词在消息开头 + 其后紧跟词边界**。

用法：
    md测试              -> 总览 + 多行按钮（默认就带按钮，最容易暴露问题）
    md测试 标题|样式|列表|引用|分割|链接|图片|换行|指令|按钮|全部
"""

from urllib.parse import quote

from bot.commands import register, ROLE_ALL

# 供 Web 后台「插件总开关」使用
MD_CMD_NAMES = {"cmd_md_test"}

TRIGGERS = ["markdown测试", "md测试"]

# 触发词之后的合法边界（与其后第一个字符比对）
_BOUNDARY = set(" \t\r\n，,、;；*.-—_/\\|·：:！!？?～~（）()【】[]")

# ---------- markdown 样例 ----------
_S_OVERVIEW = """# 🧪 Markdown 测试场

群聊自定义 markdown **已全量开放**（2026/04/23 起），本插件逐项验证官方文档里的语法。

## 可用子命令
1. `标题` 2. `样式` 3. `列表` 4. `引用`
2. `分割` 6. `链接` 7. `图片` 8. `换行`
3. `指令` 10. `按钮` 11. `全部`

> 例：md测试 图片
> 底部按钮也能直接触发（如果按钮能发出来的话）"""

_S_TITLE = """# 一号标题
## 二号标题
### 三号标题
正文一行，用来对比标题字号是否有变化"""

_S_STYLE = """**加粗**
__下划线加粗__
_斜体_
*星号斜体*
***加粗斜体***
~~删除线~~

（以上是官方文档列出的全部 6 种文字样式）"""

_S_LIST = """# 有序列表
1. 第一项
2. 第二项
3. 第三项

# 无序列表
- 甲
- 乙
- 丙

# 嵌套（二级列表前要空 4 个空格）
1. 嵌套一层
    - 列表前是普通文本，则需要在列表前用空行隔开
    - 这是嵌套的第二行
2. 嵌套二层
    1. 我是有序列表，二级列表前面需要空 4 个空格
    2. 有序与无序可以相互嵌套"""

_S_QUOTE = """> 青青子衿，悠悠我心，但为君故，沉吟至今
> 四月维夏，六月徂暑。先祖匪人，胡宁忍予
> 秋日凄凄，百卉具腓。乱离瘼矣，爰其适归？
诗经《小雅》

上面三行应该渲染成一个块引用（左侧灰色竖线）"""

_S_DIVIDER = """这是段落一

***

这是段落二（上面那行 *** 应渲染成一条分割线）

___

这是段落三（用 ___ 再试一次）"""

_S_LINK = """[🔗腾讯网](https://www.qq.com)
文档可以访问<https://doc.qq.com>

裸链接测试：https://www.qq.com （不加任何标记，看会不会自动识别）"""

_IMG_OFFICIAL = ("https://resource5-1255303497.cos.ap-guangzhou.myqcloud.com/"
                 "abcmouse_word_watch/markdown/building.png")


def _self_image_url() -> str:
    """放在自己静态站点上的图片（部署时复制到 public_html/mdtest/）。"""
    try:
        from config import STATIC_PUBLIC_URL
        return str(STATIC_PUBLIC_URL).rstrip("/") + "/mdtest/art.png"
    except Exception:
        return ""


_S_BREAK = """第一行

第二行

\u200B
\u200B
第三行（前两行之间插了两个零宽空格，官方推荐的「换多行」写法）

最后一段：
这一行和下一行只有一个普通换行，
看会不会被合并成一行。"""


def _text_chain_sample() -> str:
    """文字链样例：text/show 都要 urlencode（官方要求）。

    ★ 实测结论（2026-10-08）：`<qqbot-cmd-enter>` 在群聊**确实不支持** —— 平台直接返回
      400 `{'code': 40034106, 'message': '群消息不支持qqbot-cmd-enter'}`，
      而且**整条消息都发不出去**（并非只忽略那一段）。所以这里只用 cmd-input，
      不把 cmd-enter 放进真实消息里（文档原文：「群聊和文字子频道不支持该能力」）。
    """
    ci_text = quote("md测试 列表")
    ci_show = quote("① 插一条列表指令")
    return """文字链测试（正文里的可点击标签，和底部按钮是两回事）：

参数指令 · 点击后把文本插入输入框：
<qqbot-cmd-input text="%s" show="%s" />

@某人 · 需要真实 openid，这里放个占位看会不会原样显示：
<qqbot-at-user id="00000000000000000000000000000000" />""" % (ci_text, ci_show)


def _image_sample() -> str:
    self_url = _self_image_url()
    lines = ["官方示例图（带尺寸 320×208）：",
             "![building #320px #208px](%s)" % _IMG_OFFICIAL,
             "",
             "同一张图不带尺寸：",
             "![building](%s)" % _IMG_OFFICIAL]
    if self_url:
        lines += ["", "自己站点上的图（验证公网 URL 是否可用）：",
                  "![art #260px #300px](%s)" % self_url]
    return "\n".join(lines)


# ---------- 按钮样例 ----------
def _btn(bid, label, visited, style, atype, data, **extra):
    """构造一个 button（官方字段见「消息按钮」文档）。

    atype: 0=跳转 1=回调 2=指令
    style: 0=灰色线框 1=蓝色线框
    unsupport_tips 是**必填**字段。
    """
    action = {
        "type": atype,
        "permission": {"type": 2},          # 2 = 所有人可操作
        "data": data,
        "unsupport_tips": "当前客户端版本不支持此按钮",
    }
    action.update(extra)
    return {
        "id": bid,
        "render_data": {"label": label, "visited_label": visited, "style": style},
        "action": action,
    }


# 单行：一个指令按钮，点击后自动发送（enter=True）
BTN_ONE_ROW = {"content": {"rows": [
    {"buttons": [
        _btn("b1", "📋 列表语法", "已请求", 0, 2, "md测试 列表", enter=True),
    ]},
]}}

# 多行：覆盖 3 种 action.type + 2 种 style
BTN_MULTI_ROW = {"content": {"rows": [
    {"buttons": [
        _btn("b1", "标题", "标题", 0, 2, "md测试 标题", enter=True),
        _btn("b2", "样式", "样式", 0, 2, "md测试 样式", enter=True),
        _btn("b3", "图片", "图片", 0, 2, "md测试 图片", enter=True),
    ]},
    {"buttons": [
        _btn("b4", "蓝色线框", "已点", 1, 2, "md测试 引用", enter=True),
        _btn("b5", "跳转 qq.com", "跳转中", 1, 0, "https://www.qq.com"),
    ]},
    {"buttons": [
        _btn("b6", "回调按钮（测 INTERACTION_CREATE）", "已回调", 0, 1, "md_test_cb"),
    ]},
]}}

_S_BTN_TEXT = """# 🔘 按钮测试

这条消息底部挂了按钮，用来验证**自定义按钮（keyboard）在群聊能不能发**。

官方「消息按钮」文档写：
- 按钮模板 → 【申请使用】
- **自定义按钮 → 【内邀开通】**

如果你能看到下面三行按钮，说明**已经开通**了。
第一行是指令按钮（`enter=true`，点了直接发指令）；第二行有个蓝色线框样式和一个跳转按钮；
第三行是回调按钮，点它会触发 `INTERACTION_CREATE` 事件。"""

_S_CALLBACK = """# 🧪 回调按钮实验组

**回调按钮（action.type=1）的价值**：点完立刻触发 bot，**不用再按发送** ——
这是它和指令按钮的本质区别。

下面 6 个按钮各测一种「收到点击后怎么产出消息」，**点一个看一个**：

1. **① event_id 回复** —— 官方「被动消息(响应事件)」的写法（`event_id=`）
2. **② 主动消息** —— 不带任何 id 直接发
3. **③ 回 markdown** —— 用 `event_id` 回一条富文本
4. **④ 回显 data** —— 验证 `button_data` 透传
5. **⑤ code=1** —— 回应改成「操作失败」，看客户端提示怎么变
6. **⑥ code=5** —— 回应改成「仅管理员操作」

> ①②③④ 只要在群里收到 bot 的新消息，就说明**回调按钮能直接产出内容**
> （真正做到省掉「输入 + 发送」）。
> ⑤⑥ 只在客户端提示上观察差异。"""

# 回调按钮实验组：data 用 `mdtest:<exp>` 约定，由 webhook 转给 plugins/md_test/interaction.py
BTN_CALLBACK_LAB = {"content": {"rows": [
    {"buttons": [
        _btn("c1", "① event_id 回复", "已测", 0, 1, "mdtest:event"),
        _btn("c2", "② 主动消息", "已测", 0, 1, "mdtest:active"),
        _btn("c3", "③ 回 markdown", "已测", 0, 1, "mdtest:md"),
    ]},
    {"buttons": [
        _btn("c4", "④ 回显 data", "已测", 1, 1, "mdtest:echo"),
        _btn("c5", "⑤ code=1", "已测", 1, 1, "mdtest:code1"),
        _btn("c6", "⑥ code=5", "已测", 1, 1, "mdtest:code5"),
    ]},
]}}

_S_ALL = "\n\n".join([
    "# 全部语法合并",
    "## 标题二",
    "**加粗** / _斜体_ / ~~删除线~~",
    "1. 有序一\n2. 有序二",
    "- 无序甲\n- 无序乙",
    "> 块引用一行",
    "***",
    "[🔗腾讯网](https://www.qq.com)",
])

# 子命令 -> (正文, 按钮 or None)
_SUBCMDS = {
    "标题": (_S_TITLE, None),
    "样式": (_S_STYLE, None),
    "列表": (_S_LIST, None),
    "引用": (_S_QUOTE, None),
    "分割": (_S_DIVIDER, None),
    "链接": (_S_LINK, None),
    "图片": (None, None),          # 动态生成，见下
    "换行": (_S_BREAK, None),
    "指令": (None, None),          # 动态生成
    "全部": (_S_ALL, None),
    "按钮": (_S_BTN_TEXT, BTN_ONE_ROW),
    "多行按钮": (_S_BTN_TEXT, BTN_MULTI_ROW),
    "回调": (_S_CALLBACK, BTN_CALLBACK_LAB),
}

_HELP = """🧪 Markdown 测试场 · 用法

md测试                     总览 + 多行按钮
md测试 标题 / 样式 / 列表 / 引用 / 分割 / 链接
md测试 图片 / 换行 / 指令 / 全部
md测试 按钮                单行按钮
md测试 多行按钮            三行按钮（覆盖 3 种 action.type 与 2 种 style）
md测试 回调                回调按钮实验组（6 个按钮，测点完后怎么产出消息）

每个子命令只测一项，方便定位哪种语法不生效。"""


# ---------- 触发判定（与 sv_card 同一套：开头 + 词边界）----------
def _match_trigger(text: str, triggers):
    t = (text or "").strip()
    if not t:
        return None
    tl = t.lower()
    for tr in sorted(triggers, key=len, reverse=True):
        if not tr or not tl.startswith(tr.lower()):
            continue
        rest = t[len(tr):]
        if not rest or rest[0] in _BOUNDARY:
            return tr
    return None


def _matcher(text: str) -> bool:
    return _match_trigger(text, TRIGGERS) is not None


def _subkey(text: str) -> str:
    """剥掉触发词，取子命令名（容错：允许写成「md测试图片」这种紧贴写法之外的常见形式）。"""
    t = (text or "").strip()
    tr = _match_trigger(t, TRIGGERS)
    if not tr:
        return ""
    rest = t[len(tr):].lstrip(" \t，,、;；*.-—_/\\|·：:")
    return rest.strip()


@register(
    keywords=TRIGGERS,
    help="Markdown / 按钮能力实测场（md测试 查看用法）",
    matcher=_matcher,
    role=ROLE_ALL,
)
async def cmd_md_test(ctx):
    text = getattr(ctx, "args", None) or getattr(ctx.message, "content", "") or ""
    key = _subkey(text)

    if key in ("帮助", "help", "?", "？"):
        await ctx.sender.send_markdown(ctx.message, _HELP, reply=True)
        return

    # 动态生成的样例
    if key == "图片":
        await ctx.sender.send_markdown(ctx.message, _image_sample(), reply=True)
        return
    if key == "指令":
        await ctx.sender.send_markdown(ctx.message, _text_chain_sample(), reply=True)
        return

    if not key:
        # 默认：总览 + 多行按钮（最能暴露「按钮到底能不能发」）
        await ctx.sender.send_markdown(ctx.message, _S_OVERVIEW, reply=True,
                                       keyboard=BTN_MULTI_ROW)
        return

    hit = _SUBCMDS.get(key)
    if hit is None:
        # 容错：允许「md测试 看看图片」这类写法
        for k, v in _SUBCMDS.items():
            if k in key:
                hit = v
                break
    if hit is None:
        await ctx.sender.send_markdown(
            ctx.message,
            "没找到子命令 `%s`\n\n%s" % (key, _HELP),
            reply=True,
        )
        return

    body, keyboard = hit
    await ctx.sender.send_markdown(ctx.message, body, reply=True, keyboard=keyboard)
