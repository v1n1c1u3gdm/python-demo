<template>
  <SiteLayout>
    <template #main-left>
      <div v-if="isLoading" class="state state--info">Carregando artigo...</div>
      <div v-else-if="error" class="state state--error">{{ error }}</div>
      <div v-else-if="!article" class="state">Selecione um artigo para visualizar.</div>

      <template v-else>
        <figure class="hero">
          <img
            :src="article.heroImage"
            :alt="article.heroAlt"
            loading="eager"
            :srcset="article.heroSrcset || null"
            :sizes="article.heroSizes || null"
          />
          <div class="hero-overlay">
            <h2 class="hero-title">
              <router-link to="/">
                {{ hero.title }}
              </router-link>
            </h2>
            <hr class="divider" />
          </div>
          <span class="sr-only">{{ article.title }}</span>
        </figure>
        <header class="article-hero-header">
          <h1>{{ article.title }}</h1>
          <p>
            <time :datetime="article.datetime">{{ article.dateLabel }}</time>
            by
            <template v-if="article.author && article.author.name">
              <a
                v-if="article.author.url"
                :href="article.author.url"
                rel="author noopener noreferrer"
                target="_blank"
                :title="article.author.name"
              >
                {{ article.author.name }}
              </a>
              <span v-else>{{ article.author.name }}</span>
            </template>
          </p>
        </header>
      </template>
    </template>

    <template #main-right>
      <div v-if="isLoading" class="state state--info">Carregando artigo...</div>
      <div v-else-if="error" class="state state--error">{{ error }}</div>
      <div v-else-if="!article" class="state">Selecione um artigo para visualizar.</div>

      <article v-else class="post">
        <div class="post__entry">
          <p
            v-for="(paragraph, index) in article.content"
            :key="`paragraph-${index}`"
            v-html="paragraph"
          />
          <p v-if="!article.content.length" class="post__placeholder">
            Este artigo ainda não possui conteúdo disponível para exibição.
          </p>
        </div>
      </article>
    </template>
  </SiteLayout>
</template>

<script>
import SiteLayout from '@/components/SiteLayout.vue'
import { fetchArticles, fetchArticleBySlug, buildArticleUrl } from '@/services/articlesService'
import defaultHeroImage from '@/assets/hero.jpg'

