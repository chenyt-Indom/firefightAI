const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = 'C:/Users/19853/WorkBuddy/2026-07-18-07-52-25/firefightAI';
const PORT = 5800;

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript',
  '.css': 'text/css',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.json': 'application/json',
};

http.createServer((req, res) => {
  let url = req.url === '/' ? '/src/web/annotate_flags.html' : req.url;
  let filePath = path.join(ROOT, url);
  
  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(404);
      res.end('Not found: ' + url);
      return;
    }
    let ext = path.extname(filePath);
    res.writeHead(200, { 'Content-Type': MIME[ext] || 'application/octet-stream' });
    res.end(data);
  });
}).listen(PORT, () => console.log(`Server: http://localhost:${PORT}`));
