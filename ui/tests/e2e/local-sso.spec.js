import { execFileSync } from 'node:child_process'
import { createServer } from 'node:https'
import { request as httpsRequest } from 'node:https'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { randomBytes } from 'node:crypto'
import { test } from './fixtures.js'

const root = resolve(import.meta.dirname, '../../..')
const artifactRoot = resolve(root, 'infra/.local/playwright')
const baseURL = process.env.LOCAL_SSO_BASE_URL || 'https://app.localhost'
function requireBehavior(condition, message) {
  if (!condition) throw new Error(message)
}

function runKeycloakAdmin(script, extraEnv = {}) {
  const project = process.env.COMPOSE_PROJECT_NAME || 'python-demo-local'
  try {
    return execFileSync('docker', [
      'compose', '-p', project, '-f', 'compose.yaml', 'exec', '-T',
      ...Object.entries(extraEnv).flatMap(([name, value]) => ['-e', `${name}=${value}`]),
      'app-keycloak', 'sh', '-c', script,
    ], { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], timeout: 30_000 }).trim()
  } catch {
    throw new Error('A bounded native Keycloak test-fixture operation failed.')
  }
}

function createTemporaryNonAdmin() {
  const suffix = randomBytes(8).toString('hex')
  const username = `task4nonadmin${suffix}`
  const password = randomBytes(32).toString('base64url')
  const email = `${username}@app.localhost`
  const config = `/tmp/task4-${suffix}.kcadm`
  const authenticate = `password=$(cat /run/local-secrets/keycloak_admin_password); ` +
    `/opt/keycloak/bin/kcadm.sh config credentials --config='${config}' ` +
    '--server http://localhost:8080/auth --realm master --user admin --password "$password" >/dev/null; '
  const subject = runKeycloakAdmin(
    `${authenticate}/opt/keycloak/bin/kcadm.sh create users -r python-demo --config='${config}' ` +
      `-s username=${username} -s enabled=true -s email=${email} -s emailVerified=true ` +
      '-s firstName=Temporary -s lastName=Authorization-Probe -i',
  )
  requireBehavior(/^[0-9a-f-]{36}$/i.test(subject), 'Keycloak did not create an exact temporary non-admin subject.')
  const fixture = { username, password, subject, config }
  try {
    runKeycloakAdmin(
      `/opt/keycloak/bin/kcadm.sh set-password --config='${config}' -r python-demo ` +
        `--username=${username} --new-password="$TASK4_TEMP_PASSWORD"`,
      { TASK4_TEMP_PASSWORD: password },
    )
  } catch (error) {
    removeTemporaryNonAdmin(fixture)
    throw error
  }
  return fixture
}

function removeTemporaryNonAdmin(fixture) {
  if (!fixture) return
  runKeycloakAdmin(
    `/opt/keycloak/bin/kcadm.sh delete users/${fixture.subject} -r python-demo ` +
      `--config='${fixture.config}' && rm -f '${fixture.config}'`,
  )
}

async function preflightLocalStack() {
  const stateDirectory = resolve(root, process.env.LOCAL_STATE_DIR || 'infra/.local')
  const markerReader = [
    "from pathlib import Path; import json; s=Path('/state'); g=(s/'bootstrap-generation').read_text().strip();",
    "r={p.name:p.read_text().strip()==g for p in (s/'ready').glob('*')};",
    "print(json.dumps({'generation':bool(g),'ready':r},sort_keys=True))",
  ].join(' ')
  let markers
  try {
    const summary = execFileSync('docker', [
      'run', '--rm', '--network', 'none', '--read-only', '--mount',
      `type=bind,src=${stateDirectory},dst=/state,readonly`, 'python-demo-local-bootstrap:local',
      'python', '-c', markerReader,
    ], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], timeout: 10_000 })
    markers = JSON.parse(summary)
  } catch {
    throw new Error('Local Compose readiness markers could not be verified safely.')
  }
  requireBehavior(
    markers.generation && ['bookstack', 'gitea', 'ci'].every(route => markers.ready[route] === true),
    'The local native integrations are not ready for the current bootstrap generation.',
  )

  const base = new URL(baseURL)
  const ca = readFileSync(resolve(root, process.env.LOCAL_SSO_CA_FILE || `${stateDirectory}/public/ca.crt`))
  async function status(path) {
    return new Promise((resolveStatus, rejectStatus) => {
      const request = httpsRequest({
        host: '127.0.0.1',
        port: Number(base.port || 443),
        path,
        method: 'GET',
        servername: 'app.localhost',
        ca,
        rejectUnauthorized: true,
        headers: { Host: 'app.localhost' },
        timeout: 5000,
      }, response => {
        response.resume()
        resolveStatus(response.statusCode)
      })
      request.on('timeout', () => request.destroy(new Error('timeout')))
      request.on('error', () => rejectStatus(new Error('Local route preflight failed.')))
      request.end()
    })
  }
  for (const path of ['/api/ready', '/bookstack/login', '/git/user/oauth2/keycloak', '/ci/authorize']) {
    const responseStatus = await status(path)
    requireBehavior(
      path === '/api/ready' ? responseStatus === 200 : responseStatus >= 200 && responseStatus < 400,
      'A local native route is unavailable before the browser SSO test.',
    )
  }
}

