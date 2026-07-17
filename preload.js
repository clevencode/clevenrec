const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  startRecording: (options) => ipcRenderer.invoke('start-recording', options),
  stopRecording: () => ipcRenderer.invoke('stop-recording'),
  getStatus: () => ipcRenderer.invoke('get-status'),
  getRemoteInfo: () => ipcRenderer.invoke('get-remote-info'),
  saveSettings: (settings) => ipcRenderer.invoke('save-settings', settings),
  activateConnection: (payload) => ipcRenderer.invoke('activate-connection', payload),
  chooseFolder: () => ipcRenderer.invoke('choose-folder'),

  onRecordingStarted: (callback) => ipcRenderer.on('recording-started', (event, data) => callback(data)),
  onRecordingStopped: (callback) => ipcRenderer.on('recording-stopped', () => callback()),
  onRecordingError: (callback) => ipcRenderer.on('recording-error', (event, msg) => callback(msg)),
});
