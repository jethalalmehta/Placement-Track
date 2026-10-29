import express from 'express';
import http from 'http';
import { spawn, execSync, ChildProcess } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const PORT = 3000;
const FLASK_PORT = 5001;

let flaskProcess: ChildProcess | null = null;
let isShuttingDown = false;
let restartAttempts = 0;
let lastRestartTime = Date.now();

function cleanupPort(port: number) {
  if (process.platform === 'win32') {
    try {
      const netstat = execSync(`netstat -ano -p tcp | findstr :${port}`).toString();
      for (const line of netstat.split('\n')) {
        if (line.includes('LISTENING')) {
          const parts = line.trim().split(/\s+/);
          const pid = parts[parts.length - 1];
          if (pid && !isNaN(Number(pid)) && Number(pid) !== process.pid) {
            try {
              execSync(`taskkill /F /PID ${pid}`, { stdio: 'ignore' });
            } catch {}
          }
        }
      }
    } catch {}
  } else {
    try {
      execSync(`pkill -f "app.py" || true`, { stdio: 'ignore' });
    } catch {}
  }
}

function killFlask() {
  if (!flaskProcess || !flaskProcess.pid) return;
  try {
    if (process.platform === 'win32') {
      execSync(`taskkill /F /T /PID ${flaskProcess.pid}`, { stdio: 'ignore' });
    } else {
      flaskProcess.kill('SIGTERM');
    }
  } catch {}
}

function getPythonExecutable(): string {
  const vendorPath = path.join(__dirname, 'vendor_packages');
  const isWin = process.platform === 'win32';
  const candidates = isWin
    ? ['python', 'py', 'python3']
    : [
        'python3',
        '/usr/bin/python3',
        '/usr/local/bin/python3',
        'python3.11',
        '/usr/bin/python3.11',
        'python'
      ];

  const pathSep = isWin ? ';' : ':';
  const testEnv = {
    ...process.env,
    PYTHONPATH: `${vendorPath}${pathSep}${process.env.PYTHONPATH || ''}`
  };

  for (const cmd of candidates) {
    try {
      execSync(`${cmd} -c "import flask"`, { env: testEnv, stdio: 'ignore' });
      return cmd;
    } catch {
      // try next candidate
    }
  }
  return isWin ? 'python' : 'python3';
}

function startFlask() {
  cleanupPort(FLASK_PORT);

  const pythonBin = getPythonExecutable();
  const vendorPath = path.join(__dirname, 'vendor_packages');
  const isWin = process.platform === 'win32';
  const pathSep = isWin ? ';' : ':';
  console.log(`[Server] Launching Flask backend on port ${FLASK_PORT} using ${pythonBin}...`);

  flaskProcess = spawn(pythonBin, ['app.py'], {
    cwd: __dirname,
    env: {
      ...process.env,
      PORT: String(FLASK_PORT),
      FLASK_DEBUG: '0',
      PYTHONUNBUFFERED: '1',
      PYTHONPATH: `${vendorPath}${pathSep}${process.env.PYTHONPATH || ''}`
    },
    stdio: ['ignore', 'inherit', 'inherit']
  });

  flaskProcess.on('error', (err) => {
    console.error('[Server] Failed to spawn Flask process:', err);
  });

  flaskProcess.on('exit', (code, signal) => {
    console.log(`[Server] Flask process exited with code ${code} and signal ${signal}`);
    const isCancel = code === 3221225786 || code === 130 || signal === 'SIGINT' || signal === 'SIGTERM';
    if (isCancel || isShuttingDown) {
      return;
    }

    const now = Date.now();
    if (now - lastRestartTime > 15000) {
      restartAttempts = 0;
    }
    lastRestartTime = now;
    restartAttempts++;

    if (restartAttempts > 5) {
      console.error('[Server] Flask failed to start 5 times in a row. Halting respawn.');
      return;
    }

    console.log('[Server] Re-spawning Flask in 1.5 seconds...');
    setTimeout(startFlask, 1500);
  });
}

startFlask();

// Transparent reverse proxy to Python Flask
app.all('*', (req, res) => {
  const options: http.RequestOptions = {
    hostname: '127.0.0.1',
    port: FLASK_PORT,
    path: req.originalUrl,
    method: req.method,
    headers: {
      ...req.headers,
      host: req.headers.host || `localhost:${PORT}`,
      'x-forwarded-for': req.ip || '127.0.0.1',
      'x-forwarded-proto': (req.headers['x-forwarded-proto'] as string) || req.protocol || 'http',
      'x-forwarded-port': String(PORT)
    }
  };

  const proxyReq = http.request(options, (proxyRes) => {
    res.writeHead(proxyRes.statusCode || 200, proxyRes.headers);
    proxyRes.pipe(res);
  });

  proxyReq.on('error', () => {
    res.status(502).send(`
      <!DOCTYPE html>
      <html>
        <head>
          <title>Connecting to Placement System...</title>
          <meta http-equiv="refresh" content="1">
          <style>
            body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; background: #f8fafc; color: #0f172a; }
            .card { background: white; padding: 32px; border-radius: 12px; border: 1px solid #e2e8f0; text-align: center; max-width: 440px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }
            h2 { margin: 0 0 8px; font-size: 20px; }
            p { color: #64748b; font-size: 14px; margin: 0 0 16px; }
            .spinner { width: 28px; height: 28px; border: 3px solid #e2e8f0; border-top-color: #4f46e5; border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto; }
            @keyframes spin { to { transform: rotate(360deg); } }
          </style>
        </head>
        <body>
          <div class="card">
            <div class="spinner"></div>
            <h2 style="margin-top: 16px;">Starting Placement System</h2>
            <p>Initializing Python Flask backend and SQLite database...</p>
          </div>
        </body>
      </html>
    `);
  });

  req.pipe(proxyReq);
});

const server = app.listen(PORT, '0.0.0.0', () => {
  console.log(`[Server] Proxy gateway running on http://0.0.0.0:${PORT} -> Flask http://127.0.0.1:${FLASK_PORT}`);
});

process.on('SIGTERM', () => {
  isShuttingDown = true;
  killFlask();
  server.close(() => process.exit(0));
});

process.on('SIGINT', () => {
  isShuttingDown = true;
  killFlask();
  server.close(() => process.exit(0));
});

process.on('exit', () => {
  killFlask();
});
