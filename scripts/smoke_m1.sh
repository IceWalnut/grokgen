#!/usr/bin/env bash
# M1 端到端冒烟：上传图 → 提交 I2VA → 轮询到完成 → 从网关取回 mp4（含 Range）
#              → 在服务器上 ffprobe 断言内容 → 中断验证
#
# ⚠️ ffprobe 在**服务器上**跑，不在开发机上 —— 开发机没有 ffmpeg（runbook §1）。
#    执行文档原来写的是「下载 mp4 → 本地 ffprobe」，那条前提是错的，已回写更正。
#    「下载回来」这一步不能因此省掉：它和 ffprobe 验的不是同一件事 ——
#    **字节在本地验（取回链路与 Range，VS-11），像素在服务器验（编码与尺寸，VS-6）。**
#
# ⚠️ 所有 curl 必须带 --noproxy '*'。开发机的 http_proxy 指向 127.0.0.1:7890，
#    而 no_proxy 不含 tailnet 域名，不加这个参数会返回 502，
#    **看起来和服务器挂了一模一样**（runbook §6.4）。
#    所以 curl 只在 _http() 里出现一次，别处一律调它。
#
# ⚠️ 解析 JSON 的分工：网关的响应用 jq，ComfyUI 的 /history 用 python3。
#    实测 jq 1.6 遇到字符串里的裸控制字符直接 parse error 退出，且没有宽松开关；
#    而 /history 会回放用户 prompt 的原文，M1R3 已经在这上面挂过一次。
#    python 的 json.load(..., strict=False) 能容忍它。
#
# 用法：
#   scripts/smoke_m1.sh                 # 默认：两次生成，约 3 分钟 GPU
#   scripts/smoke_m1.sh --cold-start    # 先卸载模型，量冷启动读数
#   scripts/smoke_m1.sh --skip-cancel   # 跳过中断验证
#
# 退出码即出问题的层次，看码就知道该查谁：
#   0 成功 / 10 本机环境 / 11 网络或 ssh / 12 ComfyUI / 13 网关 / 14 生成 / 15 产物 / 16 补验证

set -euo pipefail

# 环境变量名与默认值必须和 server/tests/test_integration_comfy.py 一致 ——
# 两处各写一套默认地址，改地址时一定会漏掉一处。
COMFY_URL="${GROKGEN_COMFY_BASE_URL:-http://icewalnut-1060.tail22a711.ts.net:8188}"
GATEWAY_URL="${GROKGEN_GATEWAY_URL:-http://icewalnut-1060.tail22a711.ts.net:7869}"
SSH_HOST="icewalnut-wsl"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE_MAKER="$REPO_ROOT/scripts/lib/make_smoke_image.py"
EXPECT_IMAGE_W=1280
EXPECT_IMAGE_H=720
# 1280x720 按比例推画布正好落回 736x416，与 M1R3 的基线逐项对齐。
EXPECT_FRAMES=124

POLL_FAST=1      # 提交后到看见第一次状态变化，密一点
POLL_SLOW=3      # 之后
TIMEOUT_WARM=420
TIMEOUT_COLD=1200

COLD_START=0
DO_CANCEL=1
ALLOW_BUSY=0
ALLOW_DRIFT=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --cold-start)    COLD_START=1 ;;
        --skip-cancel)   DO_CANCEL=0 ;;
        --allow-busy)    ALLOW_BUSY=1 ;;
        --allow-drift)   ALLOW_DRIFT=1 ;;
        -h|--help)       sed -n '1,30p' "$0"; exit 0 ;;
        *)               echo "未知参数：$1" >&2; exit 10 ;;
    esac
    shift
done

WORKDIR=$(mktemp -d)
SUCCESS=0
ASSET_ID=""

cleanup() {
    # ⚠️ 失败时**保留**现场：那几个 JSON 就是排障材料。
    if [[ $SUCCESS == 1 ]]; then
        rm -rf "$WORKDIR"
    else
        echo "" >&2
        echo "现场保留在 $WORKDIR" >&2
    fi
}
trap cleanup EXIT

die()  { local code=$1; shift; echo "✗ $*" >&2; exit "$code"; }
ok()   { echo "  ✓ $*"; }
warn() { echo "  ⚠️  $*" >&2; }
step() { echo ""; echo "==> $*"; }