export default {
  name: 'ArticleView',
  components: {
    SiteLayout
  },
  data() {
    return {
      hero: {
        title: 'Risadas, ódio e sangue',
        subtitle: 'Bem-vindo, Bienvenido, Welcome'
      },
      article: null,
      isLoading: false,
      error: null
    }
  },
  computed: {
    shareLinks() {
      if (!this.article || !this.article.url) {
        return []
      }

      const encodedUrl = encodeURIComponent(this.article.url)
      const encodedTitle = encodeURIComponent(this.article.title || '')

      return [
        {
          label: 'Compartilhar no Twitter',
          icon: 'fab fa-twitter',
          href: `https://twitter.com/share?url=${encodedUrl}&text=${encodedTitle}`
        },
        {
          label: 'Compartilhar no LinkedIn',
          icon: 'fab fa-linkedin',
          href: `https://www.linkedin.com/sharing/share-offsite/?url=${encodedUrl}`
        },
        {
          label: 'Compartilhar no WhatsApp',
          icon: 'fab fa-whatsapp',
          href: `https://api.whatsapp.com/send?text=${encodedTitle}%20${encodedUrl}`
        }
      ]
    }
  },
  created() {
    this.loadArticle()
  },
  watch: {
    '$route.params.slug'() {
      this.loadArticle()
    }
  },
  methods: {
    async loadArticle() {
      this.isLoading = true
      this.error = null

      try {
        const slug = this.$route.params.slug
        const articleData = await this.resolveArticleData(slug)

        if (!articleData) {
          this.article = null
          this.error = slug ? 'Artigo não encontrado.' : 'Nenhum artigo disponível.'
          return
        }

        this.article = this.normalizeArticle(articleData)
      } catch (err) {
        console.error(err)
        this.error = 'Não foi possível carregar o artigo. Tente novamente.'
        this.article = null
      } finally {
        this.isLoading = false
      }
    },
    async resolveArticleData(slug) {
      if (slug) {
        return fetchArticleBySlug(slug)
      }

      const articles = await fetchArticles()
      return articles[0]
    },
    normalizeArticle(articleData) {
      if (!articleData) {
        return null
      }

      const authorName = articleData.author?.name || 'Autor desconhecido'
      const datetime = articleData.created_at || articleData.updated_at || new Date().toISOString()

      const heroImage = articleData.hero_image || articleData.author?.photo_url || this.getDefaultHeroImage()
      const slug = articleData.slug || null
      return {
        id: articleData.id,
        title: articleData.title,
        heroImage,
        heroSrcset: this.buildHeroSrcset(articleData, heroImage),
        heroSizes: articleData.hero_sizes || '(min-width: 900px) 40vw, 100vw',
        heroAlt: articleData.hero_alt || `Foto de ${authorName}`,
        dateLabel: articleData.published_label || this.formatDate(datetime),
        datetime,
        author: {
          name: authorName,
          url: this.extractAuthorUrl(articleData)
        },
        content: this.extractContent(articleData.post_entry),
        url: buildArticleUrl(slug)
      }
    },
    extractContent(entry) {
      if (!entry) return []

      return entry
        .split(/\n{2,}/)
        .map(paragraph => paragraph.replace(/\n/g, '<br />').trim())
        .filter(Boolean)
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
    getDefaultHeroImage() {
      return defaultHeroImage
    },
    buildHeroSrcset(articleData, fallback) {
      return articleData.hero_srcset || articleData.author?.photo_srcset || fallback || null
    },
    extractAuthorUrl(articleData) {
      return articleData.author?.url || articleData.author_url || null
    }
  }
}
</script>

<style scoped>
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.hero {
  margin: 0;
  position: relative;
  height: var(--hero-height);
}

.hero::after {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--hero-overlay);
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
  padding: 2rem;
  display: flex;
  flex-direction: column;
  justify-content: flex-start;
  z-index: 1;
}

.hero-title {
  color: var(--white);
  font-weight: var(--headings-weight);
  text-transform: none;
  letter-spacing: 0;
  margin: 0;
}

.hero-title a {
  color: inherit;
  text-decoration: none;
}

.divider {
  border-top: 1px solid rgba(255, 255, 255, 0.3);
  width: 100%;
  margin: 1rem 0;
}

.hero-subtitle {
  color: var(--white);
  margin: 0;
}

.article-hero-header {
  padding: 1.5rem 4%;
  color: var(--white);
}

.article-hero-header h1 {
  margin: 0 0 0.5rem;
  font-size: clamp(2rem, 4vw, 3rem);
  color: var(--white);
}

.article-hero-header p {
  margin: 0;
  color: var(--white);
  text-transform: uppercase;
  letter-spacing: 1px;
  font-size: 0.85rem;
}

.article-hero-header a {
  color: inherit;
  text-decoration: underline;
  letter-spacing: 0;
  text-transform: none;
}

.post {
  color: var(--text-color);
}

.post__entry p {
  margin-top: 1.5rem;
  font-size: 1.05rem;
}

.post__entry p:first-of-type {
  margin-top: 0;
}

.post__tag-share {
  display: flex;
  justify-content: flex-end;
  margin-top: 3rem;
}

.post__share {
  white-space: nowrap;
}

.share-link {
  padding: 0 0.35rem;
  font-size: 1rem;
  color: var(--gray-1);
}

.share-link:hover {
  color: var(--color);
}

.share-link i {
  pointer-events: none;
}

.post__placeholder {
  color: var(--gray-1);
  font-style: italic;
}

.state {
  padding: 2rem;
  text-align: center;
  color: var(--text-color);
}

.state--info {
  color: var(--gray-1);
}

.state--error {
  color: #b00020;
}

@media (min-width: 992px) {
  .hero {
    height: 100vh;
  }

  .article-hero-header {
    position: absolute;
    top: calc(var(--navbar-height) + 3.75rem);
    left: 4%;
    right: 4%;
    color: var(--white);
  }

  .article-hero-header p {
    color: var(--white);
  }

  .article-hero-header a {
    color: var(--white);
  }
}
</style>

