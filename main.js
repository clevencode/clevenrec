const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const path = require('path');
const { spawn, exec } = require('child_process');
const { OBSWebSocket } = require('obs-websocket-js');

let mainWindow;
let scrcpyProcess = null;
let isRecording = false;
let stoppingIntentionally = false;
let obsStartTime = null;
let scrcpyStartTime = null;

const DEFAULT_RECORD_DIR = 'C:\\Users\\Clevy\\Downloads\\screencopy';

const config = {
  scrcpyPath: process.env.SCRCPY_PATH || 'C:\\Users\\Clevy\\AppData\\Local\\Microsoft\\WinGet\\Packages\\Genymobile.scrcpy_Microsoft.Winget.Source_8wekyb3d8bbwe\\scrcpy-win64-v4.0\\scrcpy.exe',
  recordDir: DEFAULT_RECORD_DIR,
  recordVideoPath: path.join(DEFAULT_RECORD_DIR, 'screenvid.mkv'),
  obsHost: 'localhost',
  obsPort: 4455,
  obsPassword: '',
  ffmpegPath: process.env.FFMPEG_PATH || 'C:\\Users\\Clevy\\AppData\\Local\\Microsoft\\WinGet\\Links\\ffmpeg.exe',
};

const obs = new OBSWebSocket();

function buildRecordPath(dir) {
  const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  return path.join(dir, `screenvid-${stamp}.mkv`);
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
    stoppingIntentionally = false;

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
      obsStartTime = Date.now();
    } catch (e) {
      return {
        success: false,
        message: `Erro no OBS: ${e.message}\n\nVerifique se o WebSocket está ativo em OBS → Tools → WebSocket Server Settings`
      };
    }

    // 3. Scrcpy (vídeo)
    config.recordVideoPath = buildRecordPath(config.recordDir);
    const scrcpyArgs = [
      '-d',
      '--record', config.recordVideoPath,
      '--video-bit-rate', '6000K',
      '--max-fps', '30',
      '--no-audio',
    ];

    let scrcpyErrorOutput = '';
    scrcpyStartTime = Date.now(); // fallback se a mensagem não aparecer
    scrcpyProcess = spawn(config.scrcpyPath, scrcpyArgs, {
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: false
    });

    const onScrcpyLog = (d) => {
      const text = d.toString();
      scrcpyErrorOutput += text;
      if (text.includes('Recording started')) {
        scrcpyStartTime = Date.now();
      }
    };

    scrcpyProcess.stdout.on('data', onScrcpyLog);
    scrcpyProcess.stderr.on('data', onScrcpyLog);

    scrcpyProcess.on('error', (err) => {
      mainWindow.webContents.send('recording-error', `Scrcpy não encontrado: ${err.message}`);
    });

    scrcpyProcess.on('close', (code) => {
      // Só reporta erro se o scrcpy morreu sozinho (não foi o usuário clicando em Parar)
      if (isRecording && !stoppingIntentionally && code !== 0) {
        const errorLines = scrcpyErrorOutput
          .split('\n')
          .filter((l) => /ERROR/i.test(l))
          .join('\n');
        mainWindow.webContents.send(
          'recording-error',
          `Scrcpy falhou (código ${code}):\n${errorLines || scrcpyErrorOutput.trim().slice(-300) || 'erro desconhecido'}`
        );
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
    return { success: true, message: 'Nada estava gravando.' };
  }

  stoppingIntentionally = true;

  // Parar OBS e capturar o caminho do arquivo de áudio gravado
  let obsAudioPath = null;
  try {
    const stopResp = await obs.call('StopRecord');
    obsAudioPath = stopResp?.outputPath || null;
  } catch (e) {
    // pode já estar parado
  }

  // Encerrar scrcpy de forma suave (finaliza o mkv) e só forçar se necessário
  if (scrcpyProcess) {
    const proc = scrcpyProcess;
    scrcpyProcess = null;

    try {
      if (process.platform === 'win32') {
        // sem /F: pede para fechar e o scrcpy finaliza o arquivo
        exec(`taskkill /pid ${proc.pid} /T`);
      } else {
        proc.kill('SIGINT');
      }
    } catch (e) {
      // ignore
    }

    await waitForClose(proc, 5000);

    // se ainda estiver vivo, força
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

  // Pequena pausa para o disco gravar o final do mkv
  await new Promise((r) => setTimeout(r, 500));

  // Mesclar vídeo (scrcpy) + áudio (OBS) em um arquivo sincronizado
  if (obsAudioPath) {
    const syncedPath = config.recordVideoPath.replace(/\.mkv$/, '-sync.mkv');
    const merge = await mergeVideoAudio(config.recordVideoPath, obsAudioPath, syncedPath);
    if (merge.success) {
      return {
        success: true,
        message: 'Gravação parada e arquivo sincronizado gerado!',
        videoPath: syncedPath
      };
    }
    return {
      success: true,
      message: `Gravação parada, mas a mesclagem falhou: ${merge.message}\nVídeo e áudio foram salvos separadamente.`,
      videoPath: config.recordVideoPath
    };
  }

  return {
    success: true,
    message: 'Gravação parada com sucesso!',
    videoPath: config.recordVideoPath
  };
}

// Junta o vídeo do scrcpy com o áudio do OBS, compensando a diferença
// de tempo entre o início das duas gravações
function mergeVideoAudio(videoPath, audioPath, outputPath) {
  return new Promise((resolve) => {
    const fs = require('fs');
    if (!fs.existsSync(videoPath)) {
      return resolve({ success: false, message: `Vídeo não encontrado: ${videoPath}` });
    }
    if (!fs.existsSync(audioPath)) {
      return resolve({ success: false, message: `Áudio do OBS não encontrado: ${audioPath}` });
    }

    // o scrcpy começa a gravar depois do OBS: corta o início do áudio
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
    config.recordVideoPath = buildRecordPath(config.recordDir);
    return config.recordVideoPath;
  }
  return null;
});
