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
          <form
            v-if="!isAuthenticated"
            class="admin-login__form"
            autocomplete="off"
            @submit.prevent="handleSubmit"
          >
            <label
              class="admin-login__label"
              for="admin-user"
            >Usuário</label>
            <input
              id="admin-user"
              v-model.trim="credentials.username"
              type="text"
              required
              placeholder="admin"
              autocomplete="off"
            >

            <label
              class="admin-login__label"
              for="admin-password"
            >Senha</label>
            <input
              id="admin-password"
              v-model="credentials.password"
              type="password"
              required
              placeholder="••••••••"
              autocomplete="off"
            >

            <button
              class="btn admin-login__submit"
              type="submit"
              :disabled="isSubmitting"
            >
              {{ isSubmitting ? 'Autenticando...' : 'Entrar' }}
            </button>
          </form>

          <div
            v-else
            class="admin-login__session"
          >
            <p class="admin-login__session-title">
              Autenticado como <strong>{{ session.username }}</strong>
            </p>
            <p class="admin-login__session-roles">
              Roles: {{ session.roles.join(', ') || '—' }}
            </p>

            <div class="admin-login__session-actions">
              <button
                class="btn"
                type="button"
                :disabled="isProfileLoading"
                @click="loadProfile"
              >
                {{ isProfileLoading ? 'Sincronizando...' : 'Atualizar perfil' }}
              </button>
              <button
                class="btn btn--link"
                type="button"
                @click="handleLogout"
              >
                Sair
              </button>
            </div>
          </div>
        </article>

        <article
          v-if="isAuthenticated && isAdmin"
          class="admin-articles"
          aria-labelledby="admin-articles-title"
        >
          <h3 id="admin-articles-title">
            HTML bruto dos artigos
          </h3>
          <p>O bypass permite que o HTML do artigo seja publicado sem sanitização.</p>
          <label for="admin-article-select">Artigo existente</label>
          <select
            id="admin-article-select"
            v-model="selectedArticleId"
            aria-label="Artigo existente"
            :disabled="isArticlesLoading || isBypassSaving || adminArticles.length === 0"
            @change="selectArticle"
          >
            <option value="">
              Selecione um artigo
            </option>
            <option
              v-for="article in adminArticles"
              :key="article.id"
              :value="String(article.id)"
            >
              {{ article.title }}
            </option>
          </select>
          <p
            v-if="isArticlesLoading"
            role="status"
          >
            Carregando artigos...
          </p>
          <p
            v-else-if="adminArticles.length === 0"
            role="status"
          >
            Nenhum artigo disponível.
          </p>

          <div
            v-if="selectedArticle"
            class="admin-articles__bypass"
          >
            <label for="article-bypass-sanitization">
              <input
                id="article-bypass-sanitization"
                v-model="draftBypassSanitization"
                type="checkbox"
                :disabled="isBypassSaving"
              >
              Permitir HTML sem sanitização
            </label>
            <button
              class="btn"
              type="button"
              aria-label="Salvar bypass de sanitização"
              :disabled="isBypassSaving || draftBypassSanitization === selectedArticle.bypass_sanitization"
              @click="saveArticleBypass"
            >
              {{ isBypassSaving ? 'Salvando...' : 'Salvar' }}
            </button>
          </div>
          <p
            v-if="bypassStatus"
            role="status"
          >
            {{ bypassStatus }}
          </p>
        </article>

        <p
          v-if="errorMessage"
          class="admin-login__error"
        >
          {{ errorMessage }}
        </p>

        <article
          v-if="profile"
          class="admin-profile"
        >
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
import { fetchArticles, updateArticleSanitizationBypass } from '@/services/articlesService'

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
      errorMessage: null,
      adminArticles: [],
      selectedArticleId: '',
      draftBypassSanitization: false,
      isArticlesLoading: false,
      isBypassSaving: false,
      bypassStatus: null,
      bypassOperation: 0
    }
  },
  computed: {
    isAuthenticated() {
      return Boolean(this.session?.access_token)
    },
    isAdmin() {
      return Boolean(this.session?.roles?.includes('admin'))
    },
    selectedArticle() {
      return this.adminArticles.find(article => String(article.id) === this.selectedArticleId) || null
    }
  },
  created() {
    const storedSession = getStoredSession()
    if (storedSession) {
      this.session = storedSession
      this.loadProfile()
      if (this.isAdmin) this.loadAdminArticles()
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
        if (this.isAdmin) await this.loadAdminArticles()
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
    async loadAdminArticles() {
      if (!this.isAdmin || !this.session?.access_token) return
      const token = this.session.access_token
      const operation = this.bypassOperation
      this.isArticlesLoading = true

      try {
        const articles = await fetchArticles({ force: true })
        if (operation === this.bypassOperation && this.session?.access_token === token && this.isAdmin) {
          this.adminArticles = articles
        }
      } catch (error) {
        if (operation === this.bypassOperation && this.session?.access_token === token) {
          this.errorMessage = error?.message || 'Não foi possível carregar os artigos.'
        }
      } finally {
        if (operation === this.bypassOperation) this.isArticlesLoading = false
      }
    },
    selectArticle() {
      this.errorMessage = null
      this.bypassStatus = null
      this.draftBypassSanitization = Boolean(this.selectedArticle?.bypass_sanitization)
    },
    async saveArticleBypass() {
      if (!this.isAdmin || !this.selectedArticle || !this.session?.access_token || this.isBypassSaving) return
      const articleId = this.selectedArticle.id
      const token = this.session.access_token
      const operation = this.bypassOperation
      this.errorMessage = null
      this.bypassStatus = null
      this.isBypassSaving = true

      try {
        const updatedArticle = await updateArticleSanitizationBypass(
          articleId,
          this.draftBypassSanitization,
          token
        )
        if (operation !== this.bypassOperation || this.session?.access_token !== token) return
        this.adminArticles = this.adminArticles.map(article =>
          article.id === articleId ? updatedArticle : article
        )
        this.draftBypassSanitization = Boolean(updatedArticle.bypass_sanitization)
        this.bypassStatus = 'Bypass atualizado.'
      } catch (error) {
        if (operation !== this.bypassOperation || this.session?.access_token !== token) return
        this.draftBypassSanitization = Boolean(this.selectedArticle?.bypass_sanitization)
        this.errorMessage = error?.message || 'Não foi possível atualizar o bypass.'
      } finally {
        if (operation === this.bypassOperation) this.isBypassSaving = false
      }
    },
    handleLogout() {
      this.bypassOperation += 1
      clearSession()
      this.session = null
      this.profile = null
      this.adminArticles = []
      this.selectedArticleId = ''
      this.draftBypassSanitization = false
      this.isArticlesLoading = false
      this.isBypassSaving = false
      this.bypassStatus = null
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

.admin-articles {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  margin-top: 1.5rem;
  padding: 1.5rem;
  border: 1px solid var(--lighter);
  border-radius: 1rem;
  background: var(--white);
}

.admin-articles h3,
.admin-articles p {
  margin-bottom: 0;
}

.admin-articles select {
  border: 1px solid var(--gray-2);
  border-radius: 4px;
  padding: 0.6rem;
}

.admin-articles__bypass {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
}

.admin-articles__bypass label {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin: 0;
}
</style>
