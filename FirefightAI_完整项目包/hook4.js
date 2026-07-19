// Frida Hook v4 - 全面拦截Java触控（纯Java游戏，无Unity原生层）
Java.perform(function() {
    console.log("[Frida v4] Injecting into Java game...");

    // 1. Hook MotionEvent全部方法
    var MotionEvent = Java.use("android.view.MotionEvent");
    var methods = MotionEvent.class.getDeclaredMethods();
    var hooked = 0;
    methods.forEach(function(m) {
        var name = m.getName();
        if (name.includes("getSource") || name.includes("getToolType") ||
            name.includes("getDevice") || name.includes("getAction") ||
            name.includes("getFlags") || name.includes("getButtonState") ||
            name.includes("getPointerId") || name.includes("findPointerIndex")) {
            try {
                MotionEvent[name].overloads.forEach(function(overload) {
                    overload.implementation = function() {
                        // 篡改返回值
                        if (name.includes("Source")) return 0x1002;
                        if (name.includes("ToolType")) return 1;
                        if (name.includes("DeviceId")) return 1;
                        if (name.includes("Flags")) return 0;
                        // 其他调用原方法
                        return overload.apply(this, arguments);
                    };
                });
                hooked++;
            } catch(e) {}
        }
    });
    console.log("[MotionEvent] Hooked " + hooked + " methods");

    // 2. Hook View.onTouchEvent - 游戏主视图
    var View = Java.use("android.view.View");
    View.onTouchEvent.implementation = function(event) {
        // 强制接受所有触控事件
        return true;
    };
    console.log("[View] onTouchEvent hooked");

    // 3. Hook dispatchTouchEvent - Activity级别
    var Activity = Java.use("android.app.Activity");
    Activity.dispatchTouchEvent.implementation = function(event) {
        return true; // 标记已处理
    };
    console.log("[Activity] dispatchTouchEvent hooked");

    // 4. Hook InputManager.injectInputEvent - 拦截注入检测
    try {
        var InputManager = Java.use("android.hardware.input.InputManager");
        InputManager.injectInputEvent.implementation = function(event, mode) {
            return true;
        };
        console.log("[InputManager] injectInputEvent hooked");
    } catch(e) {
        console.log("[InputManager] skip: " + e);
    }

    console.log("[Frida v4] ===== ALL HOOKS ACTIVE =====");
    console.log("[Frida v4] ADB touch should now be accepted as real!");
});