# ───────── HTTP：curl 只在这里出现一次 ─────────

_http() { curl -sS --noproxy '*' -m "${HTTP_TIMEOUT:-60}" "$@"; }

api()   { _http "$GATEWAY_URL$1"; }
comfy() { _http "$COMFY_URL$1"; }

comfy_post() {
    _http -X POST -H 'Content-Type: application/json' -d "$2" "$COMFY_URL$1"
}

# 诊断：区分「服务器挂了」「本机代理拦了」「防火墙又挡了」
#
# 这三者从开发机看**症状完全一样**（连不上或 502），
# 所以不靠猜，靠第三个视角：从服务器内部再打一次自己。
diagnose() {
    local url="$1" port="$2"
    echo "" >&2
    echo "==> 诊断 $url 为什么不通" >&2

    local direct proxied inside listening
    direct=$(curl -s -o /dev/null -w '%{http_code}' --noproxy '*' -m 8 "$url" 2>/dev/null || echo 000)
    proxied=$(curl -s -o /dev/null -w '%{http_code}' -m 8 "$url" 2>/dev/null || echo 000)
    inside=$(ssh -o ConnectTimeout=10 "$SSH_HOST" \
        "curl -s -o /dev/null -w '%{http_code}' --noproxy '*' -m 8 http://127.0.0.1:$port/" \
        2>/dev/null || echo SSH_FAIL)
    listening=$(ssh -o ConnectTimeout=10 "$SSH_HOST" \
        "ss -tln | grep -c ':$port ' || true" 2>/dev/null || echo SSH_FAIL)

    echo "    不带代理：$direct    带代理：$proxied    服务器内部：$inside    端口在监听：$listening" >&2
    echo "" >&2

    if [[ "$inside" == "SSH_FAIL" ]]; then
        echo "    结论：连 ssh 都不通。WSL 可能被回收了（runbook §4），" >&2
        echo "          或者 Hyper-V 防火墙又把 2222 挡住了（§6.5）。" >&2
        echo "          查：ssh icewalnut 'Get-ScheduledTask -TaskName Treader-Keep-WSL-Alive'" >&2
        return 11
    elif [[ "$listening" == "0" ]]; then
        echo "    结论：服务真的没在监听，进程挂了。" >&2
        echo "          查：ssh $SSH_HOST 'tail -50 ~/workspace/grokgen/server/gateway.log'" >&2
        return 12
    else
        echo "    结论：服务在服务器内部是好的，但从开发机连不上。" >&2
        echo "          脚本已全程 --noproxy，所以更可能是 Hyper-V 防火墙没放行 $port（§6.5），" >&2
        echo "          修：ssh icewalnut 'powershell -File ...\\allow_wsl_ports.ps1'" >&2
        echo "          （当前 http_proxy=${http_proxy:-未设置}）" >&2
        return 11
    fi
}

# 从网关的 JSON 里取一个字段。网关的响应由 FastAPI 序列化，转义一定正确，jq 安全。
jget() { jq -r "$1"; }

# 从 ComfyUI 的 /history 取记录：必须走 python，见文件头的说明。
history_record() {
    local prompt_id="$1" out="$WORKDIR/history_$1.json"
    comfy "/history/$prompt_id" > "$out"
    python3 - "$out" "$prompt_id" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f, strict=False)   # ← M1R3 挂在这个参数上
record = data.get(sys.argv[2])
# 重新序列化一次，转义就正确了，下游可以安全地交给 jq。
print(json.dumps(record, ensure_ascii=False) if record else "null")
PY
}

