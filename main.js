const { app, BrowserWindow, ipcMain, dialog, shell, protocol, net } = require('electron');
const path = require('path');
const fs = require('fs');
const { pathToFileURL } = require('url');
const { spawn, exec, execFile, execFileSync } = require('child_process');
const { OBSWebSocket } = require('obs-websocket-js');
const { autoUpdater } = require('electron-updater');
const { startRemoteServer } = require('./remote-server');

protocol.registerSchemesAsPrivileged([
  {
    scheme: 'cleven-preview',
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      stream: true,
      bypassCSP: true,
    },
  },
]);

let mainWindow;
let splashWindow = null;
let scrcpyProcess = null;
let isRecording = false;
let stoppingIntentionally = false;
let obsStartTime = null;
let scrcpyStartTime = null;
let sessionMode = 'capture'; // 'capture' | 'record'
let sessionAudioSource = 'pc'; // 'none' | 'pc' | 'phone'
let sessionUseObs = true;
let sessionObsLaunchedByApp = false;
let sessionObsPid = null;
let remoteInfo = { urls: [], primaryUrl: null, port: 8787 };
let pathCache = null;
const whichCache = new Map();
const RELEASES_URL = 'https://github.com/clevencode/clevenrec/releases';

function isPortableBuild() {
  if (process.env.PORTABLE_EXECUTABLE_DIR) return true;
  try {
    return /-portable(?:\.exe)?$/i.test(app.getPath('exe'));
  } catch (_) {
    return false;
  }
}

let updateState = {
  status: 'idle',
  currentVersion: app.getVersion(),
  version: null,
  percent: 0,
  message: '',
  isPortable: false,
};

function sendUpdateStatus() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.webContents.send('update-status', { ...updateState });
}

function setUpdateStatus(patch) {
  updateState = { ...updateState, ...patch };
  sendUpdateStatus();
}

function initAutoUpdater() {
  updateState.isPortable = isPortableBuild();
  updateState.currentVersion = app.getVersion();

  if (!app.isPackaged) {
    setUpdateStatus({
      status: 'dev',
      message: 'Atualizações automáticas ficam ativas na versão instalada (NSIS).',
    });
    return;
  }

  if (isPortableBuild()) {
    setUpdateStatus({
      status: 'unsupported',
      message: 'Versão portable: baixe o novo instalador em GitHub Releases.',
    });
    return;
  }

  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = false;

  autoUpdater.on('checking-for-update', () => {
    setUpdateStatus({ status: 'checking', message: 'Verificando atualizações…', percent: 0 });
  });

  autoUpdater.on('update-available', (info) => {
    setUpdateStatus({
      status: 'downloading',
      version: info.version,
      message: `Baixando ${info.version}…`,
      percent: 0,
    });
  });

  autoUpdater.on('update-not-available', () => {
    setUpdateStatus({
      status: 'not-available',
      message: 'Você já está na versão mais recente.',
      percent: 0,
    });
  });

  autoUpdater.on('download-progress', (progress) => {
    const percent = Math.round(progress.percent || 0);
    setUpdateStatus({
      status: 'downloading',
      message: `Baixando ${updateState.version || 'atualização'}… ${percent}%`,
      percent,
    });
  });

  autoUpdater.on('update-downloaded', (info) => {
    setUpdateStatus({
      status: 'downloaded',
      version: info.version,
      message: `Versão ${info.version} pronta para instalar.`,
      percent: 100,
    });
  });

  autoUpdater.on('error', (error) => {
    setUpdateStatus({
      status: 'error',
      message: error?.message || 'Falha ao verificar atualização.',
    });
  });
}

async function checkForAppUpdates() {
  updateState.currentVersion = app.getVersion();

  if (!app.isPackaged) {
    setUpdateStatus({
      status: 'dev',
      message: 'Atualizações automáticas ficam ativas na versão instalada (NSIS).',
    });
    return { success: false, status: { ...updateState } };
  }

  if (isPortableBuild()) {
    setUpdateStatus({
      status: 'unsupported',
      message: 'Versão portable: baixe o novo instalador em GitHub Releases.',
    });
    return { success: false, status: { ...updateState } };
  }

  try {
    await autoUpdater.checkForUpdates();
    return { success: true, status: { ...updateState } };
  } catch (error) {
    setUpdateStatus({
      status: 'error',
      message: error?.message || 'Não foi possível verificar atualizações.',
    });
    return { success: false, status: { ...updateState } };
  }
}

function which(cmd) {
  if (whichCache.has(cmd)) return whichCache.get(cmd);
  try {
    const bin = process.platform === 'win32' ? 'where.exe' : 'which';
    const out = execFileSync(bin, [cmd], { encoding: 'utf8' });
    const found = out.split(/\r?\n/).map((s) => s.trim()).find((s) => s && fs.existsSync(s)) || null;
    whichCache.set(cmd, found);
    return found;
  } catch (_) {
    whichCache.set(cmd, null);
    return null;
  }
}

function firstExisting(candidates) {
  for (const candidate of candidates) {
    if (candidate && fs.existsSync(candidate)) return candidate;
  }
  return null;
}

function findInWingetPackages(exeName, packageHint) {
  const localAppData = process.env.LOCALAPPDATA || '';
  const wingetRoot = path.join(localAppData, 'Microsoft', 'WinGet', 'Packages');
  if (!fs.existsSync(wingetRoot)) return null;

  try {
    const hint = new RegExp(packageHint, 'i');
    const target = exeName.toLowerCase();
    const packages = fs.readdirSync(wingetRoot).filter((name) => hint.test(name));

    for (const pkg of packages) {
      const pkgDir = path.join(wingetRoot, pkg);
      const stack = [{ dir: pkgDir, depth: 0 }];
      while (stack.length) {
        const { dir, depth } = stack.pop();
        if (depth > 3) continue;
        let entries = [];
        try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch (_) { continue; }
        for (const entry of entries) {
          const full = path.join(dir, entry.name);
          if (entry.isFile() && entry.name.toLowerCase() === target) {
            return full;
          }
          if (entry.isDirectory()) {
            stack.push({ dir: full, depth: depth + 1 });
          }
        }
      }
    }
    return null;
  } catch (_) {
    return null;
  }
}

function resolveScrcpyPath() {
  if (process.env.SCRCPY_PATH && fs.existsSync(process.env.SCRCPY_PATH)) {
    return process.env.SCRCPY_PATH;
  }

  return firstExisting([
    which('scrcpy'),
    which('scrcpy.exe'),
    findInWingetPackages('scrcpy.exe', 'scrcpy'),
  ]) || 'scrcpy';
}

function resolveFfmpegPath() {
  if (process.env.FFMPEG_PATH && fs.existsSync(process.env.FFMPEG_PATH)) {
    return process.env.FFMPEG_PATH;
  }

  const localAppData = process.env.LOCALAPPDATA || '';
  const programFiles = process.env.ProgramFiles || 'C:\\Program Files';
  const programFilesX86 = process.env['ProgramFiles(x86)'] || 'C:\\Program Files (x86)';

  return firstExisting([
    which('ffmpeg'),
    which('ffmpeg.exe'),
    path.join(localAppData, 'Microsoft', 'WinGet', 'Links', 'ffmpeg.exe'),
    findInWingetPackages('ffmpeg.exe', 'ffmpeg|gyan'),
    path.join(programFiles, 'ffmpeg', 'bin', 'ffmpeg.exe'),
    path.join(programFilesX86, 'ffmpeg', 'bin', 'ffmpeg.exe'),
  ]) || 'ffmpeg';
}

function resolveAdbPath(scrcpyPath = config.scrcpyPath) {
  const localAppData = process.env.LOCALAPPDATA || '';
  const besideScrcpy = (scrcpyPath && scrcpyPath.includes(path.sep))
    ? path.join(path.dirname(scrcpyPath), process.platform === 'win32' ? 'adb.exe' : 'adb')
    : null;

  return firstExisting([
    besideScrcpy,
    which('adb'),
    which('adb.exe'),
    path.join(localAppData, 'Microsoft', 'WinGet', 'Links', 'adb.exe'),
    findInWingetPackages('adb.exe', 'scrcpy|android'),
  ]) || 'adb';
}

