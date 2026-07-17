const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn, exec, execFileSync } = require('child_process');
const { OBSWebSocket } = require('obs-websocket-js');
const { startRemoteServer } = require('./remote-server');

let mainWindow;
let splashWindow = null;
let scrcpyProcess = null;
let isRecording = false;
let stoppingIntentionally = false;
let obsStartTime = null;
let scrcpyStartTime = null;
let sessionMode = 'record'; // 'capture' | 'record'
let sessionUseObs = true;
let remoteInfo = { urls: [], primaryUrl: null, port: 8787 };

function which(cmd) {
  try {
    const bin = process.platform === 'win32' ? 'where.exe' : 'which';
    const out = execFileSync(bin, [cmd], { encoding: 'utf8' });
    return out.split(/\r?\n/).map((s) => s.trim()).find((s) => s && fs.existsSync(s)) || null;
  } catch (_) {
    return null;
  }
}

function firstExisting(candidates) {
  for (const candidate of candidates) {
    if (candidate && fs.existsSync(candidate)) return candidate;
  }
  return null;
}

function resolveScrcpyPath() {
  if (process.env.SCRCPY_PATH) {
    const envPath = process.env.SCRCPY_PATH;
    if (fs.existsSync(envPath)) return envPath;
  }
  const fromPath = which('scrcpy') || which('scrcpy.exe');
  if (fromPath) return fromPath;

  const localAppData = process.env.LOCALAPPDATA || '';
  const wingetRoot = path.join(localAppData, 'Microsoft', 'WinGet', 'Packages');
  if (fs.existsSync(wingetRoot)) {
    try {
      const matches = fs.readdirSync(wingetRoot)
        .filter((name) => /scrcpy/i.test(name))
        .flatMap((pkg) => {
          const pkgDir = path.join(wingetRoot, pkg);
          try {
            return fs.readdirSync(pkgDir).map((child) => path.join(pkgDir, child, 'scrcpy.exe'));
          } catch (_) {
            return [];
          }
        });
      const found = firstExisting(matches);
      if (found) return found;
    } catch (_) {
      // ignore
    }
  }

  return 'scrcpy';
}

function resolveFfmpegPath() {
  if (process.env.FFMPEG_PATH && fs.existsSync(process.env.FFMPEG_PATH)) {
    return process.env.FFMPEG_PATH;
  }

  const localAppData = process.env.LOCALAPPDATA || '';
  const candidates = [
    which('ffmpeg'),
    which('ffmpeg.exe'),
    path.join(localAppData, 'Microsoft', 'WinGet', 'Links', 'ffmpeg.exe'),
  ];

  return firstExisting(candidates) || 'ffmpeg';
}

function resolveDefaultRecordDir() {
  try {
    return path.join(app.getPath('downloads'), 'screencopy');
  } catch (_) {
    return path.join(process.env.USERPROFILE || process.cwd(), 'Downloads', 'screencopy');
  }
}

const config = {
  scrcpyPath: 'scrcpy',
  recordDir: '',
  recordVideoPath: '',
  obsHost: 'localhost',
  obsPort: 4455,
  obsPassword: '',
  ffmpegPath: 'ffmpeg',
  // defaults do painel
  mode: 'record',
  connection: 'usb', // 'usb' | 'wifi'
  wifiAddress: '',
  bitrate: '6000K',
  maxFps: '30',
  format: 'mkv',
  maxSize: '0',
  useObsAudio: true,
  stayAwake: true,
  turnScreenOff: false,
};

const obs = new OBSWebSocket();

function buildRecordPath(dir, format = config.format) {
  const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  const ext = format === 'mp4' ? 'mp4' : 'mkv';
  return path.join(dir, `screenvid-${stamp}.${ext}`);
}

const APP_ICON = path.join(__dirname, 'public', 'icon.png');

