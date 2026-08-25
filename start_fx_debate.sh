#!/usr/bin/env bash
set -Eeuo pipefail

# One-command launcher for the FX Debate workspace.
#
# Starts, in order:
#   1. SSH local forward to the private PostgreSQL service;
#   2. MCP stdio initialize/list-tools preflight;
#   3. AI Search HTTP API;
#   4. FX Debate API;
#   5. FX Debate frontend.
#
# Existing listeners on the configured application ports are stopped before a
# new run. SSH authentication remains interactive; no password is stored here.
#
# Usage:
#   ./start_fx_debate.sh
#   FRONTEND_PORT=5899 ./start_fx_debate.sh
#   ./start_fx_debate.sh stop
#   ./start_fx_debate.sh status

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
VIBE_PYTHON="${VIBE_PYTHON:-$PROJECT_DIR/Vibe-Trading/.venv/bin/python}"
FRONTEND_DIR="$SCRIPT_DIR/frontend-fx-debate"
AI_SEARCH_DIR="$SCRIPT_DIR/external/market-data-tools"
AI_SEARCH_FRONTEND_DIR="$AI_SEARCH_DIR/front"

API_PORT="${API_PORT:-8899}"
FRONTEND_PORT="${FRONTEND_PORT:-5898}"
AI_SEARCH_PORT="${AI_SEARCH_PORT:-8011}"
AI_SEARCH_FRONTEND_PORT="${AI_SEARCH_FRONTEND_PORT:-5173}"
AI_SEARCH_STARTUP_ATTEMPTS="${AI_SEARCH_STARTUP_ATTEMPTS:-60}"
API_STARTUP_ATTEMPTS="${API_STARTUP_ATTEMPTS:-180}"
FRONTEND_STARTUP_ATTEMPTS="${FRONTEND_STARTUP_ATTEMPTS:-60}"
MCP_PREFLIGHT_TIMEOUT_SECONDS="${MCP_PREFLIGHT_TIMEOUT_SECONDS:-60}"
MCP_REQUIRED="${MCP_REQUIRED:-1}"
MCP_SMOKE_QUERY="${MCP_SMOKE_QUERY:-查询 EURUSD 最近一个月的日线行情}"

SSH_ENABLED="${SSH_ENABLED:-1}"
SSH_USER="${SSH_USER:-root}"
SSH_HOST="${SSH_HOST:-101.35.55.7}"
SSH_PORT="${SSH_PORT:-22}"
DB_LOCAL_PORT="${DB_LOCAL_PORT:-15433}"
DB_REMOTE_PORT="${DB_REMOTE_PORT:-5433}"
SSH_IDENTITY_FILE="${SSH_IDENTITY_FILE:-}"

# The AI Search UI is a diagnostics workbench, not a dependency of FX Debate.
# Set START_AI_SEARCH_FRONTEND=1 when that extra UI is needed.
START_AI_SEARCH_FRONTEND="${START_AI_SEARCH_FRONTEND:-0}"

RUNTIME_DIR="${RUNTIME_DIR:-$SCRIPT_DIR/.runtime}"
LOG_DIR="$RUNTIME_DIR/logs"
mkdir -p "$LOG_DIR"

usage() {
  cat <<EOF
用法：
  $0                 停止旧服务并启动 SSH、MCP、AI Search、FX API 和 FX 前端
  $0 stop            停止本脚本管理的服务
  $0 status          查看配置端口和监听状态
  $0 version         查看当前实际启动代码的分支、提交和本地改动状态

可覆盖环境变量：
  FRONTEND_PORT=5899       FX 前端端口（默认 5898）
  API_PORT=8899            FX API 端口
  AI_SEARCH_PORT=8011      AI Search HTTP API 端口
  SSH_USER=root            SSH 用户名
  SSH_HOST=101.35.55.7     SSH 主机
  SSH_ENABLED=0            跳过 SSH 隧道
  MCP_PREFLIGHT_TIMEOUT_SECONDS=60  MCP 启动握手和数据烟测超时（秒）
  MCP_SMOKE_QUERY=...       MCP 启动烟测查询（默认：EURUSD 最近一个月日线行情）
  MCP_REQUIRED=0             允许回退到 Excel/其他数据源（默认 1，强制 MCP）
  START_AI_SEARCH_FRONTEND=1  同时启动 AI Search 调试前端（默认关闭）
EOF
}

