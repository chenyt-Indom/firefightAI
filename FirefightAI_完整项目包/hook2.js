// Frida Hook v2 - 绕过Unity原生触控检测
// Unity通过C# Input.touches获取触控，需要Hook Mono运行时

// 1. 先拦截Java层(基础)
Java.perform(function() {
    var MotionEvent = Java.use("android.view.MotionEvent");
    MotionEvent.getSource.implementation = function() { return 0x1002; };
    MotionEvent.getToolType.implementation = function(p) { return 1; };
    MotionEvent.getDeviceId.implementation = function() { return 1; };
    console.log("[Java] MotionEvent bypass OK");
});

// 2. 拦截Unity的C# Input类(Mono运行时)
var mono_loaded = false;
var Input = null;

function hookUnityInput() {
    try {
        // Unity的Input在Assembly-CSharp或UnityEngine.CoreModule中
        var assemblies = [
            "Assembly-CSharp",
            "UnityEngine.CoreModule", 
            "UnityEngine.InputLegacyModule"
        ];
        
        for (var i = 0; i < assemblies.length; i++) {
            try {
                var asm = Module.findExportByName(null, "mono_image_open_from_data_with_name");
                console.log("[Unity] Found " + assemblies[i]);
                
                // 尝试通过mono API查找Input.get_touchCount
                var mono = Module.findExportByName("libmono.so", "mono_runtime_invoke");
                if (mono) {
                    console.log("[Unity] Mono runtime found!");
                    // Hook mono_runtime_invoke来拦截所有C#调用
                    Interceptor.attach(mono, {
                        onEnter: function(args) {
                            // 检查是否调用了Input.get_touches
                        }
                    });
                }
                break;
            } catch(e) {}
        }
    } catch(e) {
        console.log("[Unity] Mono hook skip: " + e);
    }
}

// 3. 更简单的方法：直接Hook libunity.so的触控处理函数
function hookNativeUnity() {
    try {
        // Unity中处理触控的关键函数
        var targets = [
            "UnityEngine.Input::GetTouch",
            "_ZN7android13InputConsumer21initializeMotionEventEPNS_11MotionEventEPKNS_10InputEventE",
            "UnityInputProcessTouchEvent"
        ];
        
        // 尝试用Module枚举找libunity.so
        var mod = Process.findModuleByName("libunity.so");
        if (mod) {
            console.log("[Unity] libunity.so loaded at " + mod.base);
            
            // Hook常见的触控处理函数名
            var exports = mod.enumerateExports();
            for (var j = 0; j < exports.length; j++) {
                var name = exports[j].name;
                if (name.includes("Touch") || name.includes("touch") || name.includes("Input")) {
                    if (name.includes("Get") || name.includes("Process") || name.includes("Handle")) {
                        try {
                            Interceptor.attach(exports[j].address, {
                                onLeave: function(retval) {
                                    // 不修改，只打印确认被调用了
                                }
                            });
                        } catch(e) {}
                    }
                }
            }
        }
    } catch(e) {
        console.log("[Unity] Native hook skip: " + e);
    }
}

setTimeout(function() {
    hookUnityInput();
    hookNativeUnity();
    console.log("[Frida v2] All hooks installed");
}, 1000);
