#!/bin/bash
set -e
cd "$(dirname "$0")"
# Stop only this project's service; never kill an unrelated process on this port.
PIDS=$(lsof -tiTCP:8092 -sTCP:LISTEN || true)
for pid in $PIDS; do
    process_dir=$(lsof -a -p "$pid" -d cwd -Fn | sed -n 's/^n//p')
    process_args=$(ps -p "$pid" -o args=)
    if [ "$process_dir" != "$PWD" ] || [[ "$process_args" != *app_v2.py* && "$process_args" != *app.py* ]]; then
        echo "端口 8092 被其他服务占用，未终止。PID: $pid"
        exit 1
    fi
    kill -TERM "$pid"
done
for attempt in {1..50}; do
    if ! lsof -tiTCP:8092 -sTCP:LISTEN >/dev/null; then break; fi
    sleep .2
done
if lsof -tiTCP:8092 -sTCP:LISTEN >/dev/null; then
    echo '旧服务仍在退出，请稍后重试；未强制中断。'
    exit 1
fi
nohup ./start.sh > workbench.log 2>&1 < /dev/null &
for attempt in {1..40}; do
    if curl -fsS http://127.0.0.1:8092/api/config > /dev/null 2>&1; then
        echo '文档工作台已启动：http://127.0.0.1:8092'
        exit 0
    fi
    sleep .25
done
echo '启动失败，请查看 workbench.log'
exit 1