# 轮询一个任务直到终态。
#
# ⭐ **状态判据记在转移日志上，不靠采样。** 和 VS-9 的做法一致：
# 采样只能说「我看的那几眼是这样」，日志能说「整段过程是这样」。
#
# 写全局：STATES（转移日志）、ELAPSED（秒）、PROMPT_ID、FINAL_STATE
poll_job() {
    local job_id="$1" timeout="$2" cancel_at_running="${3:-0}"
    local t0 deadline last="" state interval=$POLL_FAST detail
    t0=$(date +%s)
    deadline=$(( t0 + timeout ))
    STATES=()
    PROMPT_ID=""
    COMFY_SAW_RUNNING=0
    local cancelled_already=0 heartbeat=0

    while :; do
        detail=$(api "/v1/jobs/$job_id") || { diagnose "$GATEWAY_URL/v1/health" 7869 || exit $?; }
        state=$(printf '%s' "$detail" | jget '.state')

        if [[ "$state" != "$last" ]]; then
            local at=$(( $(date +%s) - t0 ))
            STATES+=("$state@${at}s")
            echo "    [${at}s] ${last:-（提交）} → $state"
            [[ -n "$last" ]] && interval=$POLL_SLOW
            last="$state"
        fi

        # 与网关的说法互相独立的第二路证据：直接问 ComfyUI 它在跑什么。
        # 前置检查已经确认队列是空的，而本脚本是唯一提交方 ⇒ 跑着的那个就是我们的。
        local running_id
        running_id=$(comfy /queue 2>/dev/null | jq -r '.queue_running[0][1] // empty' 2>/dev/null || true)
        if [[ -n "$running_id" ]]; then
            COMFY_SAW_RUNNING=1
            PROMPT_ID="$running_id"
        fi

        if [[ "$cancel_at_running" == "1" && "$state" == "running" && $cancelled_already == 0 ]]; then
            echo "    → 发取消请求"
            api_cancel "$job_id"
            cancelled_already=1
        fi

        case "$state" in
            done|failed|cancelled)
                ELAPSED=$(( $(date +%s) - t0 ))
                FINAL_STATE="$state"
                return 0
                ;;
        esac

        local now
        now=$(date +%s)
        if (( now > deadline )); then
            ELAPSED=$(( now - t0 ))
            FINAL_STATE="$state"
            echo "$detail" > "$WORKDIR/timeout_$job_id.json"
            return 1
        fi
        # 长任务没有心跳时，人分不清「在跑」和「挂了」。
        if (( now - heartbeat >= 30 )); then
            heartbeat=$now
            local vram
            vram=$(comfy /system_stats 2>/dev/null | jq -r '.devices[0].vram_free' 2>/dev/null || echo "?")
            echo "    [$(( now - t0 ))s] 还在 $state，vram_free=$(fmt_gb "$vram")"
        fi
        sleep "$interval"
    done
}

api_cancel() {
    _http -X POST "$GATEWAY_URL/v1/jobs/$1/cancel" > "$WORKDIR/cancel_$1.json" || true
}

fmt_gb() {
    [[ "$1" =~ ^[0-9]+$ ]] || { echo "$1"; return; }
    awk "BEGIN{printf \"%.2f GB\", $1/1e9}"
}

# ═════════════════ P0：本机自检 ═════════════════
step "P0 本机自检"
for tool in curl jq python3 ssh cmp dd awk; do
    command -v "$tool" >/dev/null || die 10 "本机缺 $tool"
done
[[ -f "$IMAGE_MAKER" ]] || die 10 "找不到测试图生成器：$IMAGE_MAKER"
ok "工具齐全"

# ═════════════════ P1：前置检查 ═════════════════
step "P1 前置检查"

ssh -o ConnectTimeout=10 "$SSH_HOST" true 2>/dev/null \
    || die 11 "ssh $SSH_HOST 不通 —— WSL 可能被回收（runbook §4），或 2222 被挡了（§6.5）"
ok "ssh 通（顺带说明 WSL 还活着）"

ssh "$SSH_HOST" 'command -v ffprobe >/dev/null' \
    || die 11 "服务器上没有 ffprobe，产物内容断言做不了"
ok "服务器有 ffprobe"

HEALTH=$(api /v1/health) || { diagnose "$GATEWAY_URL/v1/health" 7869 || exit $?; }
[[ $(printf '%s' "$HEALTH" | jget '.gateway.status') == "ok" ]] || die 13 "网关 status 不是 ok"
[[ $(printf '%s' "$HEALTH" | jget '.comfy.reachable') == "true" ]] \
    || die 12 "网关报告 ComfyUI 不可达：$(printf '%s' "$HEALTH" | jget '.comfy.error')"

GW_REV=$(printf '%s' "$HEALTH" | jget '.gateway.version')
LOCAL_REV=$(git -C "$REPO_ROOT" rev-parse --short HEAD)
if [[ "$GW_REV" != "$LOCAL_REV" ]]; then
    MSG="网关跑的是 $GW_REV，本地 HEAD 是 $LOCAL_REV —— 冒烟会测到旧代码。先跑 scripts/deploy_server.sh"
    if [[ $ALLOW_DRIFT == 1 ]]; then warn "$MSG"; else die 13 "$MSG"; fi
