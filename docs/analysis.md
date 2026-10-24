# 车机自带 CarPlay / CarLife 改无线连接：可行性分析与补丁包

分析对象：`com.amwin.carplay.app.apk`（435 KB）、`com.amwin.carlife.app.apk`（4.2 MB）
固件版本：`T1A_HL1.020700.00.200214`（versionCode 127 / 147，targetSdk 19）
桌面组件指向 `com.chery.launcher`（奇瑞车机）

---

## 0. 结论先说

| 项目 | 结论 |
|---|---|
| CarPlay 改无线 | **有戏**。无线功能的 Java 代码是完整的，只是被一个编译期常量 `IsUseWiFiCarPlay = false` 关掉，导致初始化入口被编译器整体删除。补丁已做好，只差"装进去"。 |
| CarLife 改无线 | **基本没戏**。APK 里连无线连接逻辑都没有（只剩两张没用的图片资源和常量），不是开关问题，是缺一整套协议对接。 |
| "重新编译 + 直接安装" | **走不通**。两个包都是 `sharedUserId="android.uid.system"` 的平台签名应用，换签名就装不上，装上也失去系统权限。必须 root 或拿到车机固件的平台私钥。 |
| 最省事的替代方案 | USB 无线 CarPlay 盒子 + 车机装 AutoKit 类 App，不动系统一个字节。 |

---

## 1. 动手前先做三个自检（这三点决定后面有没有意义）

1. **车机能不能开 WiFi 热点？**
   打开车机设置翻一遍，看有没有"车载热点 / WiFi 共享 / 移动热点"。这一条是**生死线**：厂商的无线 CarPlay 代码里**只查询热点状态，从不创建热点**（`WiFiCarPlayBusiness.isWiFiEnabled()` 是 `isWifiApEnabled()` 的反射调用）。热点开不出来，无线 CarPlay 就无从谈起。
2. **车机有没有 root / `adb root`？**
   没有 root，任何替换系统 App 的操作都做不了；只能用第 6 节的方案 C（外接盒子）。
3. **有没有备份原包？**
   补丁包目录里我放了一份 `原包备份/com.amwin.carplay.app_原版.apk`，出事能刷回去，别删。

---

## 2. 这两个包到底是什么

- 都是**系统级应用**：`android:sharedUserId="android.uid.system"`，CarLife 还带 `coreApp="true"`。
- 都是**只管界面和调度**的空壳：真正干活的原生库不在 APK 里——
  - CarPlay 加载 `libcarplay_jni.so`（`CarPlayServer` 静态块）
  - CarLife 加载 `libcarlifevehicle.so`（`CarLifeServer` 静态块）
  两个 APK 里都没有 `lib/` 目录，说明 `.so` 在车机 `/system/lib(64)` 里，属于厂商系统分区。**这部分我们无法重新编译**，只能改 Java 层。
- 依赖三个系统组件：`com.amwin.iap2`（Apple IAP2 服务）、`com.nforetek.bt`（蓝牙协议栈）、`com.oem.frameworks`（车厂框架，已打进 APK）。
- **签名**：两个包同一张证书，是 NXP/Freescale i.MX Android BSP 的**平台测试密钥**
  `EMAILADDRESS=android@freescale.com, CN=Android / SHA1: 3E:0C:AF:3D:79:9F:BA:EC:56:6F:AC:BE:1A:67:FB:25:0B:DE:FD:93`
  —— 这是后面所有麻烦的根源。

---

## 3. CarPlay：无线功能"写好了，但没接线"

这是本次分析最关键的发现。

### 3.1 证据链

