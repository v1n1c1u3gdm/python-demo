let authorCounter = 0

export function makeAuthor(overrides = {}) {
  authorCounter += 1
  return {
    id: overrides.id || authorCounter,
    name: overrides.name || `Autor ${authorCounter}`,
    bio:
      overrides.bio ||
      'Trilha sonora da vingança.\n\nApaixonado por tecnologia e narrativas sangrentas.',
    birthdate: overrides.birthdate || '1990-05-12',
    photo_url: overrides.photo_url || 'https://example.com/author.jpg',
    photo_srcset: overrides.photo_srcset || null,
    public_key: overrides.public_key || 'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQD',
    ...overrides
  }
}

export function makeAuthors(count = 1, modifier = () => ({})) {
  return Array.from({ length: count }, (_, index) =>
    makeAuthor({
      ...modifier(index)
    })
  )
}

