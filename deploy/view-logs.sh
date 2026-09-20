#!/usr/bin/env bash
# 用 SSH 查看服务器上 QQ bot 的运行状态 —— 等价于本地的 cmd 窗口
#
# 用法（在你自己的终端里跑，需要先有私钥）：
#   ssh -i D:/jsq/qqbot/tmp/keys/qqbot_ecs root@47.83.166.37
#
# 进服务器后，常用命令都在下面各函数里，直接复制粘贴即可。

HOST=47.83.166.37
KEY=D:/jsq/qqbot/tmp/keys/qqbot_ecs

# ---------- 0. 登录 ----------
ssh -i "$KEY" root@$HOST

# ---------- 1. 看「实时滚动日志」（最像本地 cmd 的体验）----------
journalctl -u qqbot -f
# 或者只看 bot 自己写的日志文件：
tail -f /root/qq-cat-bot/logs/bot.log
# 错误单独看：
tail -f /root/qq-cat-bot/logs/bot.err.log

# ---------- 2. 看最近 N 行（不滚动）----------
journalctl -u qqbot -n 100 --no-pager
tail -n 100 /root/qq-cat-bot/logs/bot.log

# ---------- 3. 服务状态 ----------
systemctl status qqbot            # 存活状态 + 最近日志
systemctl is-active qqbot         # 只输出 active / inactive
systemctl show -p MainPID --value qqbot   # 主进程 PID

# ---------- 4. 重启 / 停止 / 启动 ----------
systemctl restart qqbot
systemctl stop qqbot
systemctl start qqbot

# ---------- 5. 端口监听检查 ----------
ss -lntp | grep -E '9090|9091|9092'

# ---------- 6. 崩溃自愈验证（强杀进程，看 systemd 是否自动拉起）----------
systemctl show -p MainPID --value qqbot       # 记住 PID
kill -9 <上面那个PID>
sleep 15 && systemctl is-active qqbot          # 应仍为 active

# ---------- 7. 只看今天以来的报错 ----------
journalctl -u qqbot --since today -p err --no-pager

# ---------- 8. Q&A：日志里怎么找问题 ----------
#  · 「机器人已上线」= 启动成功
#  · 「TLS handshake with edge error: EOF」= cloudflared 隧道断了（代理问题）
#  · 「机器人心跳维持启动」= WS 长连接正常
#  · 报错堆栈一般都带插件名，例如 jrys / meme
