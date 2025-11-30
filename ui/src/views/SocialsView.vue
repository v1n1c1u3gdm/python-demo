<template>
  <div class="socials-view">
    <p v-if="isLoading" class="socials-view__state">Carregando redes...</p>
    <p v-else-if="error" class="socials-view__state socials-view__state--error">
      {{ error }}
    </p>
    <p v-else-if="!normalizedSocials.length" class="socials-view__state">
      Nenhuma rede social cadastrada.
    </p>
    <div v-else class="socials-view__list">
      <a
        v-for="social in normalizedSocials"
        :key="social.slug"
        :href="social.profile_link"
        :aria-label="social.description || social.slug"
        :title="social.description || social.slug"
        target="_blank"
        rel="noopener noreferrer"
        :class="['social-icon', social.variant]"
      >
        <i :class="social.icon"></i>
      </a>
    </div>
  </div>
</template>

<script>
import { fetchSocials } from '@/services/socialsService'

const ICON_MAP = {
  twitter: { icon: 'fab fa-twitter', variant: 'twitter' },
  instagram: { icon: 'fab fa-instagram', variant: 'instagram' },
  linkedin: { icon: 'fab fa-linkedin', variant: 'linkedin' },
  github: { icon: 'fab fa-github', variant: 'github' },
  youtube: { icon: 'fab fa-youtube', variant: 'youtube' },
  mastodon: { icon: 'fab fa-mastodon', variant: 'mastodon' }
}

export default {
  name: 'SocialsView',
  data() {
    return {
      socials: [],
      isLoading: false,
      error: null
    }
  },
  computed: {
    normalizedSocials() {
      if (!Array.isArray(this.socials)) return []
      return this.socials
        .filter(item => item?.slug && item?.profile_link)
        .map(item => {
          const iconData = ICON_MAP[item.slug.toLowerCase()] || {
            icon: 'fas fa-external-link-alt',
            variant: 'default'
          }
          return {
            slug: item.slug,
            profile_link: item.profile_link,
            description: item.description,
            ...iconData
          }
        })
    }
  },
  created() {
    this.loadSocials()
  },
  methods: {
    async loadSocials() {
      this.isLoading = true
      this.error = null

      try {
        const socials = await fetchSocials()
        this.socials = socials || []
      } catch (err) {
        console.error(err)
        this.error = 'Falha ao carregar redes sociais.'
        this.socials = []
      } finally {
        this.isLoading = false
      }
    }
  }
}
</script>

<style scoped>
.socials-view__list {
  display: flex;
  align-items: center;
  margin-left: auto;
}

.socials-view__list .social-icon {
  margin-right: 0.75rem;
  color: var(--gray-2);
  font-size: 1rem;
}

.socials-view__list .social-icon:last-child {
  margin-right: 0;
}

.socials-view__list .social-icon:hover {
  color: var(--color);
}

.socials-view__state {
  margin: 0;
  font-size: 0.75rem;
  color: var(--gray-2);
}

.socials-view__state--error {
  color: #b00020;
}

@media (max-width: 768px) {
  .socials-view__list {
    margin-left: 0;
  }
}
</style>

