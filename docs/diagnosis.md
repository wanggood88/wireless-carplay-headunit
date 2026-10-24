# 上车前诊断清单（只读，不改任何东西）

目的：在刷包/注入之前，先确认三件事——
**① 热点是"车机当 AP"还是"车机连手机热点"；② 原生库支不支持 WiFi 通道；③ IPv6 通不通。**
第 ② 条不过，Java 层怎么改都是白费。

---

## A. 三条命令，先出结论

连上车机 adb（`adb devices` 能看到设备）后执行：

```bash
# ① 原生库位置和大小（确认存在）
adb shell ls -l /system/lib*/libcarplay_jni.so

# ② 关键：原生库里有没有"网络/无线 CarPlay"的痕迹
adb shell "strings /system/lib64/libcarplay_jni.so 2>/dev/null || strings /system/lib/libcarplay_jni.so" \
  | grep -i -E "raop|_airplay|_raop|mdns|bonjour|awdl|wifi|wlan|eth0|ncm" | sort -u
```

**怎么读 ② 的结果：**

| 看到的东西 | 含义 |
|---|---|
| `_airplay._tcp` / `_raop._tcp` / `mdns` / `bonjour` | 原生层自己做 mDNS 播发 → **无线通道大概率编进去了**，可以往下走 |
| `awdl` | 苹果的 AirDrop/WiFi-Direct 通道（一般车机没有，不重要） |
| 只有 `ncm` / `usb` / `iap2`，完全没有 mdns/airplay 服务名 | 原生层只做了 USB-NCM 通道 → **无线没戏**，别折腾了 |
| 命令报 Permission denied | 说明 /system 读不了，先 `adb root` 再试 |

```bash
# ③ 热点开起来之后，看 wlan0 的地址和 IPv6
adb shell ip -4 addr show wlan0
adb shell ip -6 addr show wlan0
adb shell "cat /proc/sys/net/ipv6/conf/wlan0/disable_ipv6"
```

**怎么读 ③ 的结果：**
- IPv4 有 `192.168.43.1/24` 之类 → 标准 Android 软 AP，正常。
- IPv6 只有 `fe80::` 链路本地、且 `disable_ipv6=0` → 勉强可以（无线 CarPlay 走 IPv6 链路本地也能建链）。
- `disable_ipv6=1` 或压根没有 IPv6 地址 → **这是最常见的失败点**：iPhone 能连上热点，但 CarPlay 永远起不来。需要给 hostapd/dnsmasq 开 IPv6（要 root）。

---

## B. 完整采集（一条命令，输出可直接贴回来）

```bash
adb shell <<'EOF'
echo "===== 1. 系统 ====="
getprop ro.build.version.release; getprop ro.build.version.sdk; getprop ro.product.model
getprop ro.build.fingerprint
echo "===== 2. 车机模式 ====="
getprop | grep -i -E "carmode|car_mode|amwin|carplay"
echo "===== 3. 原生库 ====="
ls -l /system/lib*/libcarplay_jni.so /system/lib*/libcarlifevehicle.so 2>/dev/null
echo "===== 4. 相关进程 ====="
ps -A | grep -i -E "amwin|iap2|mdns|carplay|carlife|nforetek"
echo "===== 5. 热点状态 ====="
ip -4 addr show wlan0; ip -6 addr show wlan0
getprop | grep -i -E "wifi|tether|softap"
echo "===== 6. 监听端口（AirPlay 用 7000/7001/7100 等）====="
netstat -tuln 2>/dev/null | grep -E ":7000|:7001|:7100|:5353|:49152"
echo "===== 7. 蓝牙 ====="
dumpsys bluetooth_manager 2>/dev/null | grep -i -E "state|bonded|connected" | head -20
EOF
```

---

## C. 现象 → 原因 对照表

