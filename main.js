const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const path = require('path');
const { spawn, exec } = require('child_process');
const { OBSWebSocket } = require('obs-websocket-js');

let mainWindow;
let scrcpyProcess = null;
let isRecording = false;

const DEFAULT_RECORD_DIR = 'C:\\Users\\Clevy\\Downloads\\screencopy';

const config = {
  scrcpyPath: process.env.SCRCPY_PATH || 'C:\\Users\\Clevy\\AppData\\Local\\Microsoft\\WinGet\\Packages\\Genymobile.scrcpy_Microsoft.Winget.Source_8wekyb3d8bbwe\\scrcpy-win64-v4.0\\scrcpy.exe',
  recordDir: DEFAULT_RECORD_DIR,
  recordVideoPath: path.join(DEFAULT_RECORD_DIR, 'tutorial.mp4'),
  videoBitRate: '8000K',
  maxFps: '30',
  obsHost: 'localhost',
  obsPort: 4455,
  obsPassword: '',
};

const obs = new OBSWebSocket();

function buildRecordPath(dir) {
  return path.join(dir, 'tutorial.mp4');
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 480,
    height: 620,
    resizable: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js')
    },
    autoHideMenuBar: true,
    title: 'Scrcpy + OBS Recorder',
    icon: path.join(__dirname, 'public/icon.png')
  });

  mainWindow.loadFile('index.html');
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    stopEverything();
    app.quit();
  }
});

ipcMain.handle('get-status', () => {
  return { isRecording, videoPath: config.recordVideoPath };
});

ipcMain.handle('start-recording', async () => {
  if (isRecording) return { success: false, message: 'Já está gravando!' };

  try {
    // 1. Dispositivo Android
    const adbPath = path.join(path.dirname(config.scrcpyPath), 'adb.exe');
    const adbOk = await new Promise((resolve) => {
      exec(`"${adbPath}" devices`, (err, stdout) => {
        if (err) return resolve(false);
        const devices = stdout.split('\n')
          .slice(1)
          .map((l) => l.trim())
          .filter((l) => l && l.endsWith('device'));
        resolve(devices.length > 0);
      });
    });

    if (!adbOk) {
      return {
        success: false,
        message: 'Nenhum dispositivo Android encontrado!\n\n1. Conecte o celular via USB\n2. Ative a Depuração USB\n3. Aceite o aviso "Permitir depuração USB?" no celular'
      };
    }

    // 2. OBS (áudio)
    try {
      await obs.connect(`ws://${config.obsHost}:${config.obsPort}`, config.obsPassword);
    } catch (e) {
      // já conectado ou falha — StartRecord valida de verdade
    }

    try {
      await obs.call('StartRecord');
    } catch (e) {
      return {
        success: false,
        message: `Erro no OBS: ${e.message}\n\nVerifique se o WebSocket está ativo em OBS → Tools → WebSocket Server Settings`
      };
    }

    // 3. Scrcpy (vídeo)
    // scrcpy -d --record "...\tutorial.mp4" --video-bit-rate 8000K --max-fps 30 --no-audio
    config.recordVideoPath = buildRecordPath(config.recordDir);
    const scrcpyArgs = [
      '-d',
      '--record', config.recordVideoPath,
      '--video-bit-rate', config.videoBitRate,
      '--max-fps', config.maxFps,
      '--no-audio',
    ];

    let scrcpyErrorOutput = '';
    scrcpyProcess = spawn(config.scrcpyPath, scrcpyArgs, {
      stdio: ['ignore', 'ignore', 'pipe'],
      detached: false
    });

    scrcpyProcess.stderr.on('data', (d) => {
      scrcpyErrorOutput += d.toString();
    });

    scrcpyProcess.on('error', (err) => {
      mainWindow.webContents.send('recording-error', `Scrcpy não encontrado: ${err.message}`);
    });

    scrcpyProcess.on('close', (code) => {
      if (isRecording) {
        if (code !== 0) {
          const errorLines = scrcpyErrorOutput
            .split('\n')
            .filter((l) => l.includes('ERROR'))
            .join('\n');
          mainWindow.webContents.send(
            'recording-error',
            `Scrcpy falhou (código ${code}):\n${errorLines || 'erro desconhecido'}`
          );
        }
        stopEverything();
      }
    });

    isRecording = true;
    mainWindow.webContents.send('recording-started', { videoPath: config.recordVideoPath });

    return { success: true, message: 'Gravação iniciada!', videoPath: config.recordVideoPath };
  } catch (error) {
    return { success: false, message: error.message };
  }
});

ipcMain.handle('stop-recording', async () => {
  return await stopEverything();
});

async function stopEverything() {
  if (!isRecording && !scrcpyProcess) {
    return { success: true, message: 'Nada estava gravando.' };
  }

  try {
    await obs.call('StopRecord');
  } catch (e) {
    // pode já estar parado
  }

  if (scrcpyProcess) {
    try {
      if (process.platform === 'win32') {
        exec(`taskkill /pid ${scrcpyProcess.pid} /T /F`);
      } else {
        scrcpyProcess.kill('SIGTERM');
      }
    } catch (e) {
      // ignore
    }
    scrcpyProcess = null;
  }

  isRecording = false;
  mainWindow?.webContents.send('recording-stopped');

  return {
    success: true,
    message: 'Gravação parada com sucesso!',
    videoPath: config.recordVideoPath
  };
}

ipcMain.handle('choose-folder', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory']
  });
  if (!result.canceled) {
    config.recordDir = result.filePaths[0];
    config.recordVideoPath = buildRecordPath(config.recordDir);
    return config.recordVideoPath;
  }
  return null;
});