function resolveDefaultRecordDir() {
  try {
    return path.join(app.getPath('downloads'), 'screencopy');
  } catch (_) {
    return path.join(process.env.USERPROFILE || process.cwd(), 'Downloads', 'screencopy');
  }
}

function resolveObsPath() {
  if (process.env.OBS_PATH && fs.existsSync(process.env.OBS_PATH)) {
    return process.env.OBS_PATH;
  }

  const programFiles = process.env.ProgramFiles || 'C:\\Program Files';
  const programFilesX86 = process.env['ProgramFiles(x86)'] || 'C:\\Program Files (x86)';
  const localAppData = process.env.LOCALAPPDATA || '';

  return firstExisting([
    which('obs64'),
    which('obs64.exe'),
    path.join(programFiles, 'obs-studio', 'bin', '64bit', 'obs64.exe'),
    path.join(programFilesX86, 'obs-studio', 'bin', '64bit', 'obs64.exe'),
    path.join(localAppData, 'Programs', 'obs-studio', 'bin', '64bit', 'obs64.exe'),
    findInWingetPackages('obs64.exe', 'obs'),
  ]);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isObsProcessRunning() {
  try {
    const out = execFileSync('tasklist', ['/FI', 'IMAGENAME eq obs64.exe', '/FO', 'CSV', '/NH'], {
      encoding: 'utf8',
      windowsHide: true,
    });
    return /obs64\.exe/i.test(out);
  } catch (_) {
    return false;
  }
}

/** Garante WebSocket ligado (uma vez) para o ClevenRec conectar sem intervenção. */
function getObsWebsocketConfigPath() {
  const appData = process.env.APPDATA || '';
  if (!appData) return null;
  return path.join(appData, 'obs-studio', 'plugin_config', 'obs-websocket', 'config.json');
}

function readObsWebsocketConfig() {
  const cfgPath = getObsWebsocketConfigPath();
  if (!cfgPath || !fs.existsSync(cfgPath)) return {};
  try {
    return JSON.parse(fs.readFileSync(cfgPath, 'utf8'));
  } catch (_) {
    return {};
  }
}

function syncObsPasswordFromConfig() {
  const current = readObsWebsocketConfig();
  if (typeof current.server_port === 'number') {
    config.obsPort = current.server_port;
  }
  if (current.auth_required && current.server_password) {
    config.obsPassword = String(current.server_password);
  } else if (!config.obsPassword) {
    config.obsPassword = '';
  }
}

function ensureObsWebsocketConfig() {
  const cfgPath = getObsWebsocketConfigPath();
  if (!cfgPath) return;

  const dir = path.dirname(cfgPath);
  const current = readObsWebsocketConfig();

  const next = {
    ...current,
    server_enabled: true,
    server_port: Number(current.server_port) || 4455,
  };

  // Só força senha vazia se ainda não houver senha configurada
  if (next.server_password == null) next.server_password = '';
  if (typeof next.auth_required !== 'boolean') {
    next.auth_required = Boolean(next.server_password);
  }

  const changed = JSON.stringify(current) !== JSON.stringify(next);
  if (!changed && fs.existsSync(cfgPath)) {
    syncObsPasswordFromConfig();
    return;
  }

  try {
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(cfgPath, `${JSON.stringify(next, null, 2)}\n`, 'utf8');
    syncObsPasswordFromConfig();
  } catch (err) {
    console.warn('Não foi possível ajustar config do OBS WebSocket:', err.message);
  }
}

async function tryConnectObs() {
  try {
    await obs.connect(`ws://${config.obsHost}:${config.obsPort}`, config.obsPassword || undefined);
    return true;
  } catch (_) {
    try {
      await obs.call('GetVersion');
      return true;
    } catch (_) {
      return false;
    }
  }
}

function launchObsInBackground() {
  const obsPath = resolveObsPath();
  if (!obsPath) {
    return {
      success: false,
      message:
        'OBS Studio não encontrado.\n\nInstale o OBS ou defina OBS_PATH com o caminho do obs64.exe.',
    };
  }

  ensureObsWebsocketConfig();

  try {
    const child = spawn(
      obsPath,
      ['--minimize-to-tray', '--disable-shutdown-check'],
      {
        cwd: path.dirname(obsPath),
        detached: true,
        stdio: 'ignore',
        windowsHide: false,
      }
    );
    child.unref();
    return { success: true, obsPath, pid: child.pid || null };
  } catch (err) {
    return {
      success: false,
      message: `Não foi possível iniciar o OBS: ${err.message}`,
    };
  }
}

/**
 * Conecta ao OBS; se não estiver aberto, sobe em segundo plano (bandeja)
 * e espera o WebSocket ficar pronto.
 */
async function ensureObsReady({ timeoutMs = 45000 } = {}) {
  syncObsPasswordFromConfig();
  if (await tryConnectObs()) {
    return { success: true, launched: false, pid: null };
  }

  const alreadyRunning = isObsProcessRunning();
  let launchedPid = null;
  if (!alreadyRunning) {
    mainWindow?.webContents.send('status-text', 'Abrindo OBS em segundo plano…');
    const launched = launchObsInBackground();
    if (!launched.success) return launched;
    launchedPid = launched.pid || null;
  } else {
    // OBS aberto mas WS ainda não — tenta habilitar config para próximo boot
    ensureObsWebsocketConfig();
    mainWindow?.webContents.send('status-text', 'Conectando ao OBS…');
  }

  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    await sleep(900);
    syncObsPasswordFromConfig();
    if (await tryConnectObs()) {
      return {
        success: true,
        launched: !alreadyRunning,
        pid: alreadyRunning ? null : launchedPid,
      };
    }
  }

  // Falhou: se nós abrimos o OBS, fecha para não deixar órfão
  if (!alreadyRunning && launchedPid) {
    await quitObsProcess(launchedPid);
  }

  return {
    success: false,
    message:
      'OBS não respondeu a tempo.\n\n' +
      'Se for a 1ª vez: abra o OBS → Tools → WebSocket Server Settings → Enable → OK, ' +
      'feche o OBS e tente de novo. O ClevenRec passa a abri-lo sozinho.',
  };
}

function quitObsProcess(pid) {
  return new Promise((resolve) => {
    if (!pid) {
      // fallback: só se tivermos certeza que o app subiu o OBS
      if (process.platform === 'win32') {
        exec('taskkill /IM obs64.exe /T', () => resolve());
      } else {
        exec('pkill -x obs || true', () => resolve());
      }
      return;
    }
    try {
      if (process.platform === 'win32') {
        exec(`taskkill /PID ${pid} /T`, () => {
          setTimeout(() => {
            exec(`taskkill /PID ${pid} /T /F`, () => resolve());
          }, 800);
        });
      } else {
        try { process.kill(pid, 'SIGTERM'); } catch (_) {}
        setTimeout(() => {
          try { process.kill(pid, 'SIGKILL'); } catch (_) {}
          resolve();
        }, 800);
      }
    } catch (_) {
      resolve();
    }
  });
}

async function closeObsIfLaunchedByApp() {
  if (!sessionObsLaunchedByApp) return;
  const pid = sessionObsPid;
  sessionObsLaunchedByApp = false;
  sessionObsPid = null;

  try {
    await obs.disconnect();
  } catch (_) {
    // ignore
  }

  await quitObsProcess(pid);
  console.log('OBS fechado (foi aberto pelo ClevenRec).');
}