| 现象 | 最可能的原因 | 能不能救 |
|---|---|---|
| 手机连得上热点，但 CarPlay 一点反应没有 | 热点是"车机连手机热点"模式，不是车机当 AP | 不能，无线 CarPlay 要求车机是 AP |
| 补丁刷了，日志里没有 `init WIFI` | 补丁没生效（包没换成 / 服务没重启） | 能，重启车机或 `am force-stop` 后再试 |
| 有 `init WIFI`，但没有 `setupBluetoothDevice` | 热点没开，或蓝牙 HFP 没连上 | 能，先配对蓝牙，再开热点 |
| 有 `setupBluetoothDevice address:`，反复重试 5 秒 | 手机没连热点 / 热点无 IPv6 / iOS 没识别成 CarPlay 设备 | 先查 ③ 的 IPv6 |
| 有 `**Bluetooth connected**` 但没有 `Start iap2!!` | 原生库没编 WiFi 通道（回到 A-② 判断） | 只能换固件里的 lib |
| AirPlay 端口 7000/7001 没在监听 | 服务没起，或只绑定在 USB 网卡上 | 看 native 日志 |

---

## D. 两种"能开热点"的区别（很关键）

| 模式 | 描述 | 无线 CarPlay |
|---|---|---|
| 车机当 AP（热点名是车机自己的，如 `Chery-XXXX`） | 车机开热点，手机连车机 | ✅ 唯一可行 |
| 车机连手机热点（车机设置里"连接 WiFi"） | 手机开热点，车机当客户端 | ❌ 不可能，同一部 iPhone 没法既发热点又当 CarPlay 源 |

判断方法：手机连上车机的热点后，`adb shell ip -4 addr show wlan0` 里车机自己是 `192.168.x.1`（网关）→ 车机是 AP；车机自己是 `192.168.x.10x`（客户端）→ 车机是 STA，走不通。

---

## E. 没有 root 的前提下，实际能做什么

| 能力 | 没有 root | 说明 |
|---|---|---|
| 改系统 App / 刷补丁包 | ❌ | 写不了 `/system`，签名也过不了 |
| 跑 Frida / LSPosed | ❌ | frida-server 必须 root 起，普通 shell 无法注入 system 进程 |
| 读 `/system/lib` 里的原生库 | ✅ | 诊断的关键手段 |
| `adb shell am broadcast` 发广播 | ✅ | **零 root 唯一的杠杆** |
| `dumpsys` 看服务是否 exported | ✅ | 决定能不能走"助手 App"方案 |
| 看 logcat | ✅ | 无线调试下最好用 |

### E.1 零 root 试连流程（4 步，别跳）

前提：`libcarplay_jni.so` 的字符串检测（A-②）里出现了 `_airplay._tcp` / `mdns` / `bonjour` 之类。没出现就别往下做。

```bash
# 1. 车机开热点（车机当 AP），iPhone 连上这个热点；iPhone 同时与车机蓝牙配对并连上
#    （放首歌确认 HFP 通，控制台能看到媒体信息就算通）

# 2. 清空日志
adb logcat -c

# 3. 触发 IAP2 的蓝牙连接（零 root 唯一能碰到的入口）
adb shell am broadcast -a com.amwin.iap2.IAP2Service.connect.bluetooth

# 4. 等 10 秒，看结果
adb logcat -d -s CarPlayService WiFiCarPlayBusiness CarPlayServer IAP2
```

**三种结果对应三种命运：**

| 现象 | 含义 | 还有救吗 |
|---|---|---|
| `SecurityException: Permission Denial ... not exported from uid` | IAP2 服务**存在**这个入口，但普通 shell 没权限进去 | 有救 → 见 E.2 |
| `Broadcast completed: result=0`，日志毫无变化 | 这个 receiver 根本不存在（常量只是"预留"） | 无救 → 走盒子 |
| 日志出现 `**Bluetooth connected**` / `Start iap2!!` | 链路被点着了 | 成了，看画面 |

### E.2 如果 IAP2 服务是 exported（这是唯一的零 root 突破口）

```bash
adb shell pm list packages | grep -i iap2
adb shell dumpsys package <上面查到的包名> | grep -i -E "Service|exported|permission"
```

如果看到 `exported=true` 且没有 `android:permission` 保护，就可以写一个**普通 App**（无需系统签名、无需 root）去 bind 这个服务，然后调用 `connectBluetoothDevice(手机蓝牙地址)` —— 效果和厂商自己触发一样。这个 App 我可以直接给你写出来（AIDL 桩就在 CarPlay 包里，`com.amwin.iap2.common.IIAP2Service`）。

如果 `exported=false`，这条路就断了，剩下的只有盒子。