function createSplash() {
  splashWindow = new BrowserWindow({
    width: 360,
    height: 420,
    resizable: false,
    maximizable: false,
    minimizable: false,
    frame: false,
    transparent: false,
    alwaysOnTop: true,
    center: true,
    show: false,
    backgroundColor: '#0c0e12',
    icon: APP_ICON,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  splashWindow.loadFile('splash.html');
  splashWindow.once('ready-to-show', () => {
    splashWindow.show();
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 560,
    height: 820,
    minWidth: 520,
    minHeight: 720,
    resizable: true,
    show: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js')
    },
    autoHideMenuBar: true,
    title: 'ClevenRec',
    backgroundColor: '#0c0e12',
    icon: APP_ICON
  });

  mainWindow.loadFile('index.html');

  mainWindow.once('ready-to-show', () => {
    // splash mínimo ~1.2s para leitura da marca
    const reveal = () => {
      if (splashWindow && !splashWindow.isDestroyed()) {
        splashWindow.close();
        splashWindow = null;
      }
      mainWindow.show();
      mainWindow.focus();
    };
    setTimeout(reveal, 1200);
  });
}

function getSettingsSnapshot() {
  return {
    mode: config.mode,
    connection: config.connection,
    wifiAddress: config.wifiAddress,
    bitrate: config.bitrate,
    maxFps: config.maxFps,
    format: config.format,
    maxSize: config.maxSize,
    useObsAudio: config.useObsAudio,
    stayAwake: config.stayAwake,
    turnScreenOff: config.turnScreenOff,
  };
}

function applySettings(settings = {}) {
  Object.assign(config, {
    mode: settings.mode ?? config.mode,
    connection: settings.connection ?? config.connection,
    wifiAddress: settings.wifiAddress != null ? String(settings.wifiAddress).trim() : config.wifiAddress,
    bitrate: settings.bitrate ?? config.bitrate,
    maxFps: String(settings.maxFps ?? config.maxFps),
    format: settings.format ?? config.format,
    maxSize: String(settings.maxSize ?? config.maxSize),
    useObsAudio: settings.useObsAudio ?? config.useObsAudio,
    stayAwake: settings.stayAwake ?? config.stayAwake,
    turnScreenOff: settings.turnScreenOff ?? config.turnScreenOff,
  });
  return getSettingsSnapshot();
}

function runAdb(adbPath, args) {
  return new Promise((resolve) => {
    exec(`"${adbPath}" ${args}`, { timeout: 15000 }, (err, stdout, stderr) => {
      resolve({
        ok: !err,
        stdout: (stdout || '').toString(),
        stderr: (stderr || '').toString(),
        error: err,
      });
    });
  });
}

function parseAdbDevices(stdout) {
  return stdout.split('\n')
    .slice(1)
    .map((l) => l.trim())
    .filter((l) => l && /\tdevice$/.test(l))
    .map((l) => {
      const serial = l.split(/\s+/)[0];
      return { serial, isWifi: serial.includes(':') };
    });
}

function binaryLooksAvailable(binPath) {
  if (!binPath) return false;
  if (binPath.includes(path.sep) || binPath.includes('/') || binPath.includes('\\')) {
    return fs.existsSync(binPath);
  }
  return Boolean(which(binPath) || which(`${binPath}.exe`));
}

function validateRuntimeBinaries({ needFfmpeg = false } = {}) {
  if (!binaryLooksAvailable(config.scrcpyPath)) {
    return {
      success: false,
      message:
        'scrcpy não encontrado.\n\nInstale via winget (winget install Genymobile.scrcpy) ' +
        'ou defina a variável de ambiente SCRCPY_PATH com o caminho do scrcpy.exe.',
    };
  }
  if (needFfmpeg && !binaryLooksAvailable(config.ffmpegPath)) {
    return {
      success: false,
      message:
        'ffmpeg não encontrado (necessário para sync vídeo+áudio).\n\n' +
        'Instale via winget (winget install Gyan.FFmpeg) ou defina FFMPEG_PATH.',
    };
  }
  return { success: true };
}

function initRuntimePaths() {
  config.scrcpyPath = resolveScrcpyPath();
  config.ffmpegPath = resolveFfmpegPath();
  config.recordDir = resolveDefaultRecordDir();
  try {
    fs.mkdirSync(config.recordDir, { recursive: true });
  } catch (_) {
    // ignore
  }
  config.recordVideoPath = path.join(config.recordDir, 'screenvid.mkv');
}

function getAdbPath() {
  if (config.scrcpyPath && config.scrcpyPath.includes(path.sep)) {
    const beside = path.join(path.dirname(config.scrcpyPath), process.platform === 'win32' ? 'adb.exe' : 'adb');
    if (fs.existsSync(beside)) return beside;
  }
  return which('adb') || which('adb.exe') || 'adb';
}

async function detectDeviceWifiIp(adbPath, serial) {
  const prefix = serial ? `-s "${serial}" ` : '';
  const scripts = [
    `${prefix}shell "ip -f inet addr show wlan0"`,
    `${prefix}shell "ip -f inet addr show wlan1"`,
    `${prefix}shell getprop dhcp.wlan0.ipaddress`,
    `${prefix}shell "ip route | grep wlan"`,
  ];

  for (const cmd of scripts) {
    const res = await runAdb(adbPath, cmd);
    const text = `${res.stdout}\n${res.stderr}`;
    const match = text.match(/\b(192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3})\b/);
    if (match) return match[1];
  }
  return null;
}