print_code_version() {
  local branch="unknown"
  local revision="unknown"
  local worktree="unknown"
  if command -v git >/dev/null 2>&1 && [[ -d "$SCRIPT_DIR/.git" ]]; then
    branch="$(git -C "$SCRIPT_DIR" branch --show-current 2>/dev/null || echo unknown)"
    revision="$(git -C "$SCRIPT_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    if git -C "$SCRIPT_DIR" diff --quiet -- 2>/dev/null && git -C "$SCRIPT_DIR" diff --cached --quiet -- 2>/dev/null; then
      worktree="clean"
    else
      worktree="有本地改动"
    fi
  fi
  echo "FX Debate 代码版本：${branch} @ ${revision}（${worktree}）"
}

is_listening() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

wait_for_port() {
  local port="$1"
  local attempts="${2:-40}"
  for ((i = 1; i <= attempts; i++)); do
    if is_listening "$port"; then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

process_command() {
  ps -p "$1" -o command= 2>/dev/null || true
}

kill_pid() {
  local pid="$1"
  local label="$2"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 0
  if ! kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  echo "停止 ${label}（PID ${pid}）"
  kill "$pid" 2>/dev/null || true
  for ((i = 1; i <= 20; i++)); do
    if ! kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
    sleep 0.25
  done
  echo "$label 未及时退出，发送 SIGKILL"
  kill -9 "$pid" 2>/dev/null || true
}

stop_pid_file() {
  local file="$1"
  local label="$2"
  local token="$3"
  [[ -f "$file" ]] || return 0
  local pid
  pid="$(tr -d '[:space:]' < "$file")"
  local command
  command="$(process_command "$pid")"
  if [[ -n "$command" && "$command" == *"$token"* ]]; then
    kill_pid "$pid" "$label"
  elif [[ -n "$command" ]]; then
    echo "跳过 $label 的陈旧 PID 文件（PID $pid 不是目标进程）"
  fi
  rm -f "$file"
}

stop_listeners_on_port() {
  local port="$1"
  local label="$2"
  local ssh_only="${3:-0}"
  local expected_token="${4:-}"
  local pids
  pids="$(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  [[ -n "$pids" ]] || return 0
  while IFS= read -r pid; do
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    local command
    command="$(process_command "$pid")"
    if [[ "$ssh_only" == "1" && "$command" != *"ssh"* ]]; then
      echo "端口 $port 被非 SSH 进程占用，保留该进程：$command" >&2
      continue
    fi
    if [[ -n "$expected_token" && "$command" != *"$expected_token"* ]]; then
      echo "端口 $port 被非 $label 进程占用，保留该进程：$command" >&2
      continue
    fi
    kill_pid "$pid" "${label}（端口 ${port}）"
  done <<< "$pids"
}

stop_all() {
  # PID files cover processes started by this script and the previous launcher.
  stop_pid_file "$RUNTIME_DIR/ai-search.pid" "AI Search" "backend.main:app"
  stop_pid_file "$RUNTIME_DIR/ai-search-frontend.pid" "AI Search 前端" "market-data-tools"
  stop_pid_file "$RUNTIME_DIR/api.pid" "FX API" "api_server:app"
  stop_pid_file "$RUNTIME_DIR/frontend.pid" "FX 前端" "frontend-fx-debate"
  stop_pid_file "$RUNTIME_DIR/ssh.pid" "SSH 隧道" "ssh"

  # Port fallback also catches processes started by older scripts without PID files.
  stop_listeners_on_port "$AI_SEARCH_PORT" "AI Search" 0 "market-data-tools"
  stop_listeners_on_port "$AI_SEARCH_FRONTEND_PORT" "AI Search 前端" 0 "market-data-tools"
  stop_listeners_on_port "$API_PORT" "FX API" 0 "api_server:app"
  stop_listeners_on_port "$FRONTEND_PORT" "FX 前端" 0 "frontend-fx-debate"
  stop_listeners_on_port "$DB_LOCAL_PORT" "SSH 隧道" 1
}

load_runtime_env() {
  if [[ ! -f "$SCRIPT_DIR/agent/.env" ]]; then
    echo "错误：找不到 FX API 配置文件：$SCRIPT_DIR/agent/.env" >&2
    return 1
  fi
  if [[ ! -f "$AI_SEARCH_DIR/.env" ]]; then
    echo "错误：找不到 AI Search 配置文件：$AI_SEARCH_DIR/.env" >&2
    return 1
  fi

  # Both services keep secrets in their own ignored .env files. The AI Search
  # copy can reuse the database credentials already used by the FX API without
  # duplicating the password into another project file.
  set -a
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/agent/.env"
  # shellcheck disable=SC1091
  source "$AI_SEARCH_DIR/.env"
  set +a

  export AI_SEARCH_DB_HOST="${AI_SEARCH_DB_HOST:-${MARKET_DB_HOST:-127.0.0.1}}"
  export AI_SEARCH_DB_PORT="${AI_SEARCH_DB_PORT:-${MARKET_DB_PORT:-$DB_LOCAL_PORT}}"
  export AI_SEARCH_DB_NAME="${AI_SEARCH_DB_NAME:-${MARKET_DB_NAME:-icbc_shared}}"
  export AI_SEARCH_DB_USER="${AI_SEARCH_DB_USER:-${MARKET_DB_USER:-icbc_collab}}"
  export AI_SEARCH_DB_PASSWORD="${AI_SEARCH_DB_PASSWORD:-${MARKET_DB_PASSWORD:-}}"
  if [[ -z "$AI_SEARCH_DB_PASSWORD" ]]; then
    echo "错误：AI Search 未配置 AI_SEARCH_DB_PASSWORD 或 agent/.env 中的 MARKET_DB_PASSWORD" >&2
    return 1
  fi
}

validate_data_source() {
  if [[ "$MCP_REQUIRED" == "1" && "${FX_DEBATE_DATA_SOURCE:-}" != "ai_search" ]]; then
    echo "错误：当前数据源为 '${FX_DEBATE_DATA_SOURCE:-未配置}'，MCP 优先启动要求 FX_DEBATE_DATA_SOURCE=ai_search" >&2
    echo "如需明确回退到 Excel，请使用 MCP_REQUIRED=0，并确认流程日志不应出现 MCP 数据事件。" >&2
    return 1
  fi
  echo "FX Debate 数据源：${FX_DEBATE_DATA_SOURCE:-未配置}"
}

start_ssh() {
  [[ "$SSH_ENABLED" == "1" ]] || { echo "SSH 隧道：已跳过（SSH_ENABLED=0）"; return 0; }
  command -v ssh >/dev/null 2>&1 || { echo "错误：系统中找不到 ssh" >&2; return 1; }
  if is_listening "$DB_LOCAL_PORT"; then
    echo "错误：本机端口 $DB_LOCAL_PORT 仍被其他进程占用，无法建立 SSH 隧道" >&2
    return 1
  fi

  local ssh_target="$SSH_USER@$SSH_HOST"
  local ssh_args=(
    ssh
    -p "$SSH_PORT"
    -o ExitOnForwardFailure=yes
    -o ServerAliveInterval=60
    -o ServerAliveCountMax=3
    -o StrictHostKeyChecking=accept-new
    -fN
    -L "$DB_LOCAL_PORT:127.0.0.1:$DB_REMOTE_PORT"
  )
  if [[ -n "$SSH_IDENTITY_FILE" ]]; then
    ssh_args+=( -i "$SSH_IDENTITY_FILE" )
  fi
  ssh_args+=( "$ssh_target" )

  echo "建立 SSH 隧道：127.0.0.1:$DB_LOCAL_PORT -> $ssh_target:127.0.0.1:$DB_REMOTE_PORT"
  echo "如果 SSH 使用密码认证，请在这里输入密码；密码不会写入项目。"
  "${ssh_args[@]}"

  if ! wait_for_port "$DB_LOCAL_PORT" 20; then
    echo "错误：SSH 隧道未监听本机端口 $DB_LOCAL_PORT" >&2
    return 1
  fi
  local pid
  pid="$(lsof -nP -tiTCP:"$DB_LOCAL_PORT" -sTCP:LISTEN 2>/dev/null | head -n 1 || true)"
  [[ -n "$pid" ]] && printf '%s\n' "$pid" > "$RUNTIME_DIR/ssh.pid"
  echo "SSH 隧道已就绪：127.0.0.1:$DB_LOCAL_PORT"
}

preflight_mcp() {
  local preflight_log="$LOG_DIR/mcp-preflight.log"
  if [[ ! -f "$SCRIPT_DIR/agent/mcp_preflight.py" ]]; then
    echo "错误：找不到 MCP 启动检查：$SCRIPT_DIR/agent/mcp_preflight.py" >&2
    return 1
  fi

  echo "MCP 启动前置检查：初始化 stdio 并确认 unified_search..."
  if "$VIBE_PYTHON" "$SCRIPT_DIR/agent/mcp_preflight.py" \
    --python "$VIBE_PYTHON" \
    --directory "$AI_SEARCH_DIR" \
    --server-module "backend.mcp_server" \
    --timeout "$MCP_PREFLIGHT_TIMEOUT_SECONDS" \
    --smoke-query "$MCP_SMOKE_QUERY" \
    > "$preflight_log" 2>&1; then
    cat "$preflight_log"
    echo "MCP 已就绪：unified_search（查询时由 FX API 按需创建短会话）"
    return 0
  fi

  cat "$preflight_log" >&2 || true
  echo "错误：MCP 未通过启动前置检查，已阻止 AI Search、FX API 和前端继续启动；详情：$preflight_log" >&2
  return 1
}

start_ai_search() {
  if is_listening "$AI_SEARCH_PORT"; then
    echo "AI Search 已在 http://127.0.0.1:$AI_SEARCH_PORT 运行"
    return 0
  fi
  nohup "$VIBE_PYTHON" -m uvicorn \
    --app-dir "$AI_SEARCH_DIR" \
    backend.main:app \
    --host 127.0.0.1 \
    --port "$AI_SEARCH_PORT" \
    > "$LOG_DIR/ai-search.log" 2>&1 < /dev/null &
  echo $! > "$RUNTIME_DIR/ai-search.pid"
  if wait_for_port "$AI_SEARCH_PORT" "$AI_SEARCH_STARTUP_ATTEMPTS"; then
    echo "AI Search 已启动：http://127.0.0.1:$AI_SEARCH_PORT"
  else
    kill_pid "$(cat "$RUNTIME_DIR/ai-search.pid")" "AI Search"
    echo "错误：AI Search 启动失败，请查看 $LOG_DIR/ai-search.log" >&2
    return 1
  fi
}

start_ai_search_frontend() {
  [[ "$START_AI_SEARCH_FRONTEND" == "1" ]] || return 0
  if [[ ! -d "$AI_SEARCH_FRONTEND_DIR/node_modules" ]]; then
    echo "错误：AI Search 前端依赖不存在，请先执行 npm --prefix $AI_SEARCH_FRONTEND_DIR install" >&2
    return 1
  fi
  if is_listening "$AI_SEARCH_FRONTEND_PORT"; then
    echo "AI Search 前端已在 http://127.0.0.1:$AI_SEARCH_FRONTEND_PORT 运行"
    return 0
  fi
  nohup npm --prefix "$AI_SEARCH_FRONTEND_DIR" run dev -- \
    --host 127.0.0.1 \
    --port "$AI_SEARCH_FRONTEND_PORT" \
    > "$LOG_DIR/ai-search-frontend.log" 2>&1 < /dev/null &
  echo $! > "$RUNTIME_DIR/ai-search-frontend.pid"
  if wait_for_port "$AI_SEARCH_FRONTEND_PORT" "$FRONTEND_STARTUP_ATTEMPTS"; then
    echo "AI Search 前端已启动：http://127.0.0.1:$AI_SEARCH_FRONTEND_PORT"
  else
    kill_pid "$(cat "$RUNTIME_DIR/ai-search-frontend.pid")" "AI Search 前端"
    echo "错误：AI Search 前端启动失败，请查看 $LOG_DIR/ai-search-frontend.log" >&2
    return 1
  fi
}

start_api() {
  if is_listening "$API_PORT"; then
    echo "FX API 已在 http://127.0.0.1:$API_PORT 运行"
    return 0
  fi
  nohup "$VIBE_PYTHON" -m uvicorn \
    --app-dir "$SCRIPT_DIR/agent" \
    api_server:app \
    --host 127.0.0.1 \
    --port "$API_PORT" \
    > "$LOG_DIR/api.log" 2>&1 < /dev/null &
  echo $! > "$RUNTIME_DIR/api.pid"
  if wait_for_port "$API_PORT" "$API_STARTUP_ATTEMPTS"; then
    echo "FX API 已启动：http://127.0.0.1:$API_PORT"
  else
    kill_pid "$(cat "$RUNTIME_DIR/api.pid")" "FX API"
    echo "错误：FX API 启动失败，请查看 $LOG_DIR/api.log" >&2
    return 1
  fi
}

start_frontend() {
  if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
    echo "错误：FX 前端依赖不存在，请先执行 npm --prefix $FRONTEND_DIR install" >&2
    return 1
  fi
  if is_listening "$FRONTEND_PORT"; then
    echo "FX 前端已在 http://127.0.0.1:$FRONTEND_PORT 运行"
    return 0
  fi
  nohup npm --prefix "$FRONTEND_DIR" run dev -- \
    --host 127.0.0.1 \
    --port "$FRONTEND_PORT" \
    > "$LOG_DIR/frontend.log" 2>&1 < /dev/null &
  echo $! > "$RUNTIME_DIR/frontend.pid"
  if wait_for_port "$FRONTEND_PORT" "$FRONTEND_STARTUP_ATTEMPTS"; then
    echo "FX 前端已启动：http://127.0.0.1:$FRONTEND_PORT"
  else
    kill_pid "$(cat "$RUNTIME_DIR/frontend.pid")" "FX 前端"
    echo "错误：FX 前端启动失败，请查看 $LOG_DIR/frontend.log" >&2
    return 1
  fi
}

health_check() {
  command -v curl >/dev/null 2>&1 || return 0
  if curl -fsS --max-time 5 "http://127.0.0.1:$API_PORT/live" >/dev/null; then
    echo "FX API 健康检查通过"
  else
    echo "警告：FX API 端口已监听，但 /live 检查未通过；请查看 $LOG_DIR/api.log" >&2
  fi
  if curl -fsS --max-time 5 "http://127.0.0.1:$AI_SEARCH_PORT/health" >/dev/null; then
    echo "AI Search 健康检查通过（数据库状态详见 /health）"
  else
    echo "警告：AI Search 端口已监听，但 /health 检查未通过；请查看 $LOG_DIR/ai-search.log" >&2
  fi
}

status() {
  print_code_version
  local mcp_status="not-run"
  if [[ -f "$LOG_DIR/mcp-preflight.log" ]] && grep -q "MCP preflight passed: unified_search" "$LOG_DIR/mcp-preflight.log"; then
    mcp_status="passed"
  elif [[ -f "$LOG_DIR/mcp-preflight.log" ]]; then
    mcp_status="failed"
  fi
  printf 'MCP stdio preflight: %s\n' "$mcp_status"
  printf 'SSH 数据库隧道 %s: %s\n' "$DB_LOCAL_PORT" "$(is_listening "$DB_LOCAL_PORT" && echo listening || echo stopped)"
  printf 'AI Search API %s: %s\n' "$AI_SEARCH_PORT" "$(is_listening "$AI_SEARCH_PORT" && echo listening || echo stopped)"
  printf 'FX API %s: %s\n' "$API_PORT" "$(is_listening "$API_PORT" && echo listening || echo stopped)"
  printf 'FX 前端 %s: %s\n' "$FRONTEND_PORT" "$(is_listening "$FRONTEND_PORT" && echo listening || echo stopped)"
  if [[ "$START_AI_SEARCH_FRONTEND" == "1" ]]; then
    printf 'AI Search 前端 %s: %s\n' "$AI_SEARCH_FRONTEND_PORT" "$(is_listening "$AI_SEARCH_FRONTEND_PORT" && echo listening || echo stopped)"
  fi
}

start_all() {
  [[ -x "$VIBE_PYTHON" ]] || { echo "错误：找不到 Python：$VIBE_PYTHON" >&2; return 1; }
  load_runtime_env
  validate_data_source
  print_code_version
  echo "检查并关闭旧服务..."
  stop_all
  : > "$LOG_DIR/mcp-preflight.log"
  start_ssh
  if [[ "$MCP_REQUIRED" == "1" ]]; then
    if ! preflight_mcp; then
      stop_all
      return 1
    fi
  else
    echo "MCP 启动前置检查：已跳过（MCP_REQUIRED=0，允许非 MCP 数据源）"
  fi
  start_ai_search
  start_ai_search_frontend
  start_api
  start_frontend
  health_check
  echo
  echo "FX Debate 工作台已就绪：http://127.0.0.1:$FRONTEND_PORT/"
  echo "AI Search API：http://127.0.0.1:$AI_SEARCH_PORT/health"
  if [[ "$START_AI_SEARCH_FRONTEND" == "1" ]]; then
    echo "AI Search 前端：http://127.0.0.1:$AI_SEARCH_FRONTEND_PORT/"
  fi
  echo "MCP：启动前置握手已通过；由 FX API 在每次查询时按需启动短会话。"
  echo "日志目录：$LOG_DIR"
}

case "${1:-start}" in
  start)
    start_all
    ;;
  stop)
    stop_all
    ;;
  status)
    status
    ;;
  version|-v|--version)
    print_code_version
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    echo "未知命令：$1" >&2
    usage >&2
    exit 2
    ;;
esac