async function navigate(page, path) {
  try {
    await page.goto(new URL(path, baseURL).toString())
  } catch (error) {
    throw new Error(`Navigation failed for ${path} (${error?.name || 'browser error'}).`, {
      cause: error,
    })
  }
}

async function confirmConsentIfShown(page) {
  const consent = page.getByRole('button', { name: /allow|accept|authorize|yes/i })
  if (await consent.count()) await consent.first().click()
}

async function enterKeycloakCredentials(page, username, password) {
  await page.locator('#username').fill(username)
  await page.locator('#password').fill(password)
  await page.locator('form').getByRole('button', { name: /sign in|log in|entrar/i }).click()
  await confirmConsentIfShown(page)
}

async function waitForSsoSession(page) {
  await page.locator('.admin-login__session-title').waitFor({ state: 'visible' })
  const username = await page.locator('.admin-login__session-title strong').textContent()
  requireBehavior(username?.trim() === 'admin', 'The administrative UI did not resolve the signed-in Keycloak identity.')
}

test.beforeEach(async ({ page }) => {
  page.setDefaultTimeout(20_000)
  page.setDefaultNavigationTimeout(20_000)
})

test.beforeAll(async () => {
  await preflightLocalStack()
})

test('one Keycloak password login reaches each native admin session and refreshes', async ({ page }, testInfo) => {
  testInfo.setTimeout(20 * 60 * 1000)
  let tokenResponses = 0
  let refreshRequests = 0
  let loginSubmissions = 0
  let accessTokenLifetimeSeconds = 0
  page.on('request', request => {
    const pathname = new URL(request.url()).pathname
    if (pathname.endsWith('/protocol/openid-connect/token')) tokenResponses += 1
    if (pathname.endsWith('/protocol/openid-connect/token') && request.method() === 'POST') {
      const body = request.postData() || ''
      if (new URLSearchParams(body).get('grant_type') === 'refresh_token') refreshRequests += 1
    }
    if (pathname.endsWith('/login-actions/authenticate')) loginSubmissions += 1
  })
  page.on('response', async response => {
    if (!new URL(response.url()).pathname.endsWith('/protocol/openid-connect/token')) return
    try {
      const body = await response.json()
      if (Number.isFinite(body.expires_in)) accessTokenLifetimeSeconds = body.expires_in
    } catch {
      // The test records only the lifetime; token response data is never attached or printed.
    }
  })

  try {
    await navigate(page, '/admin')
    await page.getByRole('button', { name: 'Entrar com Keycloak' }).click()
    await enterKeycloakCredentials(page, 'admin', 'admin!123')
    await waitForSsoSession(page)
    console.log('milestone: ui-admin-session')
    requireBehavior(loginSubmissions === 1, 'The admin flow prompted for a Keycloak password more than once.')

    await navigate(page, '/bookstack/login')
    await page.getByRole('button', { name: /login with keycloak/i }).click()
    await confirmConsentIfShown(page)
    await navigate(page, '/bookstack/settings/users')
    requireBehavior(
      new URL(page.url()).pathname === '/bookstack/settings/users' &&
        (await page.locator('body').innerText()).toLowerCase().includes('users'),
      'BookStack did not grant the signed-in local administrator access to user settings.',
    )
    console.log('milestone: bookstack-admin-settings')

    let giteaAdminPageStatus = false
    page.on('response', response => {
      const url = new URL(response.url())
      if (url.pathname === '/git/admin' && response.status() === 200) giteaAdminPageStatus = true
    })
    await navigate(page, '/git/user/oauth2/keycloak')
    await confirmConsentIfShown(page)
    await navigate(page, '/git/admin')
    const giteaPageState = await page.evaluate(() => {
      const body = document.body.innerText.toLowerCase()
      return {
        path: location.pathname,
        title: document.title,
        loginPrompt: body.includes('sign in') || body.includes('log in'),
      }
    })
    requireBehavior(
      giteaPageState.path === '/git/admin' &&
        giteaPageState.title.includes('Local Administrator') &&
        giteaAdminPageStatus && !giteaPageState.loginPrompt,
      'Gitea did not grant the signed-in local administrator access to its native admin panel.',
    )
    console.log('milestone: gitea-admin-panel')

    await navigate(page, '/ci/authorize')
    await confirmConsentIfShown(page)
    await navigate(page, '/ci/')
    const woodpeckerIdentity = await page.evaluate(async () => {
      const csrf = window.WOODPECKER_CSRF
      const user = window.WOODPECKER_USER
      if (!csrf || !user) return { status: null, adminPage: false, username: null }
      const response = await fetch('/ci/api/user', {
        credentials: 'same-origin',
        headers: csrf ? { 'X-CSRF-Token': csrf } : {}
      })
      const identity = response.ok ? await response.json() : null
      return {
        ready: response.ok,
        status: response.status,
        adminPage: user?.admin === true,
        username: identity?.login || identity?.username || identity?.name || null,
        csrfPresent: Boolean(csrf),
        userPresent: Boolean(user),
      }
    })
    console.log(`milestone: woodpecker-api status=${woodpeckerIdentity.status ?? 'absent'} admin=${woodpeckerIdentity.adminPage === true} username=${woodpeckerIdentity.username || 'absent'} csrf=${woodpeckerIdentity.csrfPresent === true} user=${woodpeckerIdentity.userPresent === true}`)
    requireBehavior(
      woodpeckerIdentity.ready && woodpeckerIdentity.status === 200 &&
        woodpeckerIdentity.adminPage && woodpeckerIdentity.username === 'admin',
      'Woodpecker did not report an authenticated administrator using its native CSRF-protected session API.',
    )
    console.log('milestone: woodpecker-admin-api')
    requireBehavior(loginSubmissions === 1, 'A native service requested another Keycloak password login.')
    requireBehavior(accessTokenLifetimeSeconds > 0, 'The Keycloak browser login did not expose an expiry lifetime.')

    await navigate(page, '/admin')
    console.log(`milestone: refresh-wait-start lifetime-seconds=${accessTokenLifetimeSeconds}`)
    const refreshRequestsBeforeExpiry = refreshRequests
    await page.waitForTimeout((accessTokenLifetimeSeconds + 5) * 1000)
    await page.locator('.admin-login__session-title').waitFor({ state: 'visible' })
    requireBehavior(
      refreshRequests > refreshRequestsBeforeExpiry,
      'The browser session did not send a refresh-token grant after its access token expired.',
    )
    requireBehavior(tokenResponses > 1, 'The browser did not renew its Keycloak token response.')
    console.log('milestone: refresh-after-expiry')
    requireBehavior(loginSubmissions === 1, 'Refreshing the session requested the Keycloak password again.')

    await page.goto(new URL('/admin', baseURL).toString())
    await page.getByRole('button', { name: 'Sair' }).click()
    await page.getByRole('button', { name: 'Entrar com Keycloak' }).waitFor({ state: 'visible' })
    requireBehavior(
      !(await page.locator('.admin-profile').count()),
      'The administrative profile remained visible after local SSO logout.',
    )
    console.log('milestone: local-logout')
  } finally {
    await page.close()
  }
})