| 位置 | 内容 | 说明 |
|---|---|---|
| `CarPlayService` 常量区 | `private static final boolean IsUseWiFiCarPlay = false` | 编译期常量，`javac` 会把所有 `if` 分支折掉。整个 DEX 里**没有任何一处引用它**（smali 已核对） |
| `CarPlayService` 消息表 | `HANDLER_INITWIFIBUSINESS = 16` → `WiFiCarPlayBusiness.instance().init(this)` | 全工程**没有任何一处** `sendEmptyMessage(16)`。也就是说无线业务模块从来没被启动过 |
| `wifi/WiFiCarPlayBusiness.java`（357 行，完整实现） | 蓝牙扫描 iPhone 的 CarPlay UUID `1a29eaab-...` + IAP2 UUID `fecacade-...`、配对设备列表落盘 `carplay.dev`、监听 `WIFI_AP_STATE_CHANGED`（13=热点已开，11=已关）、`connectBluetoothDevice(addr)`、失败后 5 秒重试 | 典型的"无线 CarPlay 首连流程"，代码质量正常，不像残迹 |
| `CarPlayService.mIAP2ServiceCallback.connectNotify()` | `b==4`（蓝牙已连）→ `initCarPlayServer(1)`；`b==3`（USB 已连）→ `initCarPlayServer(0)` | 参数 0/1 就是 `CONNECT_USB` / `CONNECT_WIFI`，无线链路入口是通的 |
| `initCarPlayServer()` | `SystemProperties.set("ctl.start","mdnsd")` → `new CarPlayServer(...)` | 起 mDNS 广播（AirPlay 靠 mDNS 播发服务） |
| `kAirPlayHandleStartIap2`(6) | `mIsCarPlayWifiConnected = true` + `connectBluetoothDevice(null)` | 无线会话建立的收尾处理，也是完整的 |

**判断**：厂商原本做过无线版本（大概率是同一套硬件的其他车型/其他配置），出货时用一个开关关掉了。从代码完整度看，作者是"能用"的水平，不是写了一半扔掉。

### 3.2 但它还缺三样东西（别抱 100% 期待）

1. **热点由谁开？** 代码只 `isWifiApEnabled()` 查询，从不 `setWifiApEnabled()`。所以必须车机系统自己已经把热点开起来了。
2. **原生库是否编进了 AirPlay-over-WiFi？** `libcarplay_jni.so` 不在 APK 里，无法验证。如果它只编了 USB-NCM 通道，Java 层再对也没用——这一点只能上车实测。
3. **iOS 侧时序**：需要"手机连车机热点 + 蓝牙配对"两件事都到位，中间任何一步顺序不对，`setupBluetoothDevice()` 会直接 return（它开头就有 `!isWiFiEnabled() || !isBTConnected()` 的守门判断）。

### 3.3 我打的补丁（两处，已编进附带的 APK）

```smali
# 1) CarPlayService.onCreate() 末尾 —— 让无线业务模块真正启动
invoke-static {}, Lcom/amwin/carplay/app/wifi/WiFiCarPlayBusiness;->instance()Lcom/amwin/carplay/app/wifi/WiFiCarPlayBusiness;
move-result-object v4
invoke-virtual {v4, p0}, Lcom/amwin/carplay/app/wifi/WiFiCarPlayBusiness;->init(Landroid/content/Context;)V

# 2) onHfpStateChanged() 末尾 —— 原版只在开机时试一次、不重试，这里补上蓝牙连上就触发
invoke-static {}, Lcom/amwin/carplay/app/wifi/WiFiCarPlayBusiness;->instance()L...;
move-result-object v1
const/4 v2, 0x0
invoke-virtual {v1, v2}, L...;->handlerSetupBluetoothDevice(I)V
```

等价于把厂商的开关打开，没有新增任何业务逻辑。改完已用 jadx 反编译回读确认两处都在。

---

## 4. 为什么"重新编译一下装上去"这条路会撞墙

这不是我保守，是 Android 的硬规则：

1. **签名变了 → 装不上。**
   原包用的是平台证书。你在自己电脑上重签，签名必然不同，`adb install -r` 会直接报
   `INSTALL_FAILED_UPDATE_INCOMPATIBLE`。
