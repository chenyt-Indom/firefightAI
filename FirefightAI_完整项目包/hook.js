// Frida Hook - 绕过Firefight合成触控检测 (MuMu x86_64)
Java.perform(function() {
    console.log("[Frida] Hook injected into Firefight!");

    var MotionEvent = Java.use("android.view.MotionEvent");

    // 1. 强制返回触摸屏来源
    MotionEvent.getSource.implementation = function() {
        return 0x1002; // SOURCE_TOUCHSCREEN
    };

    // 2. 强制返回手指工具类型
    MotionEvent.getToolType.implementation = function(pi) {
        return 1; // TOOL_TYPE_FINGER
    };

    // 3. 返回有效触摸设备ID
    MotionEvent.getDeviceId.implementation = function() {
        return 1;
    };

    console.log("[Frida] Touch bypass ACTIVE - ADB taps should work now!");
});