else
    ok "网关版本 $GW_REV 与本地 HEAD 一致"
fi

QUEUE=$(comfy /queue) || { diagnose "$COMFY_URL/queue" 8188 || exit $?; }
BUSY=$(printf '%s' "$QUEUE" | jq '(.queue_running|length) + (.queue_pending|length)')
if [[ "$BUSY" != "0" ]]; then
    MSG="ComfyUI 正忙（$BUSY 个任务）—— 容量为 1 的串行队列，现在跑耗时读数没有意义"
    if [[ $ALLOW_BUSY == 1 ]]; then warn "$MSG"; else die 12 "$MSG"; fi
else
    ok "ComfyUI 队列空闲"
fi

# 显存读数是**测量条件**，不是判据（Validation.md §4.2）。
VRAM_START=$(comfy /system_stats | jq -r '.devices[0].vram_free')
VRAM_TOTAL=$(comfy /system_stats | jq -r '.devices[0].vram_total')
ok "起始显存：$(fmt_gb "$VRAM_START") 空闲 / $(fmt_gb "$VRAM_TOTAL") 总量"

# ═════════════════ P2：上传首帧图 ═════════════════
step "P2 上传首帧图"

TEST_IMAGE="$WORKDIR/first_frame.png"
MADE=$(python3 "$IMAGE_MAKER" "$TEST_IMAGE") || die 10 "生成测试图失败"
ok "生成测试图 $MADE（$(stat -c%s "$TEST_IMAGE") 字节）"

UPLOAD=$(_http -F "file=@$TEST_IMAGE" "$GATEWAY_URL/v1/uploads/image") \
    || { diagnose "$GATEWAY_URL/v1/health" 7869 || exit $?; }
echo "$UPLOAD" > "$WORKDIR/upload.json"

ASSET_ID=$(printf '%s' "$UPLOAD" | jget '.asset_id')
UP_W=$(printf '%s' "$UPLOAD" | jget '.width')
UP_H=$(printf '%s' "$UPLOAD" | jget '.height')

