<template>
  <SiteLayout>
    <template #main-left>
      <div class="admin-hero">
        <h1>/admin</h1>
        <p>Somente usuários autenticados podem acessar as rotas administrativas.</p>
        <p class="admin-hero__hint">
          Use os usuários provisionados no Keycloak (`admin` ou `vinicius`) para explorar a área autenticada.
        </p>
      </div>
    </template>

    <template #main-right>
      <section class="admin-login">
        <header class="admin-login__header">
          <h2>Painel autenticado</h2>
          <p>Autentique-se para testar a integração com o Keycloak.</p>
        </header>

        <article class="admin-login__card">
          <form v-if="!isAuthenticated" class="admin-login__form" @submit.prevent="handleSubmit" autocomplete="off">
            <label class="admin-login__label" for="admin-user">Usuário</label>
            <input
              id="admin-user"
              v-model.trim="credentials.username"
              type="text"
              required
              placeholder="admin"
              autocomplete="off"
            />

            <label class="admin-login__label" for="admin-password">Senha</label>
            <input
              id="admin-password"
              v-model="credentials.password"
              type="password"
              required
              placeholder="••••••••"
              autocomplete="off"
            />

            <button class="btn admin-login__submit" type="submit" :disabled="isSubmitting">
              {{ isSubmitting ? 'Autenticando...' : 'Entrar' }}
            </button>
          </form>

          <div v-else class="admin-login__session">
            <p class="admin-login__session-title">
              Autenticado como <strong>{{ session.username }}</strong>
            </p>
            <p class="admin-login__session-roles">Roles: {{ session.roles.join(', ') || '—' }}</p>

            <div class="admin-login__session-actions">
              <button class="btn" type="button" :disabled="isProfileLoading" @click="loadProfile">
                {{ isProfileLoading ? 'Sincronizando...' : 'Atualizar perfil' }}
              </button>
              <button class="btn btn--link" type="button" @click="handleLogout">Sair</button>
            </div>
          </div>
        </article>

        <p v-if="errorMessage" class="admin-login__error">{{ errorMessage }}</p>

        <article v-if="profile" class="admin-profile">
          <header>
            <h3>Perfil do Keycloak</h3>
          </header>
          <dl>
            <div>
              <dt>Usuário</dt>
              <dd>{{ profile.username || '—' }}</dd>
            </div>
            <div>
              <dt>E-mail</dt>
              <dd>{{ profile.email || '—' }}</dd>
            </div>
            <div>
              <dt>Roles</dt>
              <dd>{{ (profile.roles || []).join(', ') || '—' }}</dd>
            </div>
          </dl>
        </article>
      </section>
    </template>
  </SiteLayout>
</template>

<script>
import SiteLayout from '@/components/SiteLayout.vue'
import {
  login,
  persistSession,
  getStoredSession,
  fetchAdminProfile,
  clearSession
} from '@/services/authService'

export default {
  name: 'AdminLoginView',
  components: {
    SiteLayout
  },
  data() {
    return {
      credentials: {
        username: '',
        password: ''
      },
      session: null,
      profile: null,
      isSubmitting: false,
      isProfileLoading: false,
      errorMessage: null
    }
  },
  computed: {
    isAuthenticated() {
      return Boolean(this.session?.access_token)
    }
  },
  created() {
    const storedSession = getStoredSession()
    if (storedSession) {
      this.session = storedSession
      this.loadProfile()
    }
  },
  methods: {
    async handleSubmit() {
      this.errorMessage = null
      this.isSubmitting = true

      try {
        const session = await login(this.credentials.username, this.credentials.password)
        persistSession(session)
        this.session = session
        this.profile = null
        this.credentials.username = ''
        this.credentials.password = ''
        await this.loadProfile()
      } catch (error) {
        this.errorMessage = error?.message || 'Autenticação falhou.'
      } finally {
        this.isSubmitting = false
      }
    },
    async loadProfile() {
      if (!this.session?.access_token) return
      this.errorMessage = null
      this.isProfileLoading = true

      try {
        this.profile = await fetchAdminProfile(this.session.access_token)
      } catch (error) {
        this.errorMessage = error?.message || 'Não foi possível carregar o perfil.'
      } finally {
        this.isProfileLoading = false
      }
    },
    handleLogout() {
      clearSession()
      this.session = null
      this.profile = null
      this.errorMessage = null
    }
  }
}
</script>

<style scoped>
.admin-hero {
  padding: 2rem;
  color: var(--white);
}

.admin-hero h1 {
  font-size: 3rem;
  text-transform: uppercase;
  margin-bottom: 1rem;
}

.admin-hero__hint {
  font-size: 0.9rem;
  opacity: 0.9;
}

.admin-login {
  max-width: 520px;
  margin: 0 auto;
}

.admin-login__header h2 {
  margin-bottom: 0.5rem;
}

.admin-login__card {
  background: var(--lighter);
  padding: 2rem;
  border-radius: 1rem;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.05);
}

.admin-login__form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.admin-login__label {
  font-size: 0.85rem;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: var(--gray-1);
}

.admin-login__form input {
  border: 1px solid var(--gray-2);
  border-radius: 4px;
  padding: 0.75rem;
  font-size: 1rem;
}

.admin-login__submit {
  align-self: flex-start;
}

.admin-login__session-title {
  margin-bottom: 0.25rem;
}

.admin-login__session-roles {
  margin-bottom: 1rem;
  font-size: 0.95rem;
  color: var(--gray-1);
}

.admin-login__session-actions {
  display: flex;
  gap: 1rem;
  flex-wrap: wrap;
}

.btn--link {
  border: none;
  background: transparent;
  color: var(--color);
  padding-left: 0;
}

.admin-login__error {
  color: #b00020;
  margin-top: 1rem;
}

.admin-profile {
  margin-top: 2rem;
  padding: 1.5rem;
  border: 1px solid var(--lighter);
  border-radius: 1rem;
  background: var(--white);
}

.admin-profile dl {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 1rem;
}

.admin-profile dt {
  font-size: 0.75rem;
  text-transform: uppercase;
  color: var(--gray-1);
}

.admin-profile dd {
  margin: 0;
  font-size: 1rem;
  font-weight: 600;
}
</style>

