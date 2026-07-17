const { app, BrowserWindow, ipcMain, dialog, screen } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn, exec, execFile, execFileSync } = require('child_process');
const { OBSWebSocket } = require('obs-websocket-js');
const { startRemoteServer } = require('./remote-server');

const PREVIEW_WINDOW_TITLE = 'ClevenRec Preview';
const WIN_WINDOW_SCRIPT = path.join(__dirname, 'win-window.ps1');

let mainWindow;
let splashWindow = null;
let scrcpyProcess = null;
let isRecording = false;
let stoppingIntentionally = false;
let obsStartTime = null;
let scrcpyStartTime = null;
let sessionMode = 'record'; // 'capture' | 'record'
let sessionAudioSource = 'pc'; // 'none' | 'pc' | 'phone'
let sessionUseObs = true;
let sessionObsLaunchedByApp = false;
let sessionObsPid = null;
let remoteInfo = { urls: [], primaryUrl: null, port: 8787 };
/** Bounds do painel de preview relativos à área de conteúdo da janela (DIP). */
let previewBoundsRel = { x: 400, y: 22, width: 320, height: 640 };
let scrcpyDocked = false;
let scrcpyDockTimer = null;
let scrcpyDockAttempts = 0;

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

function findInWingetPackages(exeName, packageHint) {
  const localAppData = process.env.LOCALAPPDATA || '';
  const wingetRoot = path.join(localAppData, 'Microsoft', 'WinGet', 'Packages');
  if (!fs.existsSync(wingetRoot)) return null;

  try {
    const hint = new RegExp(packageHint, 'i');
    const matches = fs.readdirSync(wingetRoot)
      .filter((name) => hint.test(name))
      .flatMap((pkg) => {
        const pkgDir = path.join(wingetRoot, pkg);
        const found = [];
        const walk = (dir, depth = 0) => {
          if (depth > 3) return;
          let entries = [];
          try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch (_) { return; }
          for (const entry of entries) {
            const full = path.join(dir, entry.name);
            if (entry.isFile() && entry.name.toLowerCase() === exeName.toLowerCase()) {
              found.push(full);
            } else if (entry.isDirectory()) {
              walk(full, depth + 1);
            }
          }
        };
        walk(pkgDir);
        return found;
      });
    return firstExisting(matches);
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
  mode: 'record',
  connection: 'usb', // 'usb' | 'wifi'
  wifiAddress: '',
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
    width: 980,
    height: 800,
    minWidth: 860,
    minHeight: 680,
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

  mainWindow.on('resize', () => {
    if (isRecording) syncScrcpyPreviewWindow();
  });
  mainWindow.on('move', () => {
    // Com SetParent o filho acompanha; sem embed, reposiciona na tela
    if (isRecording && !scrcpyDocked) syncScrcpyPreviewWindow();
  });

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

function hwndFromNativeHandle(buf) {
  if (!buf || !Buffer.isBuffer(buf)) return 0n;
  if (buf.length >= 8) return buf.readBigUInt64LE(0);
  if (buf.length >= 4) return BigInt(buf.readUInt32LE(0));
  return 0n;
}

function getPreviewLayout() {
  if (!mainWindow || mainWindow.isDestroyed()) return null;
  const winBounds = mainWindow.getBounds();
  const content = mainWindow.getContentBounds();
  const rel = previewBoundsRel || { x: 0, y: 0, width: 320, height: 640 };
  const width = Math.max(120, Math.round(rel.width));
  const height = Math.max(180, Math.round(rel.height));
  const padX = Math.max(0, content.x - winBounds.x);
  const padY = Math.max(0, content.y - winBounds.y);
  const client = {
    x: Math.round(padX + rel.x),
    y: Math.round(padY + rel.y),
    width,
    height,
  };
  const dipScreen = {
    x: Math.round(content.x + rel.x),
    y: Math.round(content.y + rel.y),
    width,
    height,
  };
  const physical = screen.dipToScreenRect(mainWindow, dipScreen);
  return { client, dipScreen, physical };
}

function runWinWindowAction(action, extraArgs = []) {
  return new Promise((resolve) => {
    if (process.platform !== 'win32' || !fs.existsSync(WIN_WINDOW_SCRIPT)) {
      resolve({ ok: false, out: 'unsupported' });
      return;
    }
    const args = [
      '-NoProfile',
      '-ExecutionPolicy', 'Bypass',
      '-File', WIN_WINDOW_SCRIPT,
      '-Action', action,
      '-Title', PREVIEW_WINDOW_TITLE,
      ...extraArgs,
    ];
    execFile('powershell.exe', args, { windowsHide: true, timeout: 8000 }, (err, stdout) => {
      const out = String(stdout || '').trim();
      resolve({ ok: !err && (out === 'ok' || out === '1'), out });
    });
  });
}

function clearScrcpyDockTimer() {
  if (scrcpyDockTimer) {
    clearTimeout(scrcpyDockTimer);
    scrcpyDockTimer = null;
  }
}

function resetScrcpyDockState() {
  clearScrcpyDockTimer();
  scrcpyDocked = false;
  scrcpyDockAttempts = 0;
}

async function syncScrcpyPreviewWindow() {
  if (!isRecording || !scrcpyProcess) return;
  const layout = getPreviewLayout();
  if (!layout) return;

  if (scrcpyDocked) {
    await runWinWindowAction('move', [
      '-X', String(layout.client.x),
      '-Y', String(layout.client.y),
      '-W', String(layout.client.width),
      '-H', String(layout.client.height),
    ]);
    return;
  }

  // Ainda não embutido: tenta posicionar pela tela (fallback / pré-embed)
  await runWinWindowAction('move', [
    '-X', String(layout.physical.x),
    '-Y', String(layout.physical.y),
    '-W', String(layout.physical.width),
    '-H', String(layout.physical.height),
  ]);
}

async function tryEmbedScrcpyWindow() {
  if (!mainWindow || mainWindow.isDestroyed() || !isRecording) return false;
  const layout = getPreviewLayout();
  if (!layout) return false;

  const parent = hwndFromNativeHandle(mainWindow.getNativeWindowHandle());
  if (!parent) return false;

  const result = await runWinWindowAction('embed', [
    '-Parent', parent.toString(),
    '-X', String(layout.client.x),
    '-Y', String(layout.client.y),
    '-W', String(layout.client.width),
    '-H', String(layout.client.height),
  ]);

  if (result.ok) {
    scrcpyDocked = true;
    mainWindow?.webContents.send('preview-state', { live: true, docked: true });
    return true;
  }
  return false;
}

function scheduleScrcpyDock() {
  resetScrcpyDockState();
  mainWindow?.webContents.send('preview-state', { live: true, docked: false });

  const tick = async () => {
    if (!isRecording || !scrcpyProcess) return;
    scrcpyDockAttempts += 1;

    const exists = await runWinWindowAction('exists');
    if (exists.ok) {
      const embedded = await tryEmbedScrcpyWindow();
      if (embedded) return;
      await syncScrcpyPreviewWindow();
    }

    if (scrcpyDockAttempts < 40) {
      scrcpyDockTimer = setTimeout(tick, 250);
    } else {
      // Fica no modo overlay na tela
      await syncScrcpyPreviewWindow();
      mainWindow?.webContents.send('preview-state', { live: true, docked: false });
    }
  };

  scrcpyDockTimer = setTimeout(tick, 400);
}

function buildScrcpyWindowArgs() {
  const layout = getPreviewLayout();
  const args = [
    '--window-title', PREVIEW_WINDOW_TITLE,
    '--window-borderless',
  ];
  if (layout) {
    args.push(
      '--window-x', String(layout.physical.x),
      '--window-y', String(layout.physical.y),
      '--window-width', String(layout.physical.width),
      '--window-height', String(layout.physical.height),
    );
  }
  return args;
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

function applySettings(settings = {}) {
  const audioSource = normalizeAudioSource(settings, config.audioSource);
  Object.assign(config, {
    mode: settings.mode ?? config.mode,
    connection: settings.connection ?? config.connection,
    wifiAddress: settings.wifiAddress != null ? String(settings.wifiAddress).trim() : config.wifiAddress,
    bitrate: settings.bitrate ?? config.bitrate,
    maxFps: String(settings.maxFps ?? config.maxFps),
    format: settings.format ?? config.format,
    maxSize: String(settings.maxSize ?? config.maxSize),
    audioSource,
    useObsAudio: isPcAudioSource(audioSource),
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

function ensureRuntimePaths() {
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
  return {
    scrcpyPath: config.scrcpyPath,
    ffmpegPath: config.ffmpegPath,
    adbPath: config.adbPath,
    obsPath: config.obsPath,
    recordDir: config.recordDir,
  };
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

  initRuntimePaths();
  createSplash();
  createWindow();
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

    // 3. Scrcpy
    const scrcpyArgs = [
      useWifi ? '-e' : '-d',
    ];

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
    scrcpyArgs.push(...buildScrcpyWindowArgs());

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
    scheduleScrcpyDock();
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
  resetScrcpyDockState();
  mainWindow?.webContents.send('recording-stopped');
  mainWindow?.webContents.send('preview-state', { live: false, docked: false });

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

ipcMain.handle('set-preview-bounds', (_event, bounds = {}) => {
  const x = Number(bounds.x);
  const y = Number(bounds.y);
  const width = Number(bounds.width);
  const height = Number(bounds.height);
  if (![x, y, width, height].every((n) => Number.isFinite(n))) {
    return { success: false };
  }
  previewBoundsRel = {
    x: Math.max(0, Math.round(x)),
    y: Math.max(0, Math.round(y)),
    width: Math.max(80, Math.round(width)),
    height: Math.max(120, Math.round(height)),
  };
  if (isRecording) syncScrcpyPreviewWindow();
  return { success: true, bounds: previewBoundsRel };
});
