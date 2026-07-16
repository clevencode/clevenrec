const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  startRecording: () => ipcRenderer.invoke('start-recording'),
  stopRecording: () => ipcRenderer.invoke('stop-recording'),
  getStatus: () => ipcRenderer.invoke('get-status'),
  chooseFolder: () => ipcRenderer.invoke('choose-folder'),
  
  onRecordingStarted: (callback) => ipcRenderer.on('recording-started', (event, data) => callback(data)),
  onRecordingStopped: (callback) => ipcRenderer.on('recording-stopped', () => callback()),
  onRecordingError: (callback) => ipcRenderer.on('recording-error', (event, msg) => callback(msg)),
});