const config = {
  scrcpyPath: 'scrcpy',
  adbPath: 'adb',
  obsPath: '',
  recordDir: '',
  recordVideoPath: '',
  obsHost: 'localhost',
  obsPort: 4455,
  obsPassword: '',
  ffmpegPath: 'ffmpeg',
  // defaults do painel
  mode: 'capture',
  connection: 'usb', // 'usb' | 'wifi'
  wifiAddress: '',
  activeSerial: '', // serial ADB escolhido (USB ou host:porta)
  bitrate: '6000K',
  maxFps: '30',
  format: 'mkv',
  maxSize: '0',
  audioSource: 'pc-desktop', // 'none' | 'pc-desktop' | 'pc-mic' | 'pc-both' | 'phone'
  useObsAudio: true, // legado: true quando audioSource começa com 'pc'
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
    backgroundColor: '#14171d',
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

const WINDOW_NARROW_WIDTH = 480;
const WINDOW_TRANSFER_WIDTH = 380;
const WINDOW_SHELL_GAP = 34; // gap 14 + padding lateral
const WINDOW_EXPANDED_WIDTH = WINDOW_NARROW_WIDTH + WINDOW_TRANSFER_WIDTH + WINDOW_SHELL_GAP;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: WINDOW_EXPANDED_WIDTH,
    height: 820,
    minWidth: 420,
    minHeight: 640,
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

  // Ao sair de maximize/fullscreen, volta à largura dos dois painéis.
  const restorePanelWidth = () => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    if (mainWindow.isMaximized() || mainWindow.isFullScreen()) return;
    const [w, h] = mainWindow.getSize();
    if (w > WINDOW_EXPANDED_WIDTH + 40) {
      mainWindow.setSize(WINDOW_EXPANDED_WIDTH, h);
    }
  };

  mainWindow.on('unmaximize', restorePanelWidth);
  mainWindow.on('leave-full-screen', restorePanelWidth);

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

function normalizeAudioSource(settings = {}, fallback = config.audioSource || 'pc-desktop') {
  const raw = settings.audioSource != null ? String(settings.audioSource) : '';
  if (raw === 'none' || raw === 'pc-desktop' || raw === 'pc-mic' || raw === 'pc-both' || raw === 'phone') {
    return raw;
  }
  if (raw === 'pc') return 'pc-desktop'; // legado
  if (typeof settings.useObsAudio === 'boolean') {
    return settings.useObsAudio ? 'pc-desktop' : 'none';
  }
  if (fallback === 'pc') return 'pc-desktop';
  if (
    fallback === 'none'
    || fallback === 'pc-desktop'
    || fallback === 'pc-mic'
    || fallback === 'pc-both'
    || fallback === 'phone'
  ) {
    return fallback;
  }
  return 'pc-desktop';
}

function isPcAudioSource(audioSource) {
  return audioSource === 'pc-desktop' || audioSource === 'pc-mic' || audioSource === 'pc-both';
}

/**
 * Roteia áudio no OBS: interno, microfone ou ambos.
 */
async function applyObsAudioRouting(audioSource) {
  const wantDesktop = audioSource === 'pc-desktop' || audioSource === 'pc-both';
  const wantMic = audioSource === 'pc-mic' || audioSource === 'pc-both';

  let desktopNames = [];
  let micNames = [];

  try {
    const special = await obs.call('GetSpecialInputs');
    desktopNames = [special.desktop1, special.desktop2].filter(Boolean);
    micNames = [special.mic1, special.mic2, special.mic3, special.mic4].filter(Boolean);
  } catch (_) {
    // segue para fallback por tipo
  }

  try {
    const listed = await obs.call('GetInputList');
    const inputs = listed?.inputs || [];
    for (const input of inputs) {
      const kind = String(input.inputKind || '');
      const name = input.inputName;
      if (!name) continue;
      if (/wasapi_output_capture|pulse_output_capture|coreaudio_output_capture/i.test(kind)) {
        if (!desktopNames.includes(name)) desktopNames.push(name);
      }
      if (/wasapi_input_capture|pulse_input_capture|coreaudio_input_capture|alsa_input/i.test(kind)) {
        if (!micNames.includes(name)) micNames.push(name);
      }
    }
  } catch (_) {
    // ignore
  }

  if (wantDesktop && desktopNames.length === 0) {
    return {
      success: false,
      message:
        'OBS sem áudio interno (Desktop Audio).\n\n' +
        'No OBS: adicione “Áudio da Área de Trabalho” / Desktop Audio na cena.',
    };
  }
  if (wantMic && micNames.length === 0) {
    return {
      success: false,
      message:
        'OBS sem microfone (Mic/Aux).\n\n' +
        'No OBS: adicione uma fonte de Microfone / Mic/Aux na cena.',
    };
  }

  for (const name of desktopNames) {
    try {
      await obs.call('SetInputMute', { inputName: name, inputMuted: !wantDesktop });
    } catch (_) {
      // ignore individual failures
    }
  }
  for (const name of micNames) {
    try {
      await obs.call('SetInputMute', { inputName: name, inputMuted: !wantMic });
    } catch (_) {
      // ignore
    }
  }

  return { success: true, desktopNames, micNames };
}

function getSettingsSnapshot() {
  const audioSource = normalizeAudioSource({}, config.audioSource);
  return {
    mode: config.mode,
    connection: config.connection,
    wifiAddress: config.wifiAddress,
    bitrate: config.bitrate,
    maxFps: config.maxFps,
    format: config.format,
    maxSize: config.maxSize,
    audioSource,
    useObsAudio: isPcAudioSource(audioSource),
    stayAwake: config.stayAwake,
    turnScreenOff: config.turnScreenOff,
  };
}

function settingsFilePath() {
  return path.join(app.getPath('userData'), 'clevenrec-settings.json');
}

function loadPersistedSettings() {
  try {
    const file = settingsFilePath();
    if (!fs.existsSync(file)) return;
    const data = JSON.parse(fs.readFileSync(file, 'utf8'));
    if (data && typeof data === 'object') applySettings(data, { persist: false });
  } catch (err) {
    console.warn('Não foi possível carregar settings:', err?.message || err);
  }
}

function persistSettings() {
  try {
    fs.writeFileSync(settingsFilePath(), JSON.stringify(getSettingsSnapshot(), null, 2), 'utf8');
  } catch (err) {
    console.warn('Não foi possível salvar settings:', err?.message || err);
  }
}

function applySettings(settings = {}, { persist = true } = {}) {
  const audioSource = normalizeAudioSource(settings, config.audioSource);
  const wifiRaw = settings.wifiAddress != null ? String(settings.wifiAddress).trim() : config.wifiAddress;
  const wifi = normalizeWifiEndpoint(wifiRaw);
  Object.assign(config, {
    mode: settings.mode ?? config.mode,
    connection: settings.connection === 'wifi' || settings.connection === 'usb'
      ? settings.connection
      : config.connection,
    wifiAddress: wifi.empty ? (settings.wifiAddress != null ? '' : config.wifiAddress) : wifi.display,
    bitrate: settings.bitrate ?? config.bitrate,
    maxFps: String(settings.maxFps ?? config.maxFps),
    format: settings.format ?? config.format,
    maxSize: String(settings.maxSize ?? config.maxSize),
    audioSource,
    useObsAudio: isPcAudioSource(audioSource),
    stayAwake: settings.stayAwake ?? config.stayAwake,
    turnScreenOff: settings.turnScreenOff ?? config.turnScreenOff,
  });
  if (persist) persistSettings();
  return getSettingsSnapshot();
}

