#!/usr/bin/env bash
# 把网关部署到家里那台 GPU 服务器的 WSL 上。
#
# 同步方式是「服务器自己 git pull」，不是从开发机推文件：
# 服务器上那份是 origin/main 的只读检出，不在那边改代码。
#
# 前提：服务器上 ~/workspace/grokgen 已经 clone 过一次（见下面的 bootstrap 提示），
# 且那台机器能用 SSH 访问 GitHub。
#
# 用法：
#   scripts/deploy_server.sh              # 拉代码 + 装依赖 + 重启网关
#   scripts/deploy_server.sh --no-restart # 只拉代码和装依赖

set -euo pipefail

SSH_HOST="icewalnut-wsl"
REMOTE_REPO="\$HOME/workspace/grokgen"
GATEWAY_PORT=7869

# ⚠️ 服务器上装 Python 包必须走镜像，而且必须摘掉代理。
#
# 那台机器的 WSL 从 Windows 继承了 http_proxy=127.0.0.1:7890，
# 但那个端口上没有任何进程在监听（runbook §6.3）。2026-09-22 实测：
#   带代理访问 pypi.org       → 20 秒超时，http=000
#   摘代理直连 pypi.org       → 不稳定
#   清华镜像                  → http=200，0.22 秒，755 KB/s
# 镜像地址放成变量，某天它不可用时改这一行即可。
PIP_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"

RESTART=1
[[ "${1:-}" == "--no-restart" ]] && RESTART=0

# 本地还有没提交或没推的东西时先提醒，否则会部署出一个跟本地不一样的版本
if [[ -n "$(git status --porcelain)" ]]; then
    echo "⚠️  本地有未提交的改动，服务器只会拿到已经 push 的版本：" >&2
    git status --short >&2
    echo >&2
fi
if [[ -n "$(git log '@{u}..HEAD' --oneline 2>/dev/null)" ]]; then
    echo "⚠️  有已提交但未 push 的 commit，先 git push 再部署。" >&2
    exit 1
fi

echo "==> 在 $SSH_HOST 上拉取并安装"
ssh "$SSH_HOST" bash -s <<REMOTE
set -euo pipefail
cd $REMOTE_REPO

# reset --hard 会静默丢掉服务器上的本地改动。按约定那边不该有改动，
# 但「丢弃」这件事必须是可见的，否则排查时会完全想不到这里。
if [[ -n "\$(git status --porcelain)" ]]; then
    echo "⚠️  服务器上有未提交改动，下面的 reset --hard 会丢掉它们："
    git status --short
fi

git fetch --quiet origin
git reset --hard --quiet origin/main
echo "当前版本: \$(git log -1 --format='%h %s')"

# 网关的 venv 独立于 ComfyUI 的 venv，不要合并
if [[ ! -d server/.venv ]]; then
    python3 -m venv server/.venv
fi

# 只摘掉 pip 这一次调用的代理变量，不改整个脚本或那台机器的环境 ——
# 代理配置是用户自己在用的（runbook §6.3）。
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
    -u all_proxy -u ALL_PROXY \
    server/.venv/bin/pip install --quiet --upgrade pip -i $PIP_INDEX_URL
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
    -u all_proxy -u ALL_PROXY \
    server/.venv/bin/pip install --quiet -i $PIP_INDEX_URL -r server/requirements.txt
REMOTE

if [[ $RESTART -eq 1 ]]; then
    echo "==> 重启网关"
    ssh "$SSH_HOST" bash -s <<REMOTE
set -euo pipefail
cd $REMOTE_REPO/server

pkill -f 'uvicorn app.main:app' || true
sleep 2

# 脱离 SSH 会话，否则连接一断进程就被杀
setsid nohup .venv/bin/uvicorn app.main:app \
    --host 0.0.0.0 --port $GATEWAY_PORT \
    > gateway.log 2>&1 < /dev/null &
sleep 3

# 只报告「进程起来了」是不够的，要确认端口真的在监听
if ss -tln | grep -q ":$GATEWAY_PORT "; then
    echo "网关已在 $GATEWAY_PORT 上监听"
else
    echo "网关没起来，日志最后 30 行：" >&2
    tail -30 gateway.log >&2
    exit 1
fi
REMOTE
fi

echo "==> 完成"

# ---------------------------------------------------------------
# 服务器上的首次 bootstrap（只做一次）：
#
#   ssh icewalnut-wsl
#   mkdir -p ~/workspace && cd ~/workspace
#   git clone git@github.com:IceWalnut/grokgen.git
#
# 若那台机器还没有能访问 GitHub 的 SSH key，先 ssh-keygen 并把公钥加到
# GitHub 账号里；仓库是 private，没有 key 就 clone 不下来。
# ---------------------------------------------------------------
