#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patch_carplay_wireless.py —— 复现/移植"无线 CarPlay"补丁

做的事：
  1. apktool 反编译原始 APK
  2. 在 CarPlayService.onCreate() 末尾插入 WiFiCarPlayBusiness.init(this)
  3. 在 onHfpStateChanged() 末尾插入 handlerSetupBluetoothDevice(0)
  4. apktool 重打包，然后【只取新 classes.dex】塞回原始 APK
     （资源/清单/arsc 一律不动，最大程度降低兼容风险）
  5. 用 uber-apk-signer（或 apksigner）签名

依赖：java、apktool.jar、uber-apk-signer.jar
用法：
  python3 patch_carplay_wireless.py \
      -i com.amwin.carplay.app.apk \
      -o com.amwin.carplay.app_无线补丁.apk \
      --apktool  /path/to/apktool.jar \
      --signer   /path/to/uber-apk-signer.jar

换用平台签名（如果你有车机固件的 platform.pk8 / platform.x509.pem）：
  # 用自己车机固件的平台私钥签名（能直接覆盖原包，最干净）：
  apksigner sign --key platform.pk8 --cert platform.x509.pem \
      --in <上面生成的 merged.apk> --out out.apk
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

INIT_SNIPPET = """
    # --- WIRELESS-CARPLAY-PATCH begin ---
    invoke-static {}, Lcom/amwin/carplay/app/wifi/WiFiCarPlayBusiness;->instance()Lcom/amwin/carplay/app/wifi/WiFiCarPlayBusiness;

    move-result-object v4

    invoke-virtual {v4, p0}, Lcom/amwin/carplay/app/wifi/WiFiCarPlayBusiness;->init(Landroid/content/Context;)V
    # --- WIRELESS-CARPLAY-PATCH end ---
"""

HFP_SNIPPET = """
    # --- WIRELESS-CARPLAY-PATCH begin ---
    invoke-static {}, Lcom/amwin/carplay/app/wifi/WiFiCarPlayBusiness;->instance()Lcom/amwin/carplay/app/wifi/WiFiCarPlayBusiness;

    move-result-object v1

    const/4 v2, 0x0

    invoke-virtual {v1, v2}, Lcom/amwin/carplay/app/wifi/WiFiCarPlayBusiness;->handlerSetupBluetoothDevice(I)V
    # --- WIRELESS-CARPLAY-PATCH end ---
"""


def run(cmd, **kw):
    print("  $ " + " ".join(str(c) for c in cmd))
    subprocess.check_call(cmd, **kw)


def patch_oncreate(smali_dir):
    """在 onCreate 末尾注入 init()。"""
    path = os.path.join(smali_dir, "com/amwin/carplay/app/CarPlayService.smali")
    src = open(path, encoding="utf-8").read()
    if "WIRELESS-CARPLAY-PATCH" in src:
        print("  已打过补丁，跳过 onCreate")
        return
    start = src.index(".method public onCreate()V")
    end = src.index(".end method", start)
    body = src[start:end]
    m = re.search(r"\.locals (\d+)", body)
    if not m:
        raise SystemExit("onCreate 里找不到 .locals")
    body = body[:m.start()] + ".locals %d" % (int(m.group(1)) + 2) + body[m.end():]
    i = body.rindex("return-void")
    body = body[:i] + INIT_SNIPPET + body[i:]
    open(path, "w", encoding="utf-8").write(src[:start] + body + src[end:])
    print("  onCreate 注入完成")


def patch_hfp(smali_dir):
    """在 onHfpStateChanged 末尾注入 handlerSetupBluetoothDevice(0)。"""
    path = os.path.join(smali_dir, "com/amwin/carplay/app/CarPlayService$11.smali")
    if not os.path.exists(path):
        print("  !! 找不到 CarPlayService$11.smali，跳过蓝牙回调补丁")
        return
    src = open(path, encoding="utf-8").read()
    if "WIRELESS-CARPLAY-PATCH" in src:
        print("  已打过补丁，跳过 HFP 回调")
        return
    pat = r"(\.method public onHfpStateChanged\(Ljava/lang/String;II\)V\s*\n\s*\.locals )(\d+)"
    if not re.search(pat, src):
        print("  !! onHfpStateChanged 结构不符，跳过（不影响主功能）")
        return
    src = re.sub(pat, lambda m: m.group(1) + str(int(m.group(2)) + 2), src, count=1)
    start = src.index(".method public onHfpStateChanged")
    end = src.index(".end method", start)
    body = src[start:end]
    i = body.rindex("return-void")
    body = body[:i] + HFP_SNIPPET + body[i:]
    open(path, "w", encoding="utf-8").write(src[:start] + body + src[end:])
    print("  HFP 回调注入完成")


def rebuild_dex_only(orig_apk, built_apk, out_apk):
    """把 built_apk 里的 classes.dex 换进 orig_apk，资源保持原样。"""
    with zipfile.ZipFile(built_apk) as z:
        dex = z.read("classes.dex")
    with zipfile.ZipFile(orig_apk) as zin, zipfile.ZipFile(out_apk, "w", zipfile.ZIP_DEFLATED) as zout:
        for it in zin.infolist():
            if it.filename == "classes.dex" or it.filename.startswith("META-INF/"):
                continue
            data = zin.read(it.filename)
            zi = zipfile.ZipInfo(it.filename, date_time=it.date_time)
            zi.compress_type = zipfile.ZIP_STORED if it.filename == "resources.arsc" else zipfile.ZIP_DEFLATED
            zout.writestr(zi, data)
        zi = zipfile.ZipInfo("classes.dex", date_time=(2011, 7, 15, 2, 28, 0))
        zi.compress_type = zipfile.ZIP_DEFLATED
        zout.writestr(zi, dex)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--input", required=True)
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--apktool", required=True)
    ap.add_argument("--signer", required=True)
    ap.add_argument("--ks", help="已有 keystore（可选）")
    ap.add_argument("--ks-pass", default="android")
    ap.add_argument("--ks-alias", default="carplay")
    a = ap.parse_args()

    work = tempfile.mkdtemp(prefix="cpwifi_")
    dec = os.path.join(work, "dec")
    try:
        print("[1/5] 反编译")
        run(["java", "-jar", a.apktool, "d", "-f", "-o", dec, a.input])

        print("[2/5] 注入补丁")
        smali_root = os.path.join(dec, "smali")
        patch_oncreate(smali_root)
        patch_hfp(smali_root)

        print("[3/5] 重打包")
        built = os.path.join(work, "built.apk")
        run(["java", "-jar", a.apktool, "b", dec, "-o", built])

        print("[4/5] 只替换 dex")
        merged = os.path.join(work, "merged.apk")
        rebuild_dex_only(a.input, built, merged)

        print("[5/5] 签名")
        sign_dir = os.path.join(work, "signed")
        cmd = ["java", "-jar", a.signer, "-a", merged, "-o", sign_dir]
        if a.ks:
            cmd += ["--ks", a.ks, "--ksPass", a.ks_pass, "--ksKeyPass", a.ks_pass, "--ksAlias", a.ks_alias]
        run(cmd)
        for f in os.listdir(sign_dir):
            if f.endswith(".apk"):
                shutil.copy2(os.path.join(sign_dir, f), a.output)
                break
        print("完成 -> " + a.output)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