2. **就算强装（清数据/改 packages.xml/核心破解），还有 `sharedUserId` 这道锁。**
   它申请的是 `android.uid.system`，系统会校验"申请该共享 uid 的包必须与 platform 包同签名"，签名不符直接
   `INSTALL_FAILED_SHARED_USER_INCOMPATIBLE`。
3. **去掉 `sharedUserId` 能装上，但会崩。**
   没有 system uid 之后，这些调用全部失效：
   - `MANAGE_USB`（有线 CarPlay 直接废掉）
   - `SystemProperties.set("ctl.start","mdnsd")`（抛 SecurityException → 无线服务初始化直接崩）
   - `ServiceManager.addService(...)`、车厂 `PowerManager/VolumeManager/MicManager` 等私有服务
4. **`targetSdk 19` + 只有 v1 签名** 也说明这是 2020 年前后按系统镜像预装的方式打的包，本来就没打算让你单独升级。

**所以只有三种可能装进去：**

| 条件 | 做法 | 干净程度 |
|---|---|---|
| 有车机固件的 `platform.pk8 / platform.x509.pem`（在 NXP i.MX BSP 的 `device/fsl/common/security/`） | 用它重签我改好的包，`adb install -r` 直接覆盖，权限、uid 全保留 | ★★★★★ 最理想 |
| 有 root + 已装核心破解 / LSPosed（跳过签名校验） | 覆盖安装 | ★★★☆ 可用 |
| 有 root，什么都没有 | 把改好的包塞进 `/system/priv-app/...`，清掉 `/data/system/packages.xml` 里该包的记录后重启重扫；**uid 冲突仍可能导致失败** | ★★ 不推荐 |

---

## 5. CarLife：不是开关问题，是根本没做

- 资源里有 `btn_usb_wifi`、`lin_open_wifi` 两个**没人引用的**控件，代码里有个从没用过的常量 `CONNECT_STATUS_WIFI = 5` —— 就是全部"无线痕迹"。
- 连接层（`UsbDeviceManager` / `Iap2ServerManager` / `CarLifeClient`）只有三条路：Android 的 AOA、Android 的 NCM、iPhone 的 IAP2（**全部走线**）。
- 百度 CarLife 的无线是另一套握手：蓝牙 SPP 交换 SSID/密钥 → 手机连热点 → TCP 建链 → 再走 `CMD_CONNECTION_SETUP`。车机侧逻辑在闭源的 `libcarlifevehicle.so` 里，Java 层没有任何入口可以调。
- **结论**：想给 CarLife 加无线，等于自己实现一遍百度私有协议，工作量以周计，而且还要改 native。放弃。

**唯一值得花 5 分钟试的**：打开车机热点 → 手机连上该热点 → 手机打开 CarLife App，看是否自动发现车机（部分车机的 carlife daemon 自带局域网监听）。能连是运气，连不上是常态。

---

## 6. 三条落地路线（按推荐度排序）

### 方案 A：不动 APK，用 root + Frida 直接调用现成方法（推荐）

不用改包、不用重签、不碰系统分区，出问题重启即恢复。

```
adb push frida-server /data/local/tmp/
adb shell "su -c 'chmod 755 /data/local/tmp/frida-server'"
adb shell "su -c '/data/local/tmp/frida-server &'"
adb shell "ps -A | grep amwin.carplay"        # 拿 pid
frida -U -p <pid> -l frida_wireless_carplay.js
```

脚本已附（`frida_wireless_carplay.js`）：Hook `onCreate` 后调用 `WiFiCarPlayBusiness.init()`、Hook 蓝牙 HFP 回调、每 15 秒兜底重试，控制台输入 `trigger()` 可手动触发一次。

### 方案 B：刷我改好的 APK（需要上面表格里的某个条件）

