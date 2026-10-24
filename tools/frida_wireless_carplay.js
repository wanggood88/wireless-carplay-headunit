/*
 * frida_wireless_carplay.js
 * ------------------------------------------------------------------
 * 作用：在不修改、不重签系统 APK 的前提下，把车机自带的 CarPlay
 *      应用里"本来就写好但从未被调用"的无线链路激活。
 *
 * 原理：com.amwin.carplay.app.CarPlayService 里有一个编译期常量
 *      IsUseWiFiCarPlay = false，导致 WiFiCarPlayBusiness.init()
 *      的唯一调用点（Handler 消息 16）被编译器整个删掉。
 *      本脚本在 CarPlayService.onCreate() 之后直接调用这个方法，
 *      效果等同于厂商自己把开关打开。
 *
 * 用法（车机需 root）：
 *   1. 推送并启动 frida-server（与车机 CPU 架构一致）：
 *        adb push frida-server /data/local/tmp/
 *        adb shell "su -c 'chmod 755 /data/local/tmp/frida-server'"
 *        adb shell "su -c '/data/local/tmp/frida-server &'"
 *   2. 找到 CarPlay 服务进程号：
 *        adb shell "ps -A | grep amwin.carplay"
 *   3. 注入：
 *        frida -U -p <pid> -l frida_wireless_carplay.js
 *      （如果服务还没起来，先打开车机的 CarPlay，再注入）
 *
 * 观察日志：
 *   adb logcat -s CarPlayService WiFiCarPlayBusiness CarPlayServer
 *   出现 "init WIFI" / "setupBluetoothDevice address:" /
 *   "**Bluetooth connected**" / "Start iap2!!" 说明链路走通了。
 * ------------------------------------------------------------------
 */

var TAG = "[wcarplay] ";
var WifiBiz = null;
var Service = null;

function getWifiBiz() {
    if (WifiBiz === null) {
        WifiBiz = Java.use("com.amwin.carplay.app.wifi.WiFiCarPlayBusiness");
    }
    return WifiBiz;
}

Java.perform(function () {
    console.log(TAG + "loaded, hooking CarPlayService...");

    Service = Java.use("com.amwin.carplay.app.CarPlayService");

    // 1) 服务创建后立刻初始化无线业务（注册热点状态广播、准备蓝牙扫描）
    Service.onCreate.implementation = function () {
        this.onCreate();
        try {
            getWifiBiz().instance().init(this);
            console.log(TAG + "WiFiCarPlayBusiness.init() OK");
        } catch (e) {
            console.log(TAG + "init() 失败: " + e);
        }
    };

    // 2) 蓝牙 HFP 回调里也补一刀（原版只在开机时试一次，不重试）
    try {
        var Cb = Java.use("com.amwin.carplay.app.CarPlayService$11");
        Cb.onHfpStateChanged.implementation = function (addr, a, b) {
            this.onHfpStateChanged(addr, a, b);
            try {
                getWifiBiz().instance().handlerSetupBluetoothDevice(0);
            } catch (e) {
                console.log(TAG + "handlerSetupBluetoothDevice 失败: " + e);
            }
        };
    } catch (e) {
        console.log(TAG + "$11 hook 跳过: " + e);
    }

    // 3) 每 15 秒兜底重试一次（车机没有"热点开启/蓝牙连上"的二次触发）
    setInterval(function () {
        try {
            getWifiBiz().instance().handlerSetupBluetoothDevice(0);
        } catch (e) {
            /* 服务未就绪时忽略 */
        }
    }, 15000);

    // 4) 手动开关：在 frida 控制台里输入 trigger() 可立即尝试一次
    global.trigger = function () {
        try {
            getWifiBiz().instance().handlerSetupBluetoothDevice(0);
            console.log(TAG + "已手动触发一次");
        } catch (e) {
            console.log(TAG + "手动触发失败: " + e);
        }
    };
});

/*
 * 可选：如果车机没有"车载热点"开关，可以尝试用下面这段强制开热点。
 * 注意：不同车机 ROM 的 WifiManager 实现差异很大，失败属正常。
 *
 * function forceHotspot(ssid, pass) {
 *     Java.perform(function () {
 *         var Ctx = Java.use("android.app.ActivityThread").currentApplication();
 *         var Wm = Java.use("android.net.wifi.WifiManager");
 *         var wm = Ctx.getSystemService("wifi");
 *         var Cfg = Java.use("android.net.wifi.WifiConfiguration");
 *         var cfg = Cfg.$new();
 *         cfg.SSID.value = ssid;
 *         cfg.preSharedKey.value = pass;
 *         cfg.allowedKeyManagement.value = Java.use("android.net.wifi.WifiConfiguration$KeyMgmt").WPA_PSK.value;
 *         var m = Wm.class.getMethod("setWifiApEnabled", Cfg.class, Java.use("java.lang.Boolean").TYPE);
 *         console.log("setWifiApEnabled -> " + m.invoke(wm, cfg, true));
 *     });
 * }
 * // 在控制台执行：forceHotspot("CarPlay", "12345678")
 */
