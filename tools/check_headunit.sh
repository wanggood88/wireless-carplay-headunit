#!/usr/bin/env bash
# 车机诊断采集（只读）。用法：bash 一键诊断.sh
set -u
OUT=headunit_report.txt
sec() { printf '\n########## %s\n' "$1" >> "$OUT"; }

if ! command -v adb >/dev/null 2>&1; then
  echo "[错误] 找不到 adb，请先安装 Android platform-tools"; exit 1
fi
echo "==== 连接状态 ===="; adb devices
if ! adb get-state >/dev/null 2>&1; then
  echo "[错误] 没有设备，先 adb connect <车机IP>:5555"; exit 1
fi

: > "$OUT"
sec "1. BUILD"
adb shell 'getprop ro.build.version.release; getprop ro.build.version.sdk; getprop ro.product.model; getprop ro.build.fingerprint; getprop ro.build.type; getprop ro.debuggable' >> "$OUT" 2>&1

sec "2. CAR PROPS"
adb shell getprop >> "$OUT" 2>&1

sec "3. PACKAGES"
adb shell pm list packages >> "$OUT" 2>&1

sec "4. IAP2 SERVICE DUMP"
adb shell dumpsys package com.amwin.iap2 >> "$OUT" 2>&1

sec "5. NATIVE LIBS"
adb shell 'ls -l /system/lib64/ /system/lib/' >> "$OUT" 2>&1

sec "6. NATIVE WIRELESS SYMBOLS (carplay)"
adb shell "strings /system/lib64/libcarplay_jni.so 2>/dev/null | grep -i -E 'raop|airplay|mdns|bonjour|awdl|wifi|wlan|ncm|eth0|getifaddrs|ifaddrs' | sort -u" >> "$OUT" 2>&1

sec "7. PROCESSES"
adb shell ps -A >> "$OUT" 2>&1

sec "8. WLAN / HOTSPOT"
adb shell 'ip -4 addr show wlan0; ip -6 addr show wlan0; cat /proc/sys/net/ipv6/conf/wlan0/disable_ipv6' >> "$OUT" 2>&1

sec "9. LISTENING PORTS"
adb shell netstat -tuln >> "$OUT" 2>&1

sec "10. BLUETOOTH"
adb shell dumpsys bluetooth_manager >> "$OUT" 2>&1

echo
echo "采集完成：$OUT"
read -r -p "现在做零 root 试连？(先确认：热点已开=车机当AP，iPhone已连热点，蓝牙已连) [y/N] " ans
case "$ans" in
  y|Y)
    sec "11. TRIAL broadcast connect.bluetooth"
    adb logcat -c
    adb shell am broadcast -a com.amwin.iap2.IAP2Service.connect.bluetooth >> "$OUT" 2>&1
    echo "等 12 秒..."; sleep 12
    adb logcat -d -s CarPlayService WiFiCarPlayBusiness CarPlayServer >> "$OUT" 2>&1
    echo "试连日志已写入 $OUT"
    ;;
  *) echo "已跳过试连。" ;;
esac