function runAdb(adbPath, args, { timeout = 15000 } = {}) {
  const argv = Array.isArray(args) ? args : String(args).match(/(?:[^\s"]+|"[^"]*")+/g)?.map((s) => s.replace(/^"|"$/g, '')) || [];
  return new Promise((resolve) => {
    execFile(adbPath, argv, { timeout, windowsHide: true, maxBuffer: 20 * 1024 * 1024 }, (err, stdout, stderr) => {
      resolve({
        ok: !err,
        stdout: (stdout || '').toString(),
        stderr: (stderr || '').toString(),
        error: err,
      });
    });
  });
}

/** Normaliza IP/endpoint Wi‑Fi (host + porta). Guarda só o host em config.wifiAddress. */
function normalizeWifiEndpoint(raw) {
  const value = String(raw || '')
    .trim()
    .replace(/\s+/g, '')
    .replace(/,/g, '.');
  if (!value) {
    return { empty: true, host: '', port: '5555', full: '', display: '' };
  }
  let host = value;
  let port = '5555';
  const colon = value.lastIndexOf(':');
  if (colon > 0) {
    const maybeHost = value.slice(0, colon);
    const maybePort = value.slice(colon + 1);
    if (/^\d{1,5}$/.test(maybePort) && Number(maybePort) >= 1 && Number(maybePort) <= 65535) {
      host = maybeHost;
      port = maybePort;
    }
  }
  return {
    empty: false,
    host,
    port,
    full: `${host}:${port}`,
    display: host,
  };
}

/**
 * Parseia `adb devices`.
 * @param {string} stdout
 * @param {{ readyOnly?: boolean }} [opts] readyOnly=true → só state=device (legado)
 */
function parseAdbDevices(stdout, { readyOnly = true } = {}) {
  const rows = [];
  for (const line of String(stdout || '').split('\n').slice(1)) {
    const l = line.trim();
    if (!l) continue;
    const parts = l.split(/\s+/);
    const serial = parts[0];
    const state = parts[1] || '';
    if (!serial || serial === 'List') continue;
    rows.push({
      serial,
      state,
      isWifi: serial.includes(':'),
      ready: state === 'device',
    });
  }
  return readyOnly ? rows.filter((d) => d.ready) : rows;
}

function pickDeviceForMode(devices, mode, wifiAddress = '') {
  const preferWifi = mode === 'wifi';
  const ready = (devices || []).filter((d) => d.ready !== false);
  if (preferWifi) {
    const ep = normalizeWifiEndpoint(wifiAddress);
    if (!ep.empty) {
      const exact = ready.find((d) => d.isWifi && (d.serial === ep.full || d.serial.startsWith(`${ep.host}:`)));
      if (exact) return exact;
    }
    return ready.find((d) => d.isWifi) || null;
  }
  return ready.find((d) => !d.isWifi) || null;
}

function describeAdbBlockers(allDevices, mode) {
  const scoped = (allDevices || []).filter((d) => (mode === 'wifi' ? d.isWifi : !d.isWifi));
  const unauthorized = scoped.filter((d) => d.state === 'unauthorized');
  const offline = scoped.filter((d) => d.state === 'offline');
  if (unauthorized.length) {
    return mode === 'usb'
      ? 'Celular no cabo, mas ADB não autorizado.\n\nDesbloqueie a tela e toque em “Permitir depuração USB”.'
      : `Dispositivo Wi‑Fi não autorizado (${unauthorized[0].serial}).\n\nReconecte com USB uma vez e aceite o aviso.`;
  }
  if (offline.length) {
    return mode === 'usb'
      ? 'Celular offline no ADB.\n\nTroque o cabo/porta USB ou desative/ative Depuração USB.'
      : `Wi‑Fi offline (${offline[0].serial}).\n\nToque em Conectar de novo ou use USB para reativar tcpip 5555.`;
  }
  return null;
}

function binaryLooksAvailable(binPath) {
  if (!binPath) return false;
  if (binPath.includes(path.sep) || binPath.includes('/') || binPath.includes('\\')) {
    return fs.existsSync(binPath);
  }
  // which() já está em cache — evita varredura duplicada no PATH
  return Boolean(which(binPath) || (process.platform === 'win32' && which(`${binPath}.exe`)));
}

function ensureRuntimePaths({ force = false } = {}) {
  if (pathCache && !force) {
    if (!config.recordDir) config.recordDir = pathCache.recordDir;
    return pathCache;
  }

  config.scrcpyPath = resolveScrcpyPath();
  config.ffmpegPath = resolveFfmpegPath();
  config.adbPath = resolveAdbPath(config.scrcpyPath);
  config.obsPath = resolveObsPath() || '';
  if (!config.recordDir) {
    config.recordDir = resolveDefaultRecordDir();
  }
  try {
    fs.mkdirSync(config.recordDir, { recursive: true });
  } catch (_) {
    // ignore
  }
  if (!config.recordVideoPath) {
    config.recordVideoPath = path.join(config.recordDir, 'screenvid.mkv');
  }

  pathCache = {
    scrcpyPath: config.scrcpyPath,
    ffmpegPath: config.ffmpegPath,
    adbPath: config.adbPath,
    obsPath: config.obsPath,
    recordDir: config.recordDir,
  };
  return pathCache;
}

function validateRuntimeBinaries({ needFfmpeg = false } = {}) {
  ensureRuntimePaths();

  if (!binaryLooksAvailable(config.scrcpyPath)) {
    return {
      success: false,
      message:
        'scrcpy não encontrado.\n\nInstale via winget (winget install Genymobile.scrcpy) ' +
        'ou defina a variável de ambiente SCRCPY_PATH com o caminho do scrcpy.exe.',
    };
  }
  if (!binaryLooksAvailable(config.adbPath)) {
    return {
      success: false,
      message:
        'adb não encontrado.\n\nEle costuma vir junto do scrcpy. Reinstale o scrcpy ' +
        'ou coloque adb.exe no PATH.',
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
  ensureRuntimePaths();
}

/** Alias estável — nunca remover; usado por USB/Wi‑Fi e gravação */
function getAdbPath() {
  ensureRuntimePaths();
  return config.adbPath;
}

async function safeIpc(handler, fallbackMessage) {
  try {
    return await handler();
  } catch (err) {
    console.error(fallbackMessage, err);
    return {
      success: false,
      message: err?.message || fallbackMessage,
    };
  }
}

async function detectDeviceWifiIp(adbPath, serial) {
  const base = serial ? ['-s', serial] : [];
  const scripts = [
    [...base, 'shell', 'ip -f inet addr show wlan0'],
    [...base, 'shell', 'ip -f inet addr show wlan1'],
    [...base, 'shell', 'getprop', 'dhcp.wlan0.ipaddress'],
    [...base, 'shell', 'ip route | grep wlan'],
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
 * USB  → garante aparelho pronto no cabo
 * Wi‑Fi → tcpip 5555 (se USB disponível) + adb connect IP:porta
 * Retorna devices (seriais prontos) + serial escolhido para scrcpy/transferência.
 */
async function activateConnection(connection, wifiAddress = '') {
  const adbPath = getAdbPath();
  const mode = connection === 'wifi' ? 'wifi' : 'usb';
  config.connection = mode;

  if (wifiAddress != null && String(wifiAddress).trim()) {
    const ep = normalizeWifiEndpoint(wifiAddress);
    config.wifiAddress = ep.empty ? '' : ep.display;
  }

  const listAll = async () => {
    const listed = await runAdb(adbPath, ['devices']);
    return parseAdbDevices(listed.stdout, { readyOnly: false });
  };

  if (mode === 'usb') {
    let all = await listAll();
    let usbReady = all.filter((d) => !d.isWifi && d.ready);

    // Só força `adb usb` se ainda não há USB pronto (evita reinício desnecessário)
    if (usbReady.length === 0) {
      await runAdb(adbPath, ['usb']);
      await sleep(1200);
      all = await listAll();
      usbReady = all.filter((d) => !d.isWifi && d.ready);
    }

    if (usbReady.length === 0) {
      const blocker = describeAdbBlockers(all, 'usb');
      return {
        success: false,
        connection: 'usb',
        message: blocker
          || 'Nenhum aparelho USB pronto.\n\nConecte o cabo, ative Depuração USB e aceite o aviso no celular.',
      };
    }

    const chosen = usbReady[0];
    config.activeSerial = chosen.serial;
    return {
      success: true,
      connection: 'usb',
      serial: chosen.serial,
      devices: usbReady.map((d) => d.serial),
      message: usbReady.length === 1
        ? `USB ativo · ${chosen.serial}`
        : `USB ativo · ${usbReady.length} dispositivos (usando ${chosen.serial})`,
    };
  }

  // --- Wi‑Fi / ADB TCP ---
  let all = await listAll();
  let usbReady = all.filter((d) => !d.isWifi && d.ready);
  const wanted = normalizeWifiEndpoint(config.wifiAddress);

  // Já conectado no endpoint desejado?
  const already = pickDeviceForMode(
    all.filter((d) => d.ready),
    'wifi',
    config.wifiAddress
  );
  if (already && (wanted.empty || already.serial === wanted.full || already.serial.startsWith(`${wanted.host}:`))) {
    config.activeSerial = already.serial;
    config.wifiAddress = normalizeWifiEndpoint(already.serial).display;
    return {
      success: true,
      connection: 'wifi',
      serial: already.serial,
      wifiAddress: config.wifiAddress,
      devices: all.filter((d) => d.isWifi && d.ready).map((d) => d.serial),
      message: `ADB Wi‑Fi já conectado · ${already.serial}`,
    };
  }

  // 1ª vez / reativação: USB no cabo → tcpip 5555 + descobrir IP
  if (usbReady.length > 0) {
    const serial = usbReady[0].serial;
    const tcpip = await runAdb(adbPath, ['-s', serial, 'tcpip', '5555']);
    const tcpOut = `${tcpip.stdout} ${tcpip.stderr}`;
    if (!/restarting|5555/i.test(tcpOut) && tcpip.error) {
      return {
        success: false,
        connection: 'wifi',
        message: `Falha ao ativar ADB Wi‑Fi (tcpip 5555).\n\n${tcpOut.trim() || 'Conecte o USB uma vez para autorizar.'}`,
      };
    }
    await sleep(1500);

    if (wanted.empty) {
      const ip = await detectDeviceWifiIp(adbPath, serial);
      if (ip) {
        config.wifiAddress = ip;
      }
    }
  }

  const targetEp = normalizeWifiEndpoint(config.wifiAddress);
  if (targetEp.empty) {
    all = await listAll();
    const wifiReady = all.filter((d) => d.isWifi && d.ready);
    if (wifiReady.length > 0) {
      const chosen = wifiReady[0];
      config.activeSerial = chosen.serial;
      config.wifiAddress = normalizeWifiEndpoint(chosen.serial).display;
      return {
        success: true,
        connection: 'wifi',
        serial: chosen.serial,
        wifiAddress: config.wifiAddress,
        devices: wifiReady.map((d) => d.serial),
        message: `ADB Wi‑Fi já conectado · ${chosen.serial}`,
      };
    }
    const blocker = describeAdbBlockers(all, 'usb');
    return {
      success: false,
      connection: 'wifi',
      message: blocker
        || 'Informe o IP do celular (ex.: 192.168.0.20).\n\nNa 1ª vez: deixe o USB conectado — o app ativa tcpip 5555 e tenta detectar o IP.',
    };
  }

  const target = targetEp.full;
  const connect = await runAdb(adbPath, ['connect', target]);
  const connectOut = `${connect.stdout} ${connect.stderr}`;
  if (!/connected|already connected/i.test(connectOut)) {
    all = await listAll();
    const blocker = describeAdbBlockers(all, 'wifi');
    return {
      success: false,
      connection: 'wifi',
      wifiAddress: config.wifiAddress,
      message: blocker
        || `Não conectou em ${target}.\n\n1. USB uma vez + Depuração USB\n2. Mesmo Wi‑Fi do PC\n3. Confirme o IP\n\n${connectOut.trim()}`,
    };
  }

  all = await listAll();
  const wifiReady = all.filter((d) => d.isWifi && d.ready);
  const chosen = pickDeviceForMode(wifiReady, 'wifi', target) || wifiReady[0] || null;

  if (!chosen) {
    return {
      success: false,
      connection: 'wifi',
      wifiAddress: config.wifiAddress,
      message: `adb connect ok em ${target}, mas o dispositivo ainda não aparece como “device”.\n\nAguarde 2s e toque em Conectar de novo.`,
    };
  }

  config.activeSerial = chosen.serial;
  config.wifiAddress = normalizeWifiEndpoint(chosen.serial).display;

  return {
    success: true,
    connection: 'wifi',
    serial: chosen.serial,
    wifiAddress: config.wifiAddress,
    devices: wifiReady.map((d) => d.serial),
    message: `ADB Wi‑Fi ativo · ${chosen.serial}`,
  };
}

function getStatusPayload() {
  return {
    isRecording,
    mode: sessionMode,
    audioSource: isRecording ? sessionAudioSource : config.audioSource,
    useObsAudio: isRecording ? sessionUseObs : isPcAudioSource(config.audioSource),
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

  protocol.handle('cleven-preview', (request) => {
    try {
      const parsed = new URL(request.url);
      const fileName = parsed.searchParams.get('f');
      if (!fileName || fileName.includes('..') || /[\\/]/.test(fileName)) {
        return new Response('Forbidden', { status: 403 });
      }
      const previewDir = path.resolve(getPreviewDir());
      const full = path.resolve(previewDir, fileName);
      const rel = path.relative(previewDir, full);
      if (rel.startsWith('..') || path.isAbsolute(rel)) {
        return new Response('Forbidden', { status: 403 });
      }
      if (!fs.existsSync(full)) {
        return new Response('Not found', { status: 404 });
      }
      return net.fetch(pathToFileURL(full).href);
    } catch (err) {
      return new Response(err?.message || 'error', { status: 500 });
    }
  });

  initRuntimePaths();
  loadPersistedSettings();
  createSplash();
  createWindow();
  initAutoUpdater();
  try {
    remoteInfo = await startRemoteServer({
      getStatus: getStatusPayload,
      saveSettings: (settings) => ({ success: true, settings: applySettings(settings) }),
      activateConnection: (body = {}) => safeIpc(
        () => {
          ensureRuntimePaths();
          return activateConnection(body.connection || config.connection, body.wifiAddress);
        },
        'Falha ao ativar conexão'
      ),
      start: (options) => safeIpc(
        () => {
          ensureRuntimePaths();
          return startSession(options || {});
        },
        'Falha ao iniciar sessão'
      ),
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
    console.log('adb:', config.adbPath);
    console.log('ffmpeg:', config.ffmpegPath);
    console.log('obs:', config.obsPath || '(não encontrado)');
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
  return safeIpc(async () => {
    const connection = payload.connection || config.connection;
    const wifiAddress = payload.wifiAddress != null ? payload.wifiAddress : config.wifiAddress;
    ensureRuntimePaths();
    return activateConnection(connection, wifiAddress);
  }, 'Falha ao ativar conexão');
});

ipcMain.handle('start-recording', async (_event, options = {}) => {
  return safeIpc(async () => {
    ensureRuntimePaths();
    return startSession(options);
  }, 'Falha ao iniciar sessão');
});

async function startSession(options = {}) {
  if (isRecording) return { success: false, message: 'Já está em execução!' };

  applySettings(options);

  sessionMode = config.mode;
  sessionAudioSource = sessionMode === 'record'
    ? normalizeAudioSource({}, config.audioSource)
    : 'none';
  sessionUseObs = isPcAudioSource(sessionAudioSource);

  try {
    stoppingIntentionally = false;

    const bins = validateRuntimeBinaries({ needFfmpeg: sessionUseObs });
    if (!bins.success) return bins;

    // Ativa depuração USB ou ADB Wi‑Fi conforme o toggle
    const activated = await activateConnection(config.connection, config.wifiAddress);
    if (!activated.success) {
      return activated;
    }
    if (activated.wifiAddress) {
      config.wifiAddress = normalizeWifiEndpoint(activated.wifiAddress).display;
    }

    const useWifi = config.connection === 'wifi';
    let matching = (activated.devices || []).map((serial) => ({
      serial,
      isWifi: String(serial).includes(':'),
      ready: true,
    })).filter((d) => (useWifi ? d.isWifi : !d.isWifi));

    if (matching.length === 0) {
      const listed = await runAdb(getAdbPath(), ['devices']);
      matching = parseAdbDevices(listed.stdout).filter((d) => (useWifi ? d.isWifi : !d.isWifi));
    }

    const chosen =
      (activated.serial && matching.find((d) => d.serial === activated.serial))
      || pickDeviceForMode(matching, useWifi ? 'wifi' : 'usb', config.wifiAddress)
      || matching[0]
      || null;

    if (!chosen) {
      return {
        success: false,
        message: useWifi
          ? 'ADB Wi‑Fi ativo, mas nenhum dispositivo TCP encontrado.'
          : 'Depuração USB ativa, mas nenhum aparelho no cabo.',
      };
    }

    config.activeSerial = chosen.serial;

    // 2. OBS (áudio do PC: interno ou microfone)
    sessionObsLaunchedByApp = false;
    sessionObsPid = null;
    if (sessionUseObs) {
      const obsReady = await ensureObsReady({ timeoutMs: 45000 });
      if (!obsReady.success) {
        return {
          ...obsReady,
          message:
            `${obsReady.message}\n\n` +
            'Dica: troque a fonte de áudio para “Celular” (sem OBS) ou “Só vídeo”.',
        };
      }

      if (obsReady.launched) {
        sessionObsLaunchedByApp = true;
        sessionObsPid = obsReady.pid || null;
      }

      const routed = await applyObsAudioRouting(sessionAudioSource);
      if (!routed.success) {
        await closeObsIfLaunchedByApp();
        return routed;
      }

      try {
        await obs.call('StartRecord');
        obsStartTime = Date.now();
      } catch (e) {
        await closeObsIfLaunchedByApp();
        return {
          success: false,
          message:
            `Erro ao iniciar gravação no OBS: ${e.message}\n\n` +
            'Confirme cena/fonte de áudio no OBS, ou use áudio do Celular.',
        };
      }
    } else {
      obsStartTime = null;
    }

    // 3. Scrcpy — sempre -s <serial> (alinhado ao modo USB/Wi‑Fi escolhido)
    const scrcpyArgs = ['-s', chosen.serial];

    if (sessionAudioSource === 'phone') {
      // Áudio interno do celular (playback) embutido no mesmo arquivo
      scrcpyArgs.push('--audio-source=output');
    } else {
      scrcpyArgs.push('--no-audio');
    }

    if (config.stayAwake) scrcpyArgs.push('--stay-awake');
    if (config.turnScreenOff) scrcpyArgs.push('--turn-screen-off');
    if (config.maxFps && config.maxFps !== '0') {
      scrcpyArgs.push('--max-fps', config.maxFps);
    }
    if (config.maxSize && config.maxSize !== '0') {
      scrcpyArgs.push('--max-size', config.maxSize);
    }
    scrcpyArgs.push('--video-bit-rate', config.bitrate);
    scrcpyArgs.push('--window-title', 'ClevenRec');

    if (sessionMode === 'record') {
      config.recordVideoPath = buildRecordPath(config.recordDir, config.format);
      scrcpyArgs.push('--record', config.recordVideoPath);
    } else {
      config.recordVideoPath = null;
    }

    let scrcpyErrorOutput = '';
    const SCRCPY_LOG_CAP = 8000;
    scrcpyStartTime = Date.now();
    scrcpyProcess = spawn(config.scrcpyPath, scrcpyArgs, {
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: false
    });

    const onScrcpyLog = (d) => {
      const text = d.toString();
      scrcpyErrorOutput += text;
      if (scrcpyErrorOutput.length > SCRCPY_LOG_CAP) {
        scrcpyErrorOutput = scrcpyErrorOutput.slice(-SCRCPY_LOG_CAP);
      }
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
      audioSource: sessionAudioSource,
      useObsAudio: sessionUseObs,
      videoPath: config.recordVideoPath,
    };
    mainWindow?.webContents.send('recording-started', payload);

    const startMsg = sessionMode !== 'record'
      ? 'Captura iniciada!'
      : sessionAudioSource === 'pc-desktop'
        ? 'Gravação iniciada (vídeo + áudio interno do PC)!'
        : sessionAudioSource === 'pc-mic'
          ? 'Gravação iniciada (vídeo + microfone do PC)!'
          : sessionAudioSource === 'pc-both'
            ? 'Gravação iniciada (vídeo + áudio interno e microfone)!'
            : sessionAudioSource === 'phone'
              ? 'Gravação iniciada (vídeo + áudio do celular)!'
              : 'Gravação iniciada (só vídeo)!';

    return {
      success: true,
      message: startMsg,
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
  const shouldCloseObs = sessionObsLaunchedByApp;

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
    if (shouldCloseObs) await closeObsIfLaunchedByApp();
    return { success: true, message: 'Captura encerrada.', videoPath: null };
  }

  await new Promise((r) => setTimeout(r, 500));

  let result;
  if (obsAudioPath && config.recordVideoPath) {
    const syncedPath = config.recordVideoPath.replace(/\.(mkv|mp4)$/i, '-sync.$1');
    const merge = await mergeVideoAudio(config.recordVideoPath, obsAudioPath, syncedPath);
    if (merge.success) {
      result = {
        success: true,
        message: 'Arquivo sincronizado gerado!',
        videoPath: syncedPath
      };
    } else {
      result = {
        success: true,
        message: `Gravação salva, mas a mesclagem falhou: ${merge.message}`,
        videoPath: config.recordVideoPath
      };
    }
  } else {
    result = {
      success: true,
      message: sessionAudioSource === 'phone'
        ? 'Gravação salva (vídeo + áudio do celular)!'
        : 'Gravação parada com sucesso!',
      videoPath: config.recordVideoPath
    };
  }

  // Fecha só o OBS que o ClevenRec abriu em segundo plano
  if (shouldCloseObs) {
    await closeObsIfLaunchedByApp();
  }

  return result;
}

function mergeVideoAudio(videoPath, audioPath, outputPath) {
  return new Promise((resolve) => {
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
    if (pathCache) pathCache.recordDir = config.recordDir;
    return { dir: config.recordDir, preview: buildRecordPath(config.recordDir) };
  }
  return null;
});

function normalizeDevicePath(input) {
  const raw = String(input || '').trim().replace(/\\/g, '/');
  if (!raw) return '/sdcard/Download';
  let cleaned = raw.replace(/\/+/g, '/');
  if (!cleaned.startsWith('/')) cleaned = `/${cleaned}`;
  if (cleaned.length > 1 && cleaned.endsWith('/')) cleaned = cleaned.slice(0, -1);
  return cleaned;
}

function isAllowedDevicePath(devicePath) {
  const p = normalizeDevicePath(devicePath).toLowerCase();
  return (
    p === '/sdcard'
    || p.startsWith('/sdcard/')
    || p === '/storage/emulated/0'
    || p.startsWith('/storage/emulated/0/')
    || p.startsWith('/storage/')
  );
}

/** Volumes removíveis sob /storage (exclui emulated/self). */
async function listRemovableStorageRoots(adbPath, serial) {
  const result = await runAdb(
    adbPath,
    ['-s', serial, 'shell', 'ls', '-1', '/storage'],
    { timeout: 12000 },
  );
  const names = (result.stdout || '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .filter((name) => {
      const lower = name.toLowerCase();
      return lower !== 'emulated' && lower !== 'self' && !lower.startsWith('.');
    });

  const roots = [];
  for (const name of names) {
    const pathValue = normalizeDevicePath(`/storage/${name}`);
    // Confirma que a pasta lista (volume montado)
    const probe = await runAdb(
      adbPath,
      ['-s', serial, 'shell', 'ls', '-la', '--', pathValue],
      { timeout: 8000 },
    );
    if (!probe.ok && !(probe.stdout || '').trim()) continue;
    roots.push({
      id: `sd:${name}`,
      label: roots.length === 0 ? 'Cartão SD' : `Cartão SD (${name})`,
      path: pathValue,
      volume: name,
    });
  }
  return roots;
}

function parentDevicePath(devicePath) {
  const p = normalizeDevicePath(devicePath);
  if (p === '/' || p === '/sdcard' || p === '/storage/emulated/0') return p;
  const idx = p.lastIndexOf('/');
  if (idx <= 0) return '/';
  return p.slice(0, idx) || '/';
}

function parseLsLine(line) {
  const trimmed = String(line || '').trim();
  if (!trimmed || trimmed.startsWith('total ')) return null;
  // long listing: drwxrwx--- 2 ... name
  const parts = trimmed.split(/\s+/);
  if (parts.length < 8) {
    // plain name fallback
    const name = trimmed.replace(/\/$/, '');
    if (!name || name === '.' || name === '..') return null;
    return { name, isDir: trimmed.endsWith('/'), size: null, raw: trimmed };
  }
  const mode = parts[0];
  // Symlink (l): só trata como pasta se o listing marcar com /
  const isDir = mode.startsWith('d') || (mode.startsWith('l') && trimmed.endsWith('/'));
  const size = /^\d+$/.test(parts[4]) ? Number(parts[4]) : null;
  // date/time then name — name may contain spaces (index 7+)
  let name = parts.slice(7).join(' ');
  // some ls put year/time differently; if name empty, take last token
  if (!name) name = parts[parts.length - 1];
  name = name.replace(/\/$/, '');
  if (!name || name === '.' || name === '..') return null;
  return { name, isDir, size, raw: trimmed };
}

async function getPrimaryDeviceSerial() {
  ensureRuntimePaths();
  const adbPath = getAdbPath();
  const listed = await runAdb(adbPath, ['devices']);
  const devices = parseAdbDevices(listed.stdout || '');
  if (!devices.length) {
    const all = parseAdbDevices(listed.stdout || '', { readyOnly: false });
    const blocker = describeAdbBlockers(all, config.connection);
    return {
      success: false,
      message: blocker || 'Nenhum dispositivo ADB conectado.',
      devices: [],
    };
  }

  // Respeita o modo de conexão do painel (Wi‑Fi ≠ preferir USB às cegas)
  const chosen =
    pickDeviceForMode(devices, config.connection, config.wifiAddress)
    || (config.activeSerial && devices.find((d) => d.serial === config.activeSerial))
    || devices[0];

  return { success: true, serial: chosen.serial, devices };
}

ipcMain.handle('transfer-devices', async () => safeIpc(async () => {
  const info = await getPrimaryDeviceSerial();
  if (!info.success) return info;
  return {
    success: true,
    serial: info.serial,
    devices: info.devices,
    message: `${info.devices.length} dispositivo(s)`,
  };
}, 'Falha ao listar dispositivos'));

ipcMain.handle('transfer-roots', async () => safeIpc(async () => {
  const info = await getPrimaryDeviceSerial();
  if (!info.success) return info;
  ensureRuntimePaths();
  const adbPath = getAdbPath();
  const removable = await listRemovableStorageRoots(adbPath, info.serial);
  return {
    success: true,
    serial: info.serial,
    roots: [
      {
        id: 'download',
        label: 'Download',
        path: '/sdcard/Download',
        aliases: ['/storage/emulated/0/Download'],
      },
      ...removable,
    ],
  };
}, 'Falha ao listar raízes de armazenamento'));

ipcMain.handle('transfer-list', async (_event, payload = {}) => safeIpc(async () => {
  const dir = normalizeDevicePath(payload.path || '/sdcard/Download');
  if (!isAllowedDevicePath(dir)) {
    return { success: false, message: 'Pasta não permitida. Use /sdcard ou /storage.' };
  }
  const info = await getPrimaryDeviceSerial();
  if (!info.success) return info;

  ensureRuntimePaths();
  const adbPath = getAdbPath();
  const base = ['-s', info.serial, 'shell', 'ls', '-la', '--', dir];
  const result = await runAdb(adbPath, base, { timeout: 20000 });
  if (!result.ok && !(result.stdout || '').trim()) {
    return {
      success: false,
      message: (result.stderr || result.stdout || 'Não foi possível listar a pasta.').trim().slice(0, 240),
      path: dir,
      serial: info.serial,
    };
  }

  const entries = (result.stdout || '')
    .split(/\r?\n/)
    .map(parseLsLine)
    .filter(Boolean)
    .sort((a, b) => {
      if (a.isDir !== b.isDir) return a.isDir ? -1 : 1;
      return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
    });

  return {
    success: true,
    path: dir,
    parent: parentDevicePath(dir),
    serial: info.serial,
    entries,
  };
}, 'Falha ao listar pasta do celular'));

ipcMain.handle('transfer-choose-files', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile', 'multiSelections'],
    title: 'Escolher arquivos para enviar',
  });
  if (result.canceled || !result.filePaths?.length) return { success: false, canceled: true };
  return {
    success: true,
    files: result.filePaths,
    kind: 'files',
  };
});

ipcMain.handle('transfer-choose-folder', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory'],
    title: 'Escolher pasta para enviar',
  });
  if (result.canceled || !result.filePaths?.length) return { success: false, canceled: true };
  return {
    success: true,
    files: result.filePaths,
    kind: 'folder',
  };
});

ipcMain.handle('transfer-choose-save-dir', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory', 'createDirectory'],
    title: 'Salvar arquivos do celular em…',
  });
  if (result.canceled || !result.filePaths?.[0]) return { success: false, canceled: true };
  return { success: true, dir: result.filePaths[0] };
});

