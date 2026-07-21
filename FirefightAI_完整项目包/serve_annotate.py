import http.server, socketserver, os
os.chdir('C:/Users/19853/WorkBuddy/2026-07-18-07-52-25/firefightAI')

class H(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path in ('/', '/index.html'):
            self.path = '/src/web/annotate_flags.html'
        return super().do_GET()

socketserver.TCPServer.allow_reuse_address = True
httpd = socketserver.TCPServer(('0.0.0.0', 5800), H)
print('SERVER_READY')
httpd.serve_forever()
