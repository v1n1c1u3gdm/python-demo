let socialCounter = 0

export function makeSocial(overrides = {}) {
  socialCounter += 1
  const slug = overrides.slug || `social-${socialCounter}`

  return {
    slug,
    profile_link: overrides.profile_link || `https://example.com/${slug}`,
    description: overrides.description || `Perfil em ${slug}`,
    ...overrides
  }
}

export function makeSocials(count = 1, modifier = () => ({})) {
  return Array.from({ length: count }, (_, index) =>
    makeSocial({
      ...modifier(index)
    })
  )
}