/**
 * USB  → ativa depuração por cabo (adb usb)
 * Wi‑Fi → ativa ADB TCP (adb tcpip 5555 + adb connect)
 */
async function activateConnection(connection, wifiAddress = '') {
  const adbPath = getAdbPath();
  const mode = connection === 'wifi' ? 'wifi' : 'usb';
  config.connection = mode;
  if (wifiAddress != null && String(wifiAddress).trim()) {
    config.wifiAddress = String(wifiAddress).trim();
  }

  if (mode === 'usb') {
    await runAdb(adbPath, 'usb');
    await new Promise((r) => setTimeout(r, 1200));

    const listed = await runAdb(adbPath, 'devices');
    const usbDevices = parseAdbDevices(listed.stdout).filter((d) => !d.isWifi);

    if (usbDevices.length === 0) {
      return {
        success: false,
        connection: 'usb',
        message: 'Modo USB ativado, mas nenhum aparelho no cabo.\n\nConecte o USB, ative Depuração USB e aceite o aviso no celular.'
      };
    }

    return {
      success: true,
      connection: 'usb',
      devices: usbDevices.map((d) => d.serial),
      message: `Depuração USB ativa · ${usbDevices.length} dispositivo(s)`
    };
  }

  // --- Wi‑Fi / ADB TCP ---
  let listed = await runAdb(adbPath, 'devices');
  let devices = parseAdbDevices(listed.stdout);
  let usbDevices = devices.filter((d) => !d.isWifi);

  // Se ainda há USB, liga o daemon em TCP (necessário na 1ª vez)
  if (usbDevices.length > 0) {
    const serial = usbDevices[0].serial;
    const tcpip = await runAdb(adbPath, `-s "${serial}" tcpip 5555`);
    const tcpOut = `${tcpip.stdout} ${tcpip.stderr}`;
    if (!/restarting|5555/i.test(tcpOut) && tcpip.error) {
      return {
        success: false,
        connection: 'wifi',
        message: `Falha ao ativar ADB Wi‑Fi (tcpip 5555).\n\n${tcpOut.trim() || 'Conecte o USB uma vez para autorizar.'}`
      };
    }
    await new Promise((r) => setTimeout(r, 1500));

    if (!config.wifiAddress) {
      const ip = await detectDeviceWifiIp(adbPath, serial);
      if (ip) config.wifiAddress = ip;
    }
  }

  let targetHost = config.wifiAddress;
  if (!targetHost) {
    // já pode existir um device wifi conectado
    listed = await runAdb(adbPath, 'devices');
    const wifiDevices = parseAdbDevices(listed.stdout).filter((d) => d.isWifi);
    if (wifiDevices.length > 0) {
      return {
        success: true,
        connection: 'wifi',
        wifiAddress: wifiDevices[0].serial,
        devices: wifiDevices.map((d) => d.serial),
        message: `ADB Wi‑Fi já conectado · ${wifiDevices[0].serial}`
      };
    }
    return {
      success: false,
      connection: 'wifi',
      message: 'Informe o IP do celular (ex.: 192.168.0.20).\n\nNa 1ª vez: deixe o USB conectado, o app ativa adb tcpip 5555 e detecta o IP.'
    };
  }

  const target = targetHost.includes(':') ? targetHost : `${targetHost}:5555`;
  const connect = await runAdb(adbPath, `connect ${target}`);
  const connectOut = `${connect.stdout} ${connect.stderr}`;
  if (!/connected|already connected/i.test(connectOut)) {
    return {
      success: false,
      connection: 'wifi',
      wifiAddress: config.wifiAddress,
      message: `Não conectou em ${target}.\n\n1. USB uma vez + Depuração USB\n2. Mesmo Wi‑Fi do PC\n3. Confirme o IP\n\n${connectOut.trim()}`
    };
  }

  listed = await runAdb(adbPath, 'devices');
  const wifiDevices = parseAdbDevices(listed.stdout).filter((d) => d.isWifi);

  return {
    success: true,
    connection: 'wifi',
    wifiAddress: config.wifiAddress,
    devices: wifiDevices.map((d) => d.serial),
    message: `ADB Wi‑Fi ativo · ${target}`
  };
}