- 产物：`com.amwin.carplay.app_无线补丁.apk`（已 zipalign + v1/v2/v3 签名，签名证书是自建的 `CN=WirelessCarPlay`，**不是**平台密钥，所以默认装不上）
- 有平台私钥的，先重签再装：
  ```
  apksigner sign --key platform.pk8 --cert platform.x509.pem \
      --in com.amwin.carplay.app_无线补丁.apk --out final.apk
  adb install -r final.apk
  ```
- 想自己从原包重新生成（比如你车机固件的版本号跟我这份不一样）：
  ```
  python3 patch_carplay_wireless.py -i 原包.apk -o 新包.apk \
      --apktool apktool.jar --signer uber-apk-signer.jar --ks mykey.jks
  ```
  脚本只替换 `classes.dex`，资源、清单、`resources.arsc` 全部保留原样，最大限度避免兼容问题。

### 方案 C：外接无线 CarPlay 盒子（100% 能成，只是要花钱）

车机 USB 口插一个 Carlinkit / 类似方案的无线 CarPlay 适配器，车机装它家的 AutoKit 类 App（普通 APK，不需要 root、不需要系统签名）。缺点是多一个盒子、多一次配对，优点是跟车机固件完全解耦，换车也能用。

---

## 7. 上车验证与排错

**看日志**（这是唯一可靠的判断依据）：

```
adb logcat -s CarPlayService WiFiCarPlayBusiness CarPlayServer
```

按顺序应该看到：

| 日志 | 含义 |
|---|---|
| `init WIFI` | 无线业务已启动（补丁生效） |
| `isWifiApEnabled` 为 true / `WIFI_AP_STATE_CHANGED_ACTION state = 13` | 热点是开着的 |
| `setupBluetoothDevice address: xx:xx:...` | 拿到手机蓝牙地址，开始 IAP2 连接 |
| `**Bluetooth connected**` | iOS 侧 IAP2 链路建立 |
| `Start iap2!!` / `**initCarPlayServer**` | 无线 AirPlay 服务已起 |
| `kAirPlayHandleSessionStartedAction` | 画面来了 |

**卡在哪一步，问题就在哪：**

- 卡在 `init WIFI` 之后没有 `setupBluetoothDevice` → 热点没开，或蓝牙 HFP 没连上（`isBTConnected()` 为 false）
- 有 `setupBluetoothDevice` 但一直 `connectNum` 重试 → 手机没连热点 / iOS 侧没把车机当 CarPlay 设备 / `libcarplay_jni` 不支持 WiFi
- 有 `**Bluetooth connected**` 但没有 `Start iap2!!` → 原生库这条通道没编，只能放弃（回去用方案 C）

**顺便确认硬件**：

```
adb shell dumpsys wifi | grep -i "Wi-Fi is"
adb shell ip addr show wlan0
```

---

## 8. 风险与回滚

- 覆盖系统 App 最坏的结果是车机"CarPlay 入口点了没反应"，**有线也一起不能用**（因为包被替换坏了）。不会影响开机和桌面。
- 回滚：`adb install -r 原包备份/com.amwin.carplay.app_原版.apk`（签名一致可直接覆盖）；没有 adb 的，用 root 文件管理器把备份包放回 `/system/priv-app/` 原路径，重启。
- **不要动 CarLife**：它是 `coreApp="true"`，改坏有可能影响开机流程。
- 所有"跳过签名校验"的手段都会降低系统安全性，只在一台车上这么干没问题，别养成习惯。

---

## 9. 附：目录里都有什么

```
无线CarPlay补丁包/
├── com.amwin.carplay.app_无线补丁.apk   已注入补丁并签名的成品（自建证书）
├── patch_carplay_wireless.py            从原包重新生成补丁包的脚本（可移植到你车机的版本）
├── frida_wireless_carplay.js            不改包的 root 方案
├── 原包备份/com.amwin.carplay.app_原版.apk  回滚用，别删
└── 原包备份/（如需）com.amwin.carlife.app_原版.apk
```

> 说明：CarLife 我没有做补丁，因为它缺的不是开关而是整条链路；强行塞代码的失败率和副作用都远高于收益。