ipcMain.handle('transfer-push', async (_event, payload = {}) => safeIpc(async () => {
  const remoteDir = normalizeDevicePath(payload.remoteDir || '/sdcard/Download');
  if (!isAllowedDevicePath(remoteDir)) {
    return { success: false, message: 'Destino não permitido. Use /sdcard ou /storage.' };
  }
  const paths = Array.isArray(payload.files) ? payload.files.filter(Boolean) : [];
  if (!paths.length) return { success: false, message: 'Nenhum arquivo ou pasta selecionado.' };

  const items = [];
  for (const localPath of paths) {
    if (!fs.existsSync(localPath)) {
      return { success: false, message: `Caminho inválido: ${localPath}` };
    }
    const st = fs.statSync(localPath);
    if (!st.isFile() && !st.isDirectory()) {
      return { success: false, message: `Tipo não suportado: ${localPath}` };
    }
    items.push({
      path: localPath,
      name: path.basename(localPath),
      isDir: st.isDirectory(),
    });
  }

  const info = await getPrimaryDeviceSerial();
  if (!info.success) return info;
  ensureRuntimePaths();
  const adbPath = getAdbPath();

  const sent = [];
  for (const item of items) {
    // adb push copia pastas recursivamente para remoteDir/nome
    const result = await runAdb(
      adbPath,
      ['-s', info.serial, 'push', item.path, remoteDir + '/'],
      { timeout: 30 * 60 * 1000 }
    );
    if (!result.ok) {
      return {
        success: false,
        message: (result.stderr || result.stdout || `Falha ao enviar ${item.name}`).trim().slice(0, 300),
        sent,
        serial: info.serial,
      };
    }
    sent.push(item.isDir ? `${item.name}/` : item.name);
  }

  const folders = items.filter((i) => i.isDir).length;
  const files = items.length - folders;
  let message;
  if (items.length === 1) {
    message = items[0].isDir
      ? `Pasta enviada: ${items[0].name} → ${remoteDir}`
      : `Enviado: ${items[0].name} → ${remoteDir}`;
  } else if (folders && files) {
    message = `${files} arquivo(s) + ${folders} pasta(s) → ${remoteDir}`;
  } else if (folders) {
    message = `${folders} pasta(s) enviada(s) → ${remoteDir}`;
  } else {
    message = `${files} arquivos enviados → ${remoteDir}`;
  }

  return {
    success: true,
    message,
    sent,
    path: remoteDir,
    serial: info.serial,
  };
}, 'Falha ao enviar arquivo/pasta'));