function getStatusPayload() {
  return {
    isRecording,
    videoPath: config.recordVideoPath,
    recordDir: config.recordDir,
    settings: getSettingsSnapshot(),
    remote: remoteInfo,
  };
}

app.whenReady().then(async () => {
  if (process.platform === 'win32') {
    app.setAppUserModelId('com.cleven.clevenrec');
  }

  initRuntimePaths();
  createSplash();
  createWindow();
  try {
    remoteInfo = await startRemoteServer({
      getStatus: getStatusPayload,
      saveSettings: (settings) => ({ success: true, settings: applySettings(settings) }),
      activateConnection: (body = {}) => activateConnection(body.connection || config.connection, body.wifiAddress),
      start: (options) => startSession(options || {}),
      stop: () => stopEverything(),
    });
    // não guardar o objeto server (não é serializável no IPC)
    remoteInfo = {
      port: remoteInfo.port,
      urls: remoteInfo.urls,
      primaryUrl: remoteInfo.primaryUrl,
    };
    console.log('Remote mobile UI:', remoteInfo.primaryUrl);
    console.log('scrcpy:', config.scrcpyPath);
    console.log('ffmpeg:', config.ffmpegPath);
    console.log('recordDir:', config.recordDir);
  } catch (err) {
    console.error('Falha ao iniciar servidor remoto:', err.message);
  }
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    stopEverything();
    app.quit();
  }
});

ipcMain.handle('get-status', () => getStatusPayload());

ipcMain.handle('get-remote-info', () => remoteInfo);

ipcMain.handle('save-settings', (_event, settings) => {
  return { success: true, settings: applySettings(settings) };
});

ipcMain.handle('activate-connection', async (_event, payload = {}) => {
  const connection = payload.connection || config.connection;
  const wifiAddress = payload.wifiAddress != null ? payload.wifiAddress : config.wifiAddress;
  return activateConnection(connection, wifiAddress);
});

ipcMain.handle('start-recording', async (_event, options = {}) => {
  return startSession(options);
});

