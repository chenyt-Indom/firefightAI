#!/system/bin/sh
# 验证 sendevent 是否被 input 子系统接收

EV_LOG="/data/local/tmp/ev_verify.txt"
rm -f "$EV_LOG"

# 后台监听触控事件
getevent -lt /dev/input/event4 > "$EV_LOG" 2>&1 &
GPID=$!

# 等待监听启动
sleep 1

# 注入一个 tap 事件
sendevent /dev/input/event4 0003 002f 0
sendevent /dev/input/event4 0003 0039 11111
sendevent /dev/input/event4 0003 0035 540
sendevent /dev/input/event4 0003 0036 960
sendevent /dev/input/event4 0001 014a 1
sendevent /dev/input/event4 0000 0000 0
sleep 0.1
sendevent /dev/input/event4 0003 002f 0
sendevent /dev/input/event4 0003 0039 -1
sendevent /dev/input/event4 0001 014a 0
sendevent /dev/input/event4 0000 0000 0

sleep 1
kill $GPID 2>/dev/null
wait $GPID 2>/dev/null

echo "=== 捕获到的触控事件 ==="
cat "$EV_LOG" | tail -20