ipcMain.handle('transfer-pull', async (_event, payload = {}) => safeIpc(async () => {
  const remotePath = normalizeDevicePath(payload.remotePath || '');
  const localDir = payload.localDir;
  if (!remotePath || !isAllowedDevicePath(remotePath)) {
    return { success: false, message: 'Selecione um arquivo/pasta em /sdcard ou /storage.' };
  }
  if (!localDir || !fs.existsSync(localDir)) {
    return { success: false, message: 'Pasta local inválida.' };
  }

  const info = await getPrimaryDeviceSerial();
  if (!info.success) return info;
  ensureRuntimePaths();
  const adbPath = getAdbPath();
  const result = await runAdb(
    adbPath,
    ['-s', info.serial, 'pull', remotePath, localDir],
    { timeout: 10 * 60 * 1000 }
  );
  if (!result.ok) {
    return {
      success: false,
      message: (result.stderr || result.stdout || 'Falha ao baixar.').trim().slice(0, 300),
      serial: info.serial,
    };
  }

  const name = path.posix.basename(remotePath);
  return {
    success: true,
    message: `Baixado: ${name} → ${localDir}`,
    localDir,
    remotePath,
    serial: info.serial,
  };
}, 'Falha ao baixar arquivo'));

const PREVIEW_TEXT_MAX = 512 * 1024;
const PREVIEW_LIMITS = {
  image: 15 * 1024 * 1024,
  audio: 40 * 1024 * 1024,
  video: 80 * 1024 * 1024,
  pdf: 40 * 1024 * 1024,
  text: 8 * 1024 * 1024, // pull cap; content truncated to PREVIEW_TEXT_MAX
};

