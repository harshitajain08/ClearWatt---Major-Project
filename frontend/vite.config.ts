import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import { spawn, type ChildProcess } from 'node:child_process';
import { resolve } from 'node:path';

function startBackend(): Plugin {
  let backend: ChildProcess | undefined;

  return {
    name: 'clearwatt-auto-start-backend',
    configureServer() {
      const backendDir = resolve(__dirname, '../backend');
      backend = spawn('uv', ['run', '--with-requirements', 'requirements.txt', 'python', '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000'], {
        cwd: backendDir,
        stdio: 'inherit',
        env: process.env,
      });

      backend.once('error', (error) => {
        console.error('[clearwatt] Unable to start backend:', error.message);
      });

      const stopBackend = () => {
        if (backend && !backend.killed) backend.kill('SIGTERM');
      };
      process.once('exit', stopBackend);
      process.once('SIGINT', stopBackend);
      process.once('SIGTERM', stopBackend);
    },
  };
}

export default defineConfig({
  plugins: [startBackend(), react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        proxyTimeout: 30000,
      },
    },
  },
});