async function startSession(options = {}) {
  if (isRecording) return { success: false, message: 'Já está em execução!' };

  applySettings(options);

  sessionMode = config.mode;
  sessionUseObs = config.mode === 'record' && config.useObsAudio;

  try {
    stoppingIntentionally = false;

    const bins = validateRuntimeBinaries({ needFfmpeg: sessionMode === 'record' && sessionUseObs });
    if (!bins.success) return bins;

    // Ativa depuração USB ou ADB Wi‑Fi conforme o toggle
    const activated = await activateConnection(config.connection, config.wifiAddress);
    if (!activated.success) {
      return activated;
    }
    if (activated.wifiAddress) {
      config.wifiAddress = activated.wifiAddress.replace(/:5555$/, '');
    }

    const useWifi = config.connection === 'wifi';
    const adbPath = getAdbPath();
    const listed = await runAdb(adbPath, 'devices');
    const devices = parseAdbDevices(listed.stdout);
    const matching = devices.filter((d) => (useWifi ? d.isWifi : !d.isWifi));

    if (matching.length === 0) {
      return {
        success: false,
        message: useWifi
          ? 'ADB Wi‑Fi ativo, mas nenhum dispositivo TCP encontrado.'
          : 'Depuração USB ativa, mas nenhum aparelho no cabo.'
      };
    }

    // 2. OBS (áudio) — só no modo gravar com áudio OBS
    if (sessionUseObs) {
      try {
        await obs.connect(`ws://${config.obsHost}:${config.obsPort}`, config.obsPassword);
      } catch (e) {
        // já conectado ou falha — StartRecord valida
      }

      try {
        await obs.call('StartRecord');
        obsStartTime = Date.now();
      } catch (e) {
        return {
          success: false,
          message: `Erro no OBS: ${e.message}\n\nVerifique se o WebSocket está ativo em OBS → Tools → WebSocket Server Settings`
        };
      }
    } else {
      obsStartTime = null;
    }

    // 3. Scrcpy
    const scrcpyArgs = [
      useWifi ? '-e' : '-d',
      '--no-audio',
    ];

    if (config.stayAwake) scrcpyArgs.push('--stay-awake');
    if (config.turnScreenOff) scrcpyArgs.push('--turn-screen-off');
    if (config.maxFps && config.maxFps !== '0') {
      scrcpyArgs.push('--max-fps', config.maxFps);
    }
    if (config.maxSize && config.maxSize !== '0') {
      scrcpyArgs.push('--max-size', config.maxSize);
    }
    scrcpyArgs.push('--video-bit-rate', config.bitrate);

    if (sessionMode === 'record') {
      config.recordVideoPath = buildRecordPath(config.recordDir, config.format);
      scrcpyArgs.push('--record', config.recordVideoPath);
    } else {
      config.recordVideoPath = null;
    }

    let scrcpyErrorOutput = '';
    scrcpyStartTime = Date.now();
    scrcpyProcess = spawn(config.scrcpyPath, scrcpyArgs, {
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: false
    });

    const onScrcpyLog = (d) => {
      const text = d.toString();
      scrcpyErrorOutput += text;
      if (text.includes('Recording started') || text.includes('Texture:')) {
        scrcpyStartTime = Date.now();
      }
    };

    scrcpyProcess.stdout.on('data', onScrcpyLog);
    scrcpyProcess.stderr.on('data', onScrcpyLog);

    scrcpyProcess.on('error', (err) => {
      mainWindow?.webContents.send('recording-error', `Scrcpy não encontrado: ${err.message}`);
    });

    scrcpyProcess.on('close', (code) => {
      if (isRecording && !stoppingIntentionally && code !== 0) {
        const errorLines = scrcpyErrorOutput
          .split('\n')
          .filter((l) => /ERROR/i.test(l))
          .join('\n');
        mainWindow?.webContents.send(
          'recording-error',
          `Scrcpy falhou (código ${code}):\n${errorLines || scrcpyErrorOutput.trim().slice(-300) || 'erro desconhecido'}`
        );
        stopEverything();
      } else if (isRecording && !stoppingIntentionally && code === 0 && sessionMode === 'capture') {
        stopEverything();
      }
    });

    isRecording = true;
    const payload = {
      mode: sessionMode,
      videoPath: config.recordVideoPath,
    };
    mainWindow?.webContents.send('recording-started', payload);

    return {
      success: true,
      message: sessionMode === 'record' ? 'Gravação iniciada!' : 'Captura iniciada!',
      ...payload
    };
  } catch (error) {
    return { success: false, message: error.message };
  }
}

ipcMain.handle('stop-recording', async () => {
  return await stopEverything();
});

function waitForClose(proc, timeoutMs = 8000) {
  return new Promise((resolve) => {
    if (!proc || proc.exitCode !== null) return resolve();
    const timer = setTimeout(resolve, timeoutMs);
    proc.once('close', () => {
      clearTimeout(timer);
      resolve();
    });
  });
}