const PREVIEW_KINDS = {
  image: {
    ext: new Set(['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg']),
    mime: (ext) => (
      ext === 'jpg' || ext === 'jpeg' ? 'image/jpeg'
        : ext === 'png' ? 'image/png'
          : ext === 'gif' ? 'image/gif'
            : ext === 'webp' ? 'image/webp'
              : ext === 'bmp' ? 'image/bmp'
                : ext === 'svg' ? 'image/svg+xml'
                  : 'application/octet-stream'
    ),
  },
  audio: {
    ext: new Set(['mp3', 'wav', 'flac', 'aac', 'ogg', 'm4a', 'opus']),
    mime: (ext) => (
      ext === 'mp3' ? 'audio/mpeg'
        : ext === 'wav' ? 'audio/wav'
          : ext === 'flac' ? 'audio/flac'
            : ext === 'aac' ? 'audio/aac'
              : ext === 'ogg' ? 'audio/ogg'
                : ext === 'm4a' ? 'audio/mp4'
                  : ext === 'opus' ? 'audio/ogg'
                    : 'audio/mpeg'
    ),
  },
  video: {
    ext: new Set(['mp4', 'webm', 'mkv', 'mov', '3gp', 'm4v']),
    mime: (ext) => (
      ext === 'webm' ? 'video/webm'
        : ext === 'mkv' ? 'video/x-matroska'
          : ext === 'mov' ? 'video/quicktime'
            : ext === '3gp' ? 'video/3gpp'
              : 'video/mp4'
    ),
  },
  pdf: {
    ext: new Set(['pdf']),
    mime: () => 'application/pdf',
  },
  text: {
    ext: new Set(['txt', 'md', 'json', 'xml', 'csv', 'log']),
    mime: (ext) => (
      ext === 'json' ? 'application/json'
        : ext === 'xml' ? 'application/xml'
          : ext === 'csv' ? 'text/csv'
            : ext === 'md' ? 'text/markdown'
              : 'text/plain'
    ),
  },
};

