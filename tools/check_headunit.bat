@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
set OUT=headunit_report.txt

echo ================================================
echo  Headunit diagnostic collector (READ ONLY)
echo  Result file: %OUT%
echo ================================================
echo.
adb devices
if errorlevel 1 goto NOADB

type nul > %OUT%
call :SEC "1. DEVICES"
adb devices >> %OUT% 2>&1

call :SEC "2. BUILD"
adb shell "getprop ro.build.version.release; getprop ro.build.version.sdk; getprop ro.product.model; getprop ro.build.fingerprint; getprop ro.build.type; getprop ro.debuggable" >> %OUT% 2>&1

call :SEC "3. CAR PROPS"
adb shell "getprop" >> %OUT% 2>&1

call :SEC "4. PACKAGES"
adb shell "pm list packages" >> %OUT% 2>&1

call :SEC "5. IAP2 SERVICE DUMP"
adb shell "dumpsys package com.amwin.iap2" >> %OUT% 2>&1

call :SEC "6. NATIVE LIBS"
adb shell "ls -l /system/lib64/ /system/lib/" >> %OUT% 2>&1

call :SEC "7. NATIVE WIRELESS SYMBOLS (carplay)"
adb shell "strings /system/lib64/libcarplay_jni.so 2>/dev/null | grep -i -E 'raop|airplay|mdns|bonjour|awdl|wifi|wlan|ncm|eth0|getifaddrs|ifaddrs' | sort -u" >> %OUT% 2>&1

call :SEC "8. PROCESSES"
adb shell "ps -A" >> %OUT% 2>&1

call :SEC "9. WLAN / HOTSPOT"
adb shell "ip -4 addr show wlan0; ip -6 addr show wlan0; cat /proc/sys/net/ipv6/conf/wlan0/disable_ipv6" >> %OUT% 2>&1

call :SEC "10. LISTENING PORTS"
adb shell "netstat -tuln" >> %OUT% 2>&1

call :SEC "11. BLUETOOTH"
adb shell "dumpsys bluetooth_manager" >> %OUT% 2>&1

echo.
echo --------------------------------------------------
echo  Step 2: try the zero-root trigger?
echo  Make sure: hotspot ON (headunit as AP), iPhone joined it,
echo  iPhone BT paired and connected (play music to confirm).
echo  Type Y to continue, anything else to stop.
set /p ANS=
if /i not "%ANS%"=="Y" goto END

call :SEC "12. TRIAL broadcast connect.bluetooth"
adb logcat -c
adb shell "am broadcast -a com.amwin.iap2.IAP2Service.connect.bluetooth" >> %OUT% 2>&1
echo  waiting 12s ...
timeout /t 12 /nobreak >nul
adb logcat -d -s CarPlayService WiFiCarPlayBusiness CarPlayServer >> %OUT% 2>&1

:END
echo.
echo Done. Send back: %OUT%
pause
exit /b

:SEC
>> %OUT% echo.
>> %OUT% echo ########## %~1
exit /b

:NOADB
echo.
echo [ERROR] adb not found or no device. Check: adb devices
pause
exit /b
