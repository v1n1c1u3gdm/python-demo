<template>
  <SiteLayout>
    <template #main-left>
      <figure class="hero">
        <img :src="hero.image" :alt="hero.alt" loading="eager" />
        <div class="hero-overlay">
          <h1>{{ hero.title }}</h1>
          <hr class="divider" />
          <p class="hero-subtitle">{{ hero.subtitle }}</p>
        </div>
      </figure>
    </template>

    <template #main-right>
      <div class="feed">
        <p v-if="isLoading" class="feed__state">Carregando artigos...</p>

        <p v-else-if="error" class="feed__state feed__state--error">
          {{ error }}
        </p>

        <p v-else-if="!posts.length" class="feed__state">
          Nenhum artigo encontrado.
        </p>

        <template v-else>
          <article
            v-for="post in paginatedPosts"
            :key="post.slug"
            class="feed__item"
          >
            <header>
              <h2>
                <router-link :to="{ name: 'article', params: { slug: post.slug } }">
                  {{ post.title }}
                </router-link>
              </h2>
              <div class="post__meta">
                <time :datetime="post.datetime">{{ post.date }}</time>
              </div>
            </header>
            <p>{{ post.excerpt }}</p>
          </article>

          <nav
            v-if="totalPages > 1"
            class="pagination desc feed__pagination"
            aria-label="Paginação de artigos"
          >
            <button
              class="btn btn--icon"
              type="button"
              :disabled="currentPage === 1"
              @click="goToPage(currentPage - 1)"
              aria-label="Página anterior"
            >
              <span aria-hidden="true">‹</span>
            </button>

            <span class="pagination__info">
              Página {{ currentPage }} de {{ totalPages }}
            </span>

            <button
              class="btn btn--icon"
              type="button"
              :disabled="currentPage === totalPages"
              @click="goToPage(currentPage + 1)"
              aria-label="Próxima página"
            >
              <span aria-hidden="true">›</span>
            </button>
          </nav>
        </template>
      </div>
    </template>
  </SiteLayout>
</template>

<script>
import SiteLayout from '@/components/SiteLayout.vue'
import heroImage from '@/assets/hero.jpg'
import { fetchArticles, buildArticleUrl } from '@/services/articlesService'

export default {
  name: 'HomeView',
  components: {
    SiteLayout
  },
  data() {
    return {
      hero: {
        image: heroImage,
        alt: 'Vinicius Menezes sorrindo e ouvindo música',
        title: 'Risadas, ódio e sangue',
        subtitle: 'Bem-vindo, Bienvenido, Welcome'
      },
      posts: [],
      currentPage: 1,
      perPage: 10,
      isLoading: false,
      error: null
    }
  },
  created() {
    this.loadArticles()
  },
  computed: {
    paginatedPosts() {
      if (!this.posts.length) return []
      const start = (this.currentPage - 1) * this.perPage
      return this.posts.slice(start, start + this.perPage)
    },
    totalPages() {
      if (!this.posts.length) return 1
      return Math.max(1, Math.ceil(this.posts.length / this.perPage))
    }
  },
  methods: {
    async loadArticles() {
      this.isLoading = true
      this.error = null

      try {
        const articles = await fetchArticles()
        this.posts = articles.map(article => this.mapArticlePreview(article))
        this.currentPage = 1
      } catch (err) {
        console.error(err)
        this.error = 'Não foi possível carregar os artigos. Tente novamente.'
      } finally {
        this.isLoading = false
      }
    },
    mapArticlePreview(article) {
      if (!article) return {}

      const datetime = article.created_at || article.updated_at || new Date().toISOString()
      const slug = this.ensureSlug(article)
      return {
        id: article.id,
        slug,
        title: article.title,
        date: article.published_label || this.formatDate(datetime),
        datetime,
        excerpt: this.truncateText(article.post_entry),
        url: buildArticleUrl(slug)
      }
    },
    truncateText(text, limit = 200) {
      if (!text) return ''
      const normalized = text.trim()
      if (normalized.length <= limit) {
        return normalized
      }
      return `${normalized.slice(0, limit).trim()}…`
    },
    formatDate(value) {
      if (!value) return ''
      try {
        const date = new Date(value)
        return new Intl.DateTimeFormat('pt-BR', {
          weekday: 'long',
          day: 'numeric',
          month: 'long',
          year: 'numeric'
        }).format(date)
      } catch (err) {
        console.warn('Erro ao formatar data', err)
        return value
      }
    },
    goToPage(page) {
      const nextPage = Math.min(Math.max(page, 1), this.totalPages)
      this.currentPage = nextPage
    },
    ensureSlug(article) {
      return article?.slug || ''
    }
  }
}
</script>

<style scoped>
.hero {
  height: 420px;
  margin: 0;
  position: relative;
}

.hero img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.hero-overlay {
  position: absolute;
  inset: 0;
  background: var(--hero-overlay);
  padding: 2rem;
  display: flex;
  flex-direction: column;
  justify-content: flex-start;
}

.hero-overlay h1 {
  color: var(--white);
  font-weight: var(--headings-weight);
  margin-bottom: 0.5rem;
}

.divider {
  border-top: 1px solid rgba(255, 255, 255, 0.3);
  width: 100%;
  margin: 1rem 0;
}

.hero-subtitle {
  color: var(--white);
  font-size: 1rem;
  margin: 0;
}

.feed {
  width: 100%;
}

.feed__item {
  padding-bottom: 3rem;
  border-bottom: 1px solid var(--lighter);
}

.feed__item:last-of-type {
  border-bottom: none;
}

.feed__item h2 a {
  color: var(--headings-color);
}

.feed__item h2 a:hover {
  color: var(--color);
  text-decoration: none;
}

.post__meta {
  color: var(--gray-1);
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 1px;
}

.pagination {
  margin-top: 2rem;
}

.feed__pagination {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 0.75rem;
}

.pagination__info {
  font-size: 0.8rem;
  letter-spacing: 1px;
  text-transform: uppercase;
  color: var(--gray-1);
}

.feed__state {
  padding: 2rem 0;
  text-align: center;
  color: var(--gray-1);
  font-size: 0.95rem;
}

.feed__state--error {
  color: #b00020;
}

.btn {
  border: 1px solid var(--gray-2);
  color: var(--dark);
  padding: 0.5rem 1.5rem;
  text-transform: uppercase;
  font-size: 0.75rem;
  background: transparent;
}

.btn--icon {
  width: 2.5rem;
  height: 2.5rem;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 1.25rem;
  padding: 0;
  text-transform: none;
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn:hover {
  border-color: var(--color);
  color: var(--color);
  text-decoration: none;
}

@media (min-width: 992px) {
  .hero {
    height: 100vh;
  }
}
</style>