[[ "$ASSET_ID" == grokgen/* ]] || die 13 "asset_id 没有子目录前缀：$ASSET_ID"
# ⚠️ 这条同时验证了服务器端 ffprobe 读尺寸那条链路 ——
# 尺寸读错的话画布会按错的比例推，首帧就变形了，而那不会报错。
[[ "$UP_W" == "$EXPECT_IMAGE_W" && "$UP_H" == "$EXPECT_IMAGE_H" ]] \
    || die 13 "上传后读出的尺寸是 ${UP_W}x${UP_H}，期望 ${EXPECT_IMAGE_W}x${EXPECT_IMAGE_H}"
ok "asset_id=$ASSET_ID，服务器读出尺寸 ${UP_W}x${UP_H}"

# ═════════════════ P3：冷启动准备（可选）═════════════════
GEN_KIND="热启动（模型已在显存）"
TIMEOUT=$TIMEOUT_WARM

if [[ $COLD_START == 1 ]]; then
    step "P3 卸载模型，制造冷启动"
    comfy_post /free '{"unload_models":true,"free_memory":true}' >/dev/null \
        || die 12 "POST /free 失败"
    sleep 5
    VRAM_FREED=$(comfy /system_stats | jq -r '.devices[0].vram_free')
    # ⭐ 必须验证 /free 真的生效。没有这条断言，记下来的「冷启动读数」是假的 ——
    # 模型还在显存里，量到的仍然是热启动。
    awk "BEGIN{exit !($VRAM_FREED >= 0.9 * $VRAM_TOTAL)}" \
        || die 12 "卸载后 vram_free 只有 $(fmt_gb "$VRAM_FREED") / $(fmt_gb "$VRAM_TOTAL")，模型没卸干净，这一轮不算冷启动"
    ok "模型已卸载，vram_free 回到 $(fmt_gb "$VRAM_FREED")"
    GEN_KIND="冷启动（模型已卸载，含加载开销）"
    TIMEOUT=$TIMEOUT_COLD
fi

# ═════════════════ P4：生成 #1（I2VA，完整走完）═════════════════
step "P4 提交 I2VA 并轮询（$GEN_KIND）"

# ⚠️ 请求体用 jq 构造，不用字符串拼 —— prompt 里一旦有换行或引号，
# 拼出来的就是非法 JSON。M1R3 踩过的正是这一类。
# ⚠️ **故意不传 width/height**，让网关按首帧图比例推画布，
# 那条路径才是要复跑的那条。
BODY=$(jq -n \
    --arg asset "$ASSET_ID" \
    '{type:"h3_video", mode:"I2VA",
      prompt:{description:"[grokgen-smoke] a red sports car driving along a coastal road at sunset",
              soundscape:"engine hum and distant waves",
              music:"N/A"},
      first_frame_asset_id:$asset,
      duration_seconds:5.0, turbo:true}')

SUBMIT=$(_http -X POST -H 'Content-Type: application/json' -d "$BODY" "$GATEWAY_URL/v1/jobs") \
    || { diagnose "$GATEWAY_URL/v1/health" 7869 || exit $?; }
echo "$SUBMIT" > "$WORKDIR/submit.json"

JOB1=$(printf '%s' "$SUBMIT" | jget '.job_id')
NORM_W=$(printf '%s' "$SUBMIT" | jget '.normalized.width')
NORM_H=$(printf '%s' "$SUBMIT" | jget '.normalized.height')
NORM_FRAMES=$(printf '%s' "$SUBMIT" | jget '.normalized.length_frames')
NORM_DUR=$(printf '%s' "$SUBMIT" | jget '.normalized.actual_duration_seconds')
NORM_SEED=$(printf '%s' "$SUBMIT" | jget '.normalized.seed')

[[ $(printf '%s' "$SUBMIT" | jget '.state') == "queued" ]] || die 13 "提交后状态不是 queued"
(( NORM_W % 32 == 0 && NORM_H % 32 == 0 )) || die 13 "画布 ${NORM_W}x${NORM_H} 不是 32 的倍数"
[[ "$NORM_FRAMES" == "$EXPECT_FRAMES" ]] || die 13 "帧数是 $NORM_FRAMES，期望 $EXPECT_FRAMES"
ok "$JOB1 已排队：画布 ${NORM_W}x${NORM_H}、$NORM_FRAMES 帧、$NORM_DUR 秒、seed=$NORM_SEED"

poll_job "$JOB1" "$TIMEOUT" || die 14 "任务 $JOB1 在 ${ELAPSED}s 内没有结束，最后停在 $FINAL_STATE"
[[ "$FINAL_STATE" == "done" ]] \
    || die 14 "任务 $JOB1 落在 $FINAL_STATE：$(api "/v1/jobs/$JOB1" | jget '.failure_reason')"

GEN1_ELAPSED=$ELAPSED
GEN1_STATES="${STATES[*]}"
ok "生成完成，耗时 ${GEN1_ELAPSED} 秒"

# ── VS-19：running 状态在真实链路上可达 ──
#
# 这条守的是 M1R5 留下的缺口：running 靠「我的 prompt_id 出现在 ComfyUI 的
# queue_running 里」推进，而取 prompt_id 的槽位此前**只有替身证据**。
# 槽位取错不会报错，表现就是任务从 submitted 直接跳到完成。
if printf '%s\n' "${STATES[@]}" | grep -q '^running@'; then
    ok "VS-19：观察到 running（$(printf '%s\n' "${STATES[@]}" | grep '^running@')）"
    VS19="通过"
else
    echo "" >&2
    echo "✗ VS-19 未通过：整个轮询期间没有观察到 running。" >&2
    if [[ "$COMFY_SAW_RUNNING" == "1" ]]; then
        echo "  ⇒ ComfyUI 的 queue_running 非空（抓到 prompt_id=$PROMPT_ID），" >&2
        echo "    但网关始终没进 running。这指向 queue() 解析 prompt_id 的槽位取错了：" >&2
        echo "    server/app/comfy/http_client.py 里 entry[1] 那一处。" >&2
    else
        echo "  ⇒ ComfyUI 的 queue_running 一直是空的 —— 任务根本没被拾起，" >&2
        echo "    不是槽位解析的问题，先看 ComfyUI 那一侧。" >&2
    fi
    exit 16
fi

# ═════════════════ P5：取回产物 + Range（VS-11）═════════════════
step "P5 从网关取回产物"

DETAIL=$(api "/v1/jobs/$JOB1")
echo "$DETAIL" > "$WORKDIR/job1.json"
OUT_FILE=$(printf '%s' "$DETAIL" | jget '.outputs[0].filename')
OUT_SUB=$(printf '%s' "$DETAIL" | jget '.outputs[0].subfolder')
[[ -n "$OUT_FILE" && "$OUT_FILE" != "null" ]] || die 15 "任务完成了但没有产物记录"

REMOTE_PATH="\$HOME/workspace/ComfyUI/output/${OUT_SUB:+$OUT_SUB/}$OUT_FILE"
REMOTE_SIZE=$(ssh "$SSH_HOST" "stat -c%s \"$REMOTE_PATH\"") \
    || die 15 "服务器上找不到产物：$OUT_SUB/$OUT_FILE"

# ① 全量下载
VIDEO="$WORKDIR/out.mp4"
_http -D "$WORKDIR/h_full" -o "$VIDEO" "$GATEWAY_URL/v1/jobs/$JOB1/video" \
    || die 15 "下载产物失败"
grep -qi '^HTTP/.* 200' "$WORKDIR/h_full" || die 15 "全量下载没有返回 200"
LOCAL_SIZE=$(stat -c%s "$VIDEO")
[[ "$LOCAL_SIZE" == "$REMOTE_SIZE" ]] \
    || die 15 "下载到 $LOCAL_SIZE 字节，服务器上是 $REMOTE_SIZE 字节"
ok "全量下载 $LOCAL_SIZE 字节，与服务器上的文件一致"

# ② Accept-Ranges —— App 靠它决定要不要让用户拖进度条
grep -qi '^accept-ranges:[[:space:]]*bytes' "$WORKDIR/h_full" \
    || die 15 "响应头里没有 Accept-Ranges: bytes，播放器会退化成必须下完才能播"
ok "Accept-Ranges: bytes"

# ③ 本地就能做的容器自检，不需要 ffprobe
dd if="$VIDEO" bs=1 skip=4 count=4 2>/dev/null | grep -q ftyp \
    || die 15 "下载回来的不是 mp4（偏移 4 处不是 ftyp）"
ok "mp4 容器头正确"

# ④ Range 请求（VS-11）
_http -r 0-1023 -D "$WORKDIR/h_range" -o "$WORKDIR/head.bin" "$GATEWAY_URL/v1/jobs/$JOB1/video"
grep -qi '^HTTP/.* 206' "$WORKDIR/h_range" || die 15 "Range 请求没有返回 206"
EXPECT_CR="bytes 0-1023/$REMOTE_SIZE"
grep -qi "^content-range:[[:space:]]*$EXPECT_CR" "$WORKDIR/h_range" \
    || die 15 "Content-Range 不对，期望 $EXPECT_CR，实际 $(grep -i '^content-range' "$WORKDIR/h_range")"
[[ $(stat -c%s "$WORKDIR/head.bin") == 1024 ]] || die 15 "206 的响应体不是 1024 字节"

# ⭐ 正向断言：这 1024 字节要与全量文件的前 1024 字节逐字节相同。
# 只查 206 和 Content-Range 是负向的 —— 返回正确的头、错误的字节照样通过，
# 而那在手机上的表现是拖动之后播放错位，很难往回查到这里。
dd if="$VIDEO" bs=1024 count=1 of="$WORKDIR/head_ref.bin" 2>/dev/null
cmp -s "$WORKDIR/head.bin" "$WORKDIR/head_ref.bin" \
    || die 15 "Range 返回的字节与全量文件的前 1024 字节对不上"
ok "VS-11：206 + Content-Range 正确 + 字节逐字节相同"

# ⑤ 越界 Range —— **只记录不断言**。Starlette 在这种情况下的行为没有验证过，
# 把没验证过的行为写成断言，等于把一个猜测伪装成判据。
OOR=$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' -r 999999999- \
    "$GATEWAY_URL/v1/jobs/$JOB1/video" || echo 000)
echo "  · 越界 Range 返回 $OOR（仅记录，不作判据）"

# ═════════════════ P6：产物内容（服务器上 ffprobe）═════════════════
step "P6 在服务器上 ffprobe 断言产物内容"

PROBE_RAW=$(ssh "$SSH_HOST" bash -s <<REMOTE
set -euo pipefail
f="$REMOTE_PATH"
[[ -f "\$f" ]] || { echo "产物不在服务器上: \$f" >&2; exit 1; }
# 哨兵行：ssh 的 stdout 不保证干净，一行提示就能让 jq 报一个看着像 ffprobe 坏了的错。
echo "===FFPROBE_JSON==="
ffprobe -v error -print_format json -show_format -show_streams "\$f"
REMOTE
) || die 15 "远程 ffprobe 失败"

PROBE=$(printf '%s' "$PROBE_RAW" | awk '/^===FFPROBE_JSON===$/{f=1;next} f')
echo "$PROBE" > "$WORKDIR/ffprobe.json"

V_CODEC=$(printf '%s' "$PROBE" | jq -r '.streams[] | select(.codec_type=="video") | .codec_name')
A_CODEC=$(printf '%s' "$PROBE" | jq -r '.streams[] | select(.codec_type=="audio") | .codec_name')
V_W=$(printf '%s' "$PROBE" | jq -r '.streams[] | select(.codec_type=="video") | .width')
V_H=$(printf '%s' "$PROBE" | jq -r '.streams[] | select(.codec_type=="video") | .height')
FORMAT=$(printf '%s' "$PROBE" | jq -r '.format.format_name')
DURATION=$(printf '%s' "$PROBE" | jq -r '.format.duration')

[[ "$V_CODEC" == "h264" ]] || die 15 "视频编码是 $V_CODEC，期望 h264"
[[ "$A_CODEC" == "aac" ]]  || die 15 "音频编码是 $A_CODEC，期望 aac"
[[ "$FORMAT" == *mp4* ]]   || die 15 "容器是 $FORMAT，期望含 mp4"

# ⭐ 宽高不硬编码，而是和**网关自己宣称的 normalized** 对照。
# 硬编码 736x416 等于把「用哪张测试图」偷偷变成判据的一部分 —— 换张图就红得莫名其妙。
# 跨层一致性（网关说的 = ComfyUI 做的）比一个常数有信息量得多。
[[ "$V_W" == "$NORM_W" && "$V_H" == "$NORM_H" ]] \
    || die 15 "产物是 ${V_W}x${V_H}，而网关宣称的是 ${NORM_W}x${NORM_H} —— 两层对不上"

awk "BEGIN{exit !(($DURATION - $NORM_DUR) < 0.1 && ($NORM_DUR - $DURATION) < 0.1)}" \
    || die 15 "产物时长 $DURATION 秒，与网关宣称的 $NORM_DUR 秒差太多"

ok "VS-6：$V_CODEC + $A_CODEC + $FORMAT，${V_W}x${V_H}（= normalized），$DURATION 秒"

# ═════════════════ P7：中断验证（VS-20）═════════════════
#
# ⚠️ **这是本轮唯一一条可能推翻代码假设的验证。**
# core/jobs.py 的 _poll_until_finished 里那条
# 「被请求取消 + 记录已完成 + status 不是 success ⇒ 落 cancelled」
# 是在**没有任何真实证据**的情况下写的。
#
# 所以这一步的主要产出是那份原始记录，不是一个绿勾。
VS20="跳过"
CANCEL_STATUS=""
if [[ $DO_CANCEL == 1 ]]; then
    step "P7 中断验证：提交一个任务，进 running 后取消它"

    BODY2=$(jq -n \
        '{type:"h3_video", mode:"T2VA",
          prompt:{description:"[grokgen-smoke] cancel probe, a quiet empty room",
                  soundscape:"N/A", music:"N/A"},
          duration_seconds:5.0, turbo:true}')
    SUBMIT2=$(_http -X POST -H 'Content-Type: application/json' -d "$BODY2" "$GATEWAY_URL/v1/jobs")
    JOB2=$(printf '%s' "$SUBMIT2" | jget '.job_id')
    ok "$JOB2 已排队"

    poll_job "$JOB2" "$TIMEOUT_WARM" 1 || warn "任务 $JOB2 轮询超时，最后停在 $FINAL_STATE"
    JOB2_PROMPT="$PROMPT_ID"
    JOB2_STATES="${STATES[*]}"

    if [[ "$FINAL_STATE" == "cancelled" ]]; then
        ok "网关终态是 cancelled"
    else
        warn "网关终态是 $FINAL_STATE，不是 cancelled —— 见下面的原始记录"
    fi

    # 把 ComfyUI 那条记录原样取回来。这才是这一步的产出。
    if [[ -n "$JOB2_PROMPT" ]]; then
        RECORD=$(history_record "$JOB2_PROMPT")
        if [[ "$RECORD" == "null" ]]; then
            CANCEL_STATUS="/history 里根本没有这条记录"
            VS20="⚠️ 需要改代码"
            echo ""
            echo "  ⚠️ ComfyUI 的 /history 里没有 $JOB2_PROMPT 这条记录。"
            echo "     这意味着 _poll_until_finished 会一直轮询下去 ——"
            echo "     而 job_timeout_seconds 默认是 None，即**永远转下去**。"
            echo "     这一条要连着改超时默认值。"
        else
            CANCEL_STATUS=$(printf '%s' "$RECORD" | jq -r '.status.status_str // "（没有 status_str）"')
            echo ""
            echo "  ── ComfyUI /history 里那条记录 ──"
            echo "     status_str = $CANCEL_STATUS"
            echo "     messages   = $(printf '%s' "$RECORD" | jq -c '.status.messages // []' | head -c 400)"
            echo "     原始响应留档：$WORKDIR/history_$JOB2_PROMPT.json"
            if [[ "$CANCEL_STATUS" == "success" ]]; then
                VS20="⚠️ 代码判定是错的"
                echo ""
                echo "     ⚠️ status_str 是 success —— core/jobs.py 里"
                echo "        「status != success ⇒ 取消」这条判定不成立，取消会被误判成完成。"
            else
                VS20="通过（status_str=$CANCEL_STATUS）"
            fi
        fi
    else
        CANCEL_STATUS="没抓到 prompt_id"
        VS20="⚠️ 没拿到证据"
        warn "整个过程没从 ComfyUI 的队列里抓到 prompt_id，拿不到那条记录"
    fi
fi

# ═════════════════ P8：汇总 ═════════════════
VRAM_END=$(comfy /system_stats | jq -r '.devices[0].vram_free')

cat <<REPORT

════════════════════ 冒烟结果 ════════════════════
网关 $GW_REV   ComfyUI 空闲   模式：$GEN_KIND
显存 vram_free：跑前 $(fmt_gb "$VRAM_START") → 跑后 $(fmt_gb "$VRAM_END")（总量 $(fmt_gb "$VRAM_TOTAL")）

生成 #1  $JOB1
  转移：$GEN1_STATES
  耗时：${GEN1_ELAPSED} 秒   ← $GEN_KIND
  产物：$OUT_SUB/$OUT_FILE（$REMOTE_SIZE 字节）
  画布：${NORM_W}x${NORM_H}、$NORM_FRAMES 帧、$NORM_DUR 秒、seed=$NORM_SEED

  VS-19  running 真实可达        $VS19
  VS-11  Range                   通过（206 / $EXPECT_CR / 字节逐字节相同）
  VS-6   格式（服务器 ffprobe）  通过（$V_CODEC + $A_CODEC + $FORMAT，${V_W}x${V_H}，$DURATION 秒）
REPORT

if [[ $DO_CANCEL == 1 ]]; then
cat <<REPORT
生成 #2  ${JOB2:-?}（中断验证）
  转移：${JOB2_STATES:-?}
  VS-20  中断后的记录            $VS20
         ComfyUI status_str = ${CANCEL_STATUS:-?}
REPORT
fi

cat <<REPORT

⚠️ 脚本判不了的（Level 4，必须人眼看）：
  VS-7  画面不是黑屏或噪声      播放 $VIDEO
  VS-8  首帧认得出是那张图      对照 $TEST_IMAGE（品红方块在左上、青色在右下、黄黑对角条纹）

服务器上留下的（**不自动清理**）：
  output/$OUT_SUB/$OUT_FILE      ← 保留，它是 VS-7/VS-8 的唯一材料
  input/$ASSET_ID                 ← 上传的首帧图，会一直堆积（M3 媒体库一起处理）
  下载件与全部 JSON 留档：$WORKDIR
══════════════════════════════════════════════════

REPORT

# ⚠️ 成功也保留现场：报告里引用了 $VIDEO 和 $TEST_IMAGE，人眼判定还要用。
echo "（现场保留在 $WORKDIR，人眼判定做完后可自行删除）"
SUCCESS=0
exit 0