test('an invalid callback state and rejected token never authenticate the UI', async ({ page }) => {
  try {
    await navigate(page, '/admin?code=synthetic-invalid-code&state=synthetic-invalid-state')
    await page.waitForTimeout(1500)
    requireBehavior(
      !(await page.locator('.admin-login__session-title').count()) && !(await page.locator('.admin-profile').count()),
      'An invalid Keycloak callback created an administrative session.',
    )
    const rejectedTokenStatus = await page.evaluate(async () => {
      const response = await fetch('/api/admin/profile', {
        headers: { Authorization: 'Bearer synthetic-invalid-token' }
      })
      return response.status
    })
    requireBehavior(rejectedTokenStatus === 401, 'The API accepted a synthetic invalid bearer token.')
  } finally {
    await page.close()
  }
})

test('a temporary Keycloak identity without the admin role cannot enter the administrative UI', async ({ page }) => {
  const fixture = createTemporaryNonAdmin()
  try {
    await navigate(page, '/admin')
    await page.getByRole('button', { name: 'Entrar com Keycloak' }).click()
    await enterKeycloakCredentials(page, fixture.username, fixture.password)
    await page.getByText('A conta não possui o papel admin.', { exact: true }).waitFor({ state: 'visible' })
    requireBehavior(
      !(await page.locator('.admin-login__session-title').count()) && !(await page.locator('.admin-profile').count()),
      'A non-admin Keycloak identity created an administrative UI session.',
    )
    console.log('milestone: non-admin-ui-denied')
  } finally {
    await page.close()
    removeTemporaryNonAdmin(fixture)
  }
})

test('the isolated local SPKI exception rejects a differently signed HTTPS certificate', async ({ context }) => {
  const directory = mkdtempSync(join(artifactRoot, 'untrusted-'))
  const key = join(directory, 'key.pem')
  const certificate = join(directory, 'certificate.pem')
  let server
  const page = await context.newPage()

  try {
    execFileSync('openssl', [
      'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', key, '-out', certificate,
      '-subj', '/CN=localhost', '-days', '1', '-addext', 'subjectAltName=DNS:localhost',
    ], { stdio: 'ignore' })
    server = createServer({ key: readFileSync(key), cert: readFileSync(certificate) }, (_request, response) => {
      response.end('unexpectedly trusted')
    })
    server.listen(0, '127.0.0.1')
    await new Promise(resolveServer => server.once('listening', resolveServer))
    const address = server.address()
    let navigationRejected = false
    try {
      await page.goto(`https://localhost:${address.port}/`)
    } catch {
      navigationRejected = true
    }
    requireBehavior(navigationRejected, 'Chromium accepted a certificate outside the local gateway SPKI pin.')
  } finally {
    if (server?.listening) await new Promise(resolveServer => server.close(resolveServer))
    rmSync(directory, { recursive: true, force: true })
    await page.close()
  }
})
