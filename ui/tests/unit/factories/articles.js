let articleCounter = 0

export function makeArticle(overrides = {}) {
  articleCounter += 1
  const slugBase = overrides.slug || `article-${articleCounter}`

  return {
    id: overrides.id || articleCounter,
    slug: slugBase,
    title: overrides.title || `Article ${articleCounter}`,
    post_entry:
      overrides.post_entry ||
      'Primeiro parágrafo.\n\nSegundo parágrafo.\n\nTerceiro parágrafo com mais conteúdo.',
    created_at: overrides.created_at || '2024-01-01T12:00:00Z',
    updated_at: overrides.updated_at || '2024-01-02T12:00:00Z',
    published_label: overrides.published_label || '1 de janeiro de 2024',
    hero_image: overrides.hero_image || `https://example.com/${slugBase}.jpg`,
    author: overrides.author || {
      name: 'Autor Desconhecido',
      url: 'https://example.com/autor',
      photo_url: 'https://example.com/autor.jpg'
    },
    post_entry_html: overrides.post_entry_html,
    ...overrides
  }
}

export function makeArticles(count = 1, modifier = () => ({})) {
  return Array.from({ length: count }, (_, index) =>
    makeArticle({
      ...modifier(index)
    })
  )
}