async function stopEverything() {
  if (!isRecording && !scrcpyProcess) {
    return { success: true, message: 'Nada estava em execução.' };
  }

  stoppingIntentionally = true;
  const wasRecord = sessionMode === 'record';
  const usedObs = sessionUseObs;

  let obsAudioPath = null;
  if (usedObs) {
    try {
      const stopResp = await obs.call('StopRecord');
      obsAudioPath = stopResp?.outputPath || null;
    } catch (e) {
      // pode já estar parado
    }
  }

  if (scrcpyProcess) {
    const proc = scrcpyProcess;
    scrcpyProcess = null;

    try {
      if (process.platform === 'win32') {
        exec(`taskkill /pid ${proc.pid} /T`);
      } else {
        proc.kill('SIGINT');
      }
    } catch (e) {
      // ignore
    }

    await waitForClose(proc, 5000);

    if (proc.exitCode === null) {
      try {
        if (process.platform === 'win32') {
          exec(`taskkill /pid ${proc.pid} /T /F`);
        } else {
          proc.kill('SIGKILL');
        }
      } catch (e) {
        // ignore
      }
      await waitForClose(proc, 3000);
    }
  }

  isRecording = false;
  mainWindow?.webContents.send('recording-stopped');

  if (!wasRecord) {
    return { success: true, message: 'Captura encerrada.', videoPath: null };
  }

  await new Promise((r) => setTimeout(r, 500));

  if (obsAudioPath && config.recordVideoPath) {
    const syncedPath = config.recordVideoPath.replace(/\.(mkv|mp4)$/i, '-sync.$1');
    const merge = await mergeVideoAudio(config.recordVideoPath, obsAudioPath, syncedPath);
    if (merge.success) {
      return {
        success: true,
        message: 'Arquivo sincronizado gerado!',
        videoPath: syncedPath
      };
    }
    return {
      success: true,
      message: `Gravação salva, mas a mesclagem falhou: ${merge.message}`,
      videoPath: config.recordVideoPath
    };
  }

  return {
    success: true,
    message: 'Gravação parada com sucesso!',
    videoPath: config.recordVideoPath
  };
}

function mergeVideoAudio(videoPath, audioPath, outputPath) {
  return new Promise((resolve) => {
    const fs = require('fs');
    if (!fs.existsSync(videoPath)) {
      return resolve({ success: false, message: `Vídeo não encontrado: ${videoPath}` });
    }
    if (!fs.existsSync(audioPath)) {
      return resolve({ success: false, message: `Áudio do OBS não encontrado: ${audioPath}` });
    }

    const offsetSec = (obsStartTime && scrcpyStartTime && scrcpyStartTime > obsStartTime)
      ? (scrcpyStartTime - obsStartTime) / 1000
      : 0;

    const args = [
      '-y',
      '-i', videoPath,
      '-ss', offsetSec.toFixed(3),
      '-i', audioPath,
      '-map', '0:v:0',
      '-map', '1:a:0?',
      '-c:v', 'copy',
      '-c:a', 'aac',
      '-shortest',
      outputPath,
    ];

    const ff = spawn(config.ffmpegPath, args, { stdio: ['ignore', 'ignore', 'pipe'] });
    let stderr = '';
    ff.stderr.on('data', (d) => { stderr += d.toString(); });
    ff.on('error', (err) => resolve({ success: false, message: `ffmpeg não encontrado: ${err.message}` }));
    ff.on('close', (code) => {
      if (code === 0) {
        resolve({ success: true });
      } else {
        const lastLines = stderr.trim().split('\n').slice(-5).join('\n');
        resolve({ success: false, message: lastLines || `ffmpeg saiu com código ${code}` });
      }
    });
  });
}

ipcMain.handle('choose-folder', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory']
  });
  if (!result.canceled) {
    config.recordDir = result.filePaths[0];
    return { dir: config.recordDir, preview: buildRecordPath(config.recordDir) };
  }
  return null;
});
