<template>
  <SiteLayout>
    <template #main-left>
      <div v-if="isLoading" class="state state--info">Carregando dados do autor...</div>
      <div v-else-if="error" class="state state--error">{{ error }}</div>
      <div v-else-if="!author" class="state">Nenhum autor disponível.</div>

      <template v-else>
        <figure class="hero">
          <img
            :src="heroImage"
            :alt="`Retrato de ${author.name}`"
            loading="eager"
            :srcset="heroSrcset || null"
            :sizes="heroSizes"
          />
          <div class="hero-overlay">
            <h2 class="hero-title">
              <router-link to="/">
                {{ hero.title }}
              </router-link>
            </h2>
            <hr class="divider" />
            <p class="hero-subtitle">{{ articlesCountLabel }}</p>
          </div>
          <span class="sr-only">{{ author.name }}</span>
        </figure>
        <header class="article-hero-header">
          <h1>{{ author.name }}</h1>
          <p>
            <template v-if="author.birthdate">
              Desde <time :datetime="author.birthdate">{{ formattedBirthdate }}</time>
            </template>
          </p>
        </header>
      </template>
    </template>

    <template #main-right>
      <div v-if="isLoading" class="state state--info">Carregando dados do autor...</div>
      <div v-else-if="error" class="state state--error">{{ error }}</div>
      <div v-else-if="!author" class="state">Nenhum autor disponível.</div>

      <article v-else class="post">
        <div class="post__entry">
          <figure class="about-photo">
            <img
              :src="professionalPhoto"
              alt="Retrato profissional de Vinicius Menezes"
              loading="lazy"
            />
          </figure>
          <div class="text-column">
            <p
              v-for="(paragraph, index) in bioParagraphs"
              :key="`bio-${index}`"
              v-html="paragraph"
            />
            <p v-if="!bioParagraphs.length" class="post__placeholder">
              Nenhuma biografia cadastrada para este autor.
            </p>
          </div>
        </div>

        <section class="post__meta">
          <div v-if="author.public_key">
            <h4>Chave pública</h4>
            <code class="post__code">{{ author.public_key }}</code>
          </div>
        </section>
      </article>
    </template>
  </SiteLayout>
</template>

<script>
import SiteLayout from '@/components/SiteLayout.vue'
import { fetchAuthors, fetchArticlesCountByAuthor } from '@/services/authorsService'
import defaultHeroImage from '@/assets/hero.jpg'
import professionalPhoto from '@/assets/eu-profissional.png'

export default {
  name: 'AboutView',
  components: {
    SiteLayout
  },
  data() {
    return {
      hero: {
        title: 'Risadas, ódio e sangue',
        subtitle: 'Bem-vindo, Bienvenido, Welcome'
      },
      professionalPhoto,
      author: null,
      isLoading: false,
      error: null
    }
  },
  computed: {
    heroImage() {
      if (!this.author) return this.getDefaultHeroImage()
      return this.author.photo_url || this.getDefaultHeroImage()
    },
    heroSrcset() {
      return this.author?.photo_srcset || null
    },
    heroSizes() {
      return '(min-width: 900px) 40vw, 100vw'
    },
    articlesCountLabel() {
      if (!this.author) return ''
      const count = Number(this.author.articlesCount || 0)
      const label = count === 1 ? 'artigo publicado' : 'artigos publicados'
      return `${count} ${label}`
    },
    formattedBirthdate() {
      if (!this.author?.birthdate) return ''
      return this.formatDate(this.author.birthdate)
    },
    bioParagraphs() {
      if (!this.author?.bio) return []
      return this.extractParagraphs(this.author.bio)
    }
  },
  created() {
    this.loadAuthorData()
  },
  methods: {
    async loadAuthorData() {
      this.isLoading = true
      this.error = null

      try {
        const [authors, counts] = await Promise.all([fetchAuthors(), fetchArticlesCountByAuthor()])
        const firstAuthor = Array.isArray(authors) && authors.length ? authors[0] : null
        const countMap = this.buildCountMap(counts)

        this.author = firstAuthor
          ? {
              ...firstAuthor,
              articlesCount: countMap[firstAuthor.id] || 0
            }
          : null
      } catch (err) {
        console.error(err)
        this.error = 'Não foi possível carregar os dados do autor. Tente novamente.'
        this.author = null
      } finally {
        this.isLoading = false
      }
    },
    buildCountMap(items = []) {
      if (!Array.isArray(items)) return {}
      return items.reduce((acc, item) => {
        if (item && typeof item.author_id !== 'undefined') {
          acc[item.author_id] = Number(item.articles_count) || 0
        }
        return acc
      }, {})
    },
    extractParagraphs(value) {
      return value
        .split(/\n{2,}/)
        .map(paragraph => paragraph.replace(/\n/g, '<br />').trim())
        .filter(Boolean)
    },
    formatDate(value) {
      if (!value) return ''
      try {
        const date = this.toLocalDate(value)
        return new Intl.DateTimeFormat('pt-BR', {
          day: 'numeric',
          month: 'long',
          year: 'numeric'
        }).format(date)
      } catch (err) {
        console.warn('Erro ao formatar data', err)
        return value
      }
    },
    toLocalDate(value) {
      if (value instanceof Date) {
        return value
      }

      if (typeof value === 'string') {
        const dateOnlyMatch = value.match(/^(\d{4})-(\d{2})-(\d{2})$/)
        if (dateOnlyMatch) {
          const [, year, month, day] = dateOnlyMatch.map(Number)
          return new Date(year, month - 1, day)
        }

        const isoWithTime = value.match(
          /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?$/
        )
        if (isoWithTime) {
          return new Date(value)
        }
      }

      return new Date(value)
    },
    getDefaultHeroImage() {
      return defaultHeroImage
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

.hero-subtitle {
  color: var(--white);
  margin: 0;
  text-transform: uppercase;
  letter-spacing: 0.2rem;
  font-size: 0.85rem;
}

.divider {
  border-top: 1px solid rgba(255, 255, 255, 0.3);
  width: 100%;
  margin: 1rem 0;
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

.article-hero-header time {
  color: inherit;
}

.post {
  color: var(--text-color);
}

.post__entry {
  margin: 0;
}

.text-column {
  overflow: hidden;
}

.post__entry p {
  margin-top: 1.5rem;
  font-size: 1.05rem;
}

.post__entry p:first-of-type {
  margin-top: 0;
}

.about-photo {
  float: left;
  margin: 0 1.5rem 1rem 0;
  width: min(200px, 40vw);
  aspect-ratio: 1 / 1;
  border-radius: 16px;
  overflow: hidden;
  border: 1px solid var(--lighter);
}

.about-photo img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.post__entry p:first-of-type {
  margin-top: 0;
}

.post__placeholder {
  color: var(--gray-1);
  font-style: italic;
}

.post__meta {
  margin-top: 3rem;
  padding-top: 2rem;
  border-top: 1px solid var(--lighter);
  display: grid;
  gap: 1.5rem;
}

.post__meta h4 {
  margin: 0 0 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.2rem;
  font-size: 0.8rem;
  color: var(--gray-2);
}

.post__code {
  display: block;
  max-height: 8rem;
  overflow: auto;
  padding: 0.75rem;
  background: var(--lighter);
  border-radius: 8px;
  font-size: 0.8rem;
  line-height: 1.4;
  color: var(--dark);
  word-break: break-all;
}

.post__socials {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
}

.post__socials a {
  color: var(--color);
  text-decoration: none;
}

.post__socials a:hover {
  text-decoration: underline;
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
}
</style>

