# 让车机自带的 CarPlay 支持无线连接

针对 **奇瑞（Chery）车机自带的 `com.amwin.carplay.app`**（`T1A_HL1.020700.00.200214`，targetSdk 19，`sharedUserId=android.uid.system`）的逆向分析与改造方案。

> 核心结论：**无线功能是厂商自己写好的，出货时被一个编译期开关关掉了。它不需要重编译，只需要一个"扳机"。**

---

## 1. 发现了什么

`CarPlayService` 里有一行：

```java
private static final boolean IsUseWiFiCarPlay = false;
```

因为它是 `static final` 的编译期常量，`javac` 把相关的所有分支都折掉了，导致完整实现的 `com.amwin.carplay.app.wifi.WiFiCarPlayBusiness`（357 行）**从未被实例化**——启动它的唯一入口是 Handler 消息 16，而整个 DEX 里没有一处 `sendEmptyMessage(16)`。

而这个模块本身是**功能完整**的，不是残迹：

| 能力 | 实现情况 |
|---|---|
| 扫描 iPhone 的 CarPlay 蓝牙 UUID `1a29eaab-0173-bc88-1c45-4de166248d2d` | ✅ |
| 扫描 IAP2 UUID `fecacade-afde-cade-defa-cade00000000` | ✅ |
| 监听 `WIFI_AP_STATE_CHANGED`（13=热点已开 / 11=已关） | ✅ |
| `connectBluetoothDevice(addr)` 建立 IAP2 over BT | ✅ |
| 失败后 5 秒重试、配对设备落盘 `carplay.dev` | ✅ |

链路也是通的：

```
蓝牙连上 → IAP2 connectNotify(4) → initCarPlayServer(1)   ← 1 就是 CONNECT_WIFI
        → SystemProperties.set("ctl.start","mdnsd")
        → new CarPlayServer(...) → AirPlay 服务监听
        → kAirPlayHandleStartIap2(6) → mIsCarPlayWifiConnected = true
```

有线走的是同一个函数，只是参数是 `0`（`CONNECT_USB`）。**也就是说：只要 IAP2 通过蓝牙连上手机，原版 App 自己就会把无线服务拉起来。**

---

## 2. 为什么"重编译覆盖"和"共存版"都不行

| 方案 | 结果 | 原因 |
|---|---|---|
| 重编译 + 自签名覆盖 | ❌ 装不上 | 平台签名（NXP/Freescale i.MX BSP 的 `platform` 证书）+ `sharedUserId=android.uid.system`，换签名直接 `INSTALL_FAILED_SHARED_USER_INCOMPATIBLE` |
| 去掉 `sharedUserId` 强装 | ❌ 白屏/闪退 | 它不是独立应用，而是四个签名级系统服务的前端：`com.amwin.iap2`（IAP2）、`noforte` 蓝牙栈、`car_power_service` / `MicManager` 等车厂服务、`libcarplay_jni`（原生 AirPlay，在 `/system/lib` 里）。换个身份一个都连不上 |
| 改包名做共存版 | ❌ 且无意义 | 同上；即便能启动，原版 App 仍在跑，两个 AirPlay 服务会互撞 |
| **不改包，只"扳机"** | ✅ | 原版 App 以 system uid 运行，用现成的方法把 IAP2 连上手机即可 |

**这是本项目最重要的一句话：不需要新 APK，需要一个触发器。**

---

## 3. 三条路径（按可行性排序）

### A. 零 root：adb 广播触发（先试这个，5 分钟）

```bash
adb shell am broadcast -a com.amwin.iap2.IAP2Service.connect.bluetooth
```

IAP2 服务预留了 `connect.bluetooth / start.bluetooth / stop.bluetooth` 三个 action（在 CarPlay 包的 `WiFiCarPlayBusiness` 中声明但未使用，显然是给 IAP2 服务端接收的）。是否注册了 receiver、是否 exported，需要实测。

### B. 零 root：助手 App 绑定 IAP2（A 失败时）

如果 `dumpsys package com.amwin.iap2` 显示目标服务 `exported=true` 且无 `android:permission` 保护，可以写一个普通 App，用仓库里的 AIDL 桩（`com.amwin.iap2.common.IIAP2Service`）bind 上去，调用 `connectBluetoothDevice(手机蓝牙地址)`。

### C. 有 root：Frida 直接调方法

```bash
adb push frida-server /data/local/tmp/
adb shell "su -c 'chmod 755 /data/local/tmp/frida-server'"
adb shell "su -c '/data/local/tmp/frida-server &'"
frida -U -p $(adb shell ps -A | grep amwin.carplay | awk '{print $2}') \
      -l tools/frida_wireless_carplay.js
```

脚本在 `CarPlayService.onCreate()` 之后补调 `WiFiCarPlayBusiness.init(ctx)`，并挂上蓝牙 HFP 回调与 15 秒兜底重试。

### D. 有平台私钥：重签补丁包

如果从车机固件里能找到 `platform.pk8` / `platform.x509.pem`（NXP BSP 在 `device/fsl/common/security/`），用 `tools/patch_carplay_wireless.py` 生成补丁包后重签，即可 `adb install -r` 直接覆盖，系统 uid 全部保留。

---

## 4. 前置条件（不满足就别折腾）

1. **车机必须能当 AP**（自己发热点，手机连车机）。"车机连手机热点"模式不成立——同一部 iPhone 不能既发热点又当 CarPlay 源。
2. **原生库必须编了无线通道**：
   ```bash
   adb shell "strings /system/lib64/libcarplay_jni.so | grep -i -E 'raop|airplay|mdns|bonjour|ncm' | sort -u"
   ```
   看不到 `_airplay._tcp` / `mdns` / `bonjour` 就说明只实现了 USB-NCM 通道，Java 层再对也没用。
3. **热点要有 IPv6**（无线 CarPlay 走 IPv6）。`/proc/sys/net/ipv6/conf/wlan0/disable_ipv6` 为 `1` 是最常见的失败点。

---

## 5. 工具

| 文件 | 用途 |
|---|---|
| `tools/check_headunit.sh` / `.bat` | 一键采集诊断（固件、`ro.debuggable`、IAP2 服务 exported、原生库符号、热点 IPv4/IPv6、监听端口、蓝牙状态），可选顺带做一次广播试连 |
| `tools/patch_carplay_wireless.py` | 从原包生成"补丁版 APK"（只替换 `classes.dex`，资源清单原样），需自备 apktool / apk-signer |
| `tools/frida_wireless_carplay.js` | 不改包的 root 方案 |
| `docs/analysis.md` | 完整逆向分析（证据链、调用流程、签名问题） |
| `docs/diagnosis.md` | 诊断清单，含"现象 → 原因"对照表 |

---

## 6. 关于本仓库不含 APK

厂商 APK 属于车厂/供应商的专有二进制（内含 `com.oem.frameworks`、`noforte` 蓝牙栈等第三方代码），不适合公开转发。

**正确用法**：用 `adb pull` 从你自己的车机上取出原包，跑 `tools/patch_carplay_wireless.py` 生成补丁包——脚本只改 `.dex`，其余字节原样保留。

---

## 7. 免责

- 仅用于自己拥有的设备的互操作性研究。
- 替换系统应用有风险（最坏情况：CarPlay 入口失效，有线也不能用）。操作前务必备份原包。
- 不要动 `com.amwin.carlife.app`：它带 `coreApp="true"`，改坏可能影响开机流程。
