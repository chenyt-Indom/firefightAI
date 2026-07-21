@app.route("/api/emulator/stream")
def api_emulator_stream():
    """MJPEG PNG stream"""
    import threading as _thr, queue as _queue
    adb_exe = _get_adb_for_emulator()
    dev_id = f"127.0.0.1:{_emulator_adb_port}"
    frame_buffer = _queue.Queue(maxsize=2)
    running = [True]
    def worker():
        while running[0]:
            try:
                r = subprocess.run([adb_exe,"-s",dev_id,"exec-out","screencap","-p"],capture_output=True,timeout=3)
                if r.returncode==0 and len(r.stdout)>500:
                    try:frame_buffer.put_nowait(r.stdout)
                    except:pass
            except:time.sleep(0.5)
    _thr.Thread(target=worker,daemon=True).start()
    for _ in range(60):
        if not frame_buffer.empty():break
        time.sleep(0.1)
    def gen():
        while running[0]:
            try:
                frame = frame_buffer.get(timeout=2)
                crlf = b"\r\n"
                yield (b"--frame" + crlf + b"Content-Type: image/png" + crlf + b"Content-Length: " + str(len(frame)).encode() + crlf + crlf + frame + crlf)
            except:
                time.sleep(0.1)
    return Response(stream_with_context(gen()), mimetype="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache", "Access-Control-Allow-Origin": "*"}, direct_passthrough=True)
