// Frida Hook v3 - 拦截Android原生输入层 (AInputEvent)
// Unity通过NDK的AInputEvent读取触控，需要Hook libandroid.so

// 1. Java层
Java.perform(function() {
    var MotionEvent = Java.use("android.view.MotionEvent");
    MotionEvent.getSource.implementation = function() { return 0x1002; };
    MotionEvent.getToolType.implementation = function(p) { return 1; };
});

// 2. 拦截libandroid.so的AInputEvent_getSource
setTimeout(function() {
    var libandroid = Process.findModuleByName("libandroid.so");
    if (!libandroid) libandroid = Process.findModuleByName("libandroid_runtime.so");
    
    if (libandroid) {
        var getSource = Module.findExportByName("libandroid.so", "AInputEvent_getSource");
        if (getSource) {
            Interceptor.attach(getSource, {
                onLeave: function(retval) {
                    var src = retval.toInt32();
                    if (src === 0) { // AINPUT_SOURCE_UNKNOWN
                        retval.replace(0x1002); // AINPUT_SOURCE_TOUCHSCREEN
                    }
                }
            });
            console.log("[Native] AInputEvent_getSource hooked");
        }
        
        // 同样Hook AInputEvent_getType
        var getType = Module.findExportByName("libandroid.so", "AInputEvent_getType");
        if (getType) {
            Interceptor.attach(getType, {
                onLeave: function(retval) {
                    var t = retval.toInt32();
                    if (t === 0) retval.replace(2); // AINPUT_EVENT_TYPE_MOTION
                }
            });
            console.log("[Native] AInputEvent_getType hooked");
        }
    }
    console.log("[Frida v3] Ready");
}, 1500);