function resolvePreviewKind(ext) {
  for (const [kind, spec] of Object.entries(PREVIEW_KINDS)) {
    if (spec.ext.has(ext)) return kind;
  }
  return null;
}

function formatPreviewLimit(bytes) {
  const mb = Math.round(bytes / (1024 * 1024));
  return `${mb} MB`;
}

function getPreviewDir() {
  const dir = path.join(app.getPath('temp'), 'clevenrec-preview');
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  return dir;
}

function clearPreviewDir(exceptFile = null) {
  const dir = getPreviewDir();
  try {
    for (const name of fs.readdirSync(dir)) {
      const full = path.join(dir, name);
      if (exceptFile && path.resolve(full) === path.resolve(exceptFile)) continue;
      try { fs.unlinkSync(full); } catch (_) { /* ignore */ }
    }
  } catch (_) { /* ignore */ }
}

function toPreviewMediaUrl(localPath) {
  return `cleven-preview://preview/?f=${encodeURIComponent(path.basename(localPath))}`;
}

function readPreviewText(localPath) {
  const buf = fs.readFileSync(localPath);
  let text;
  try {
    text = buf.toString('utf8');
    if (text.includes('\uFFFD') && buf.includes(0)) {
      text = buf.toString('latin1');
    }
  } catch (_) {
    text = buf.toString('latin1');
  }
  const truncated = text.length > PREVIEW_TEXT_MAX;
  if (truncated) text = text.slice(0, PREVIEW_TEXT_MAX);
  return { text, truncated };
}

ipcMain.handle('transfer-preview', async (_event, payload = {}) => safeIpc(async () => {
  const remotePath = normalizeDevicePath(payload.remotePath || '');
  if (!remotePath || !isAllowedDevicePath(remotePath)) {
    return { success: false, message: 'Caminho remoto inválido.' };
  }
  const baseName = path.posix.basename(remotePath);
  const ext = (baseName.includes('.') ? baseName.split('.').pop() : '').toLowerCase();
  const kind = resolvePreviewKind(ext);
  if (!kind) {
    return { success: false, unsupported: true, message: 'Preview indisponível para este tipo.' };
  }

  const limit = PREVIEW_LIMITS[kind];
  const sizeHint = Number(payload.size);
  if (Number.isFinite(sizeHint) && sizeHint > limit) {
    return {
      success: false,
      tooLarge: true,
      kind,
      message: `Arquivo grande demais para preview (>${formatPreviewLimit(limit)}).`,
    };
  }

  const info = await getPrimaryDeviceSerial();
  if (!info.success) return info;
  ensureRuntimePaths();
  const adbPath = getAdbPath();
  const previewDir = getPreviewDir();
  clearPreviewDir();

  const safeName = baseName.replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').slice(0, 120) || `preview.${ext}`;
  const localPath = path.join(previewDir, `${Date.now()}-${safeName}`);

  const pullTimeout = kind === 'video' ? 3 * 60 * 1000 : 90 * 1000;
  const result = await runAdb(
    adbPath,
    ['-s', info.serial, 'pull', remotePath, localPath],
    { timeout: pullTimeout }
  );
  if (!result.ok || !fs.existsSync(localPath)) {
    return {
      success: false,
      kind,
      message: (result.stderr || result.stdout || 'Falha ao puxar preview.').trim().slice(0, 240),
      serial: info.serial,
    };
  }

  const stat = fs.statSync(localPath);
  if (stat.size > limit) {
    try { fs.unlinkSync(localPath); } catch (_) { /* ignore */ }
    return {
      success: false,
      tooLarge: true,
      kind,
      message: `Arquivo grande demais para preview (>${formatPreviewLimit(limit)}).`,
    };
  }

  const mime = PREVIEW_KINDS[kind].mime(ext);
  const mediaUrl = toPreviewMediaUrl(localPath);
  const payloadOut = {
    success: true,
    kind,
    mime,
    localPath,
    mediaUrl,
    url: pathToFileURL(localPath).href,
    name: baseName,
    size: stat.size,
    serial: info.serial,
  };

  if (kind === 'image') {
    payloadOut.dataUrl = `data:${mime};base64,${fs.readFileSync(localPath).toString('base64')}`;
  } else if (kind === 'text') {
    const { text, truncated } = readPreviewText(localPath);
    payloadOut.text = text;
    payloadOut.truncated = truncated;
  }

  return payloadOut;
}, 'Falha no preview'));

ipcMain.handle('get-app-version', () => app.getVersion());

ipcMain.handle('get-update-status', () => ({
  ...updateState,
  currentVersion: app.getVersion(),
}));

ipcMain.handle('check-for-updates', async () => checkForAppUpdates());

ipcMain.handle('install-update', () => {
  if (updateState.status !== 'downloaded') {
    return { success: false, message: 'Nenhuma atualização pronta para instalar.' };
  }
  autoUpdater.quitAndInstall(false, true);
  return { success: true };
});

ipcMain.handle('open-external', async (_event, url) => {
  if (!url || typeof url !== 'string' || !/^https?:\/\//i.test(url)) {
    return { success: false, message: 'URL inválida.' };
  }
  await shell.openExternal(url);
  return { success: true };
});

ipcMain.handle('open-path', async (_event, target) => {
  if (!target || typeof target !== 'string') {
    return { success: false, message: 'Caminho inválido.' };
  }
  const resolved = path.resolve(target);
  if (!fs.existsSync(resolved)) {
    return { success: false, message: 'Pasta ou arquivo não encontrado.' };
  }
  const err = await shell.openPath(resolved);
  if (err) return { success: false, message: err };
  return { success: true };
});
