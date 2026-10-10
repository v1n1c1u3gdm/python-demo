import { createHash, X509Certificate } from 'node:crypto'
import { connect } from 'node:tls'
import { mkdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { defineConfig } from '@playwright/test'

const repositoryRoot = resolve(import.meta.dirname, '..')
const artifactDirectory = resolve(repositoryRoot, 'infra/.local/playwright')
const baseURL = process.env.LOCAL_SSO_BASE_URL || 'https://app.localhost'
const caFile = resolve(repositoryRoot, process.env.LOCAL_SSO_CA_FILE || 'infra/.local/public/ca.crt')
const base = new URL(baseURL)
const port = Number(base.port || 443)

function localCertificatePin() {
  return new Promise((resolvePin, rejectPin) => {
    const socket = connect({
      host: '127.0.0.1',
      port,
      servername: 'app.localhost',
      ca: readFileSync(caFile),
      rejectUnauthorized: true
    })
    socket.setTimeout(5000, () => {
      socket.destroy()
      rejectPin(new Error('Strict TLS verification of the local gateway timed out.'))
    })
    socket.once('secureConnect', () => {
      if (!socket.authorized) {
        socket.destroy()
        rejectPin(new Error('Strict TLS verification of the local gateway failed.'))
        return
      }
      const certificate = new X509Certificate(socket.getPeerCertificate().raw)
      const spki = certificate.publicKey.export({ type: 'spki', format: 'der' })
      socket.end()
      resolvePin(createHash('sha256').update(spki).digest('base64'))
    })
    socket.once('error', () => rejectPin(new Error('Strict TLS verification of the local gateway failed.')))
  })
}

export const certificateSpkiPin = await localCertificatePin()
mkdirSync(artifactDirectory, { recursive: true, mode: 0o700 })

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: '**/*.spec.js',
  outputDir: resolve(artifactDirectory, 'artifacts'),
  reporter: 'list',
  timeout: 45_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  use: {
    baseURL,
    browserName: 'chromium',
    headless: true,
    launchOptions: {
      args: [`--ignore-certificate-errors-spki-list=${certificateSpkiPin}`]
    },
    acceptDownloads: false,
    trace: 'off',
    screenshot: 'off',
    video: 'off'
  }
})
