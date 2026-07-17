const http = require('http');
const fs = require('fs');
const path = require('path');
const os = require('os');
const { URL } = require('url');

const REMOTE_PORT = 8787;

function getLanAddresses() {
  const nets = os.networkInterfaces();
  const privateIps = [];
  const others = [];

  for (const name of Object.keys(nets)) {
    for (const net of nets[name] || []) {
      const family = net.family === 'IPv4' || net.family === 4;
      if (!family || net.internal) continue;
      const ip = net.address;
      const isPrivate =
        ip.startsWith('192.168.') ||
        ip.startsWith('10.') ||
        /^172\.(1[6-9]|2\d|3[0-1])\./.test(ip);
      if (isPrivate) privateIps.push(ip);
      else others.push(ip);
    }
  }

  // preferir IPs da LAN; ignorar VPN/público quando houver alternativa
  return privateIps.length ? privateIps : others;
}

function sendJson(res, status, data) {
  const body = JSON.stringify(data);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
  });
  res.end(body);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', (chunk) => {
      data += chunk;
      if (data.length > 1e6) {
        reject(new Error('Body too large'));
        req.destroy();
      }
    });
    req.on('end', () => {
      if (!data) return resolve({});
      try {
        resolve(JSON.parse(data));
      } catch (e) {
        reject(new Error('JSON inválido'));
      }
    });
    req.on('error', reject);
  });
}

function startRemoteServer(handlers) {
  const mobileHtmlPath = path.join(__dirname, 'mobile.html');

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, `http://${req.headers.host || 'localhost'}`);

    if (req.method === 'OPTIONS') {
      res.writeHead(204, {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
      });
      return res.end();
    }

    try {
      if (req.method === 'GET' && (url.pathname === '/' || url.pathname === '/mobile')) {
        const html = fs.readFileSync(mobileHtmlPath, 'utf8');
        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
        return res.end(html);
      }

      if (req.method === 'GET' && url.pathname === '/api/status') {
        return sendJson(res, 200, handlers.getStatus());
      }

      if (req.method === 'POST' && url.pathname === '/api/settings') {
        const body = await readBody(req);
        return sendJson(res, 200, handlers.saveSettings(body));
      }

      if (req.method === 'POST' && url.pathname === '/api/connection') {
        const body = await readBody(req);
        const result = await handlers.activateConnection(body);
        return sendJson(res, result.success ? 200 : 400, result);
      }

      if (req.method === 'POST' && url.pathname === '/api/start') {
        const body = await readBody(req);
        const result = await handlers.start(body);
        return sendJson(res, result.success ? 200 : 400, result);
      }

      if (req.method === 'POST' && url.pathname === '/api/stop') {
        const result = await handlers.stop();
        return sendJson(res, result.success ? 200 : 400, result);
      }

      sendJson(res, 404, { success: false, message: 'Not found' });
    } catch (err) {
      sendJson(res, 500, { success: false, message: err.message || String(err) });
    }
  });

  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(REMOTE_PORT, '0.0.0.0', () => {
      const ips = getLanAddresses();
      const urls = (ips.length ? ips : ['127.0.0.1']).map((ip) => `http://${ip}:${REMOTE_PORT}`);
      resolve({
        server,
        port: REMOTE_PORT,
        urls,
        primaryUrl: urls[0],
      });
    });
  });
}

module.exports = { startRemoteServer, getLanAddresses, REMOTE_PORT };
