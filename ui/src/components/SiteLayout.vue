<template>
  <div :class="['layout-wrapper js-container', { 'is-menu': isMenuOpen }]">
    <nav class="menu" aria-label="Menu lateral">
      <ul class="navbar__menu">
        <li
          v-for="link in navLinks"
          :key="link.label"
          :class="{ active: isLinkActive(link) }"
        >
          <router-link
            v-if="link.routeName"
            :to="{ name: link.routeName }"
            class="navbar__link"
            @click.native="handleMenuLinkClick"
          >
            {{ link.label }}
          </router-link>

          <a
            v-else
            :href="link.href"
            :target="link.external ? '_blank' : '_self'"
            :rel="link.external ? 'noopener noreferrer' : 'noopener'"
            @click="handleMenuLinkClick"
          >
            {{ link.label }}
          </a>
        </li>
      </ul>
    </nav>

    <div class="content">
      <header class="top">
        <div class="top__item"></div>
        <div class="top__item top__item--right">
          <button
            type="button"
            class="menu-toggle js-menu-toggle"
            :class="{ 'is-active': isMenuOpen }"
            aria-label="Menu"
            :aria-expanded="isMenuOpen ? 'true' : 'false'"
            @click.prevent="handleMenuToggle"
          >
            <span class="menu-toggle-box">
              <span class="menu-toggle-inner">Menu</span>
            </span>
          </button>
        </div>
      </header>

      <main class="main">
        <section class="main__left">
          <slot name="main-left" />
        </section>

        <section class="main__right">
          <div class="main__right-inner">
            <slot name="main-right" />

            <footer class="footer">
              <div class="footer__copyright">Do primeiro e único... VGDM ®</div>
              <div class="footer__social">
                <SocialsView />
              </div>
            </footer>
          </div>
        </section>
      </main>
    </div>
  </div>
</template>

<script>
import SocialsView from '@/views/SocialsView.vue'

export default {
  name: 'SiteLayout',
  components: {
    SocialsView
  },
  data() {
    return {
      isMenuOpen: false,
      navLinks: [
        { label: 'inicio', routeName: 'home' },
        { label: 'drive', href: 'https://viniciusmenezes.com/share/' },
        { label: 'estudos', href: 'https://viniciusmenezes.com/bookstack', external: true },
        { label: 'postagens por tipo', href: 'https://viniciusmenezes.com/tags/' },
        { label: 'profissional', href: 'https://www.linkedin.com/in/menezesvinicius/', external: true },
        { label: 'sobre', routeName: 'about' }
      ]
    }
  },
  methods: {
    handleMenuToggle() {
      this.isMenuOpen = !this.isMenuOpen
    },
    handleMenuLinkClick() {
      this.isMenuOpen = false
    },
    isLinkActive(link) {
      if (link.routeName) {
        return this.$route?.name === link.routeName
      }
      return Boolean(link.active)
    },
    setBodyScrollState(active) {
      if (typeof document === 'undefined') return
      document.body.classList.toggle('no-scroll', active)
    }
  },
  watch: {
    isMenuOpen(value) {
      this.setBodyScrollState(value)
    }
  },
  mounted() {
    this.setBodyScrollState(this.isMenuOpen)
  },
  beforeDestroy() {
    this.setBodyScrollState(false)
  }
}
</script>

<style>
.layout-wrapper {
  position: relative;
  min-height: 100vh;
  background: var(--white);
  color: var(--text-color);
}

.menu {
  position: fixed;
  top: 0;
  right: 0;
  width: var(--side-nav-width);
  height: 100vh;
  background: var(--side-nav-bg);
  padding: 2rem;
  overflow-y: auto;
  transform: translateX(var(--side-nav-width));
  transition: transform 0.3s ease;
  z-index: 2100;
}

.layout-wrapper.is-menu .menu {
  transform: translateX(0);
}

.navbar__menu {
  list-style: none;
  margin: 0;
  padding: 0;
}

.navbar__menu li {
  color: var(--side-nav-link);
  font-family: var(--menu-font);
  letter-spacing: 1px;
  padding: 0.35rem 0;
  text-transform: uppercase;
}

.navbar__menu li a,
.navbar__menu li .navbar__link {
  color: inherit;
}

.navbar__menu li a:hover,
.navbar__menu li .navbar__link:hover,
.navbar__menu li.active a,
.navbar__menu li.active .navbar__link {
  color: var(--side-nav-link-hover);
  text-decoration: none;
}

.content {
  min-height: 100vh;
  background: var(--white);
  transition: transform 0.5s cubic-bezier(0, 0.98, 1, 1);
  will-change: transform;
  overflow-x: hidden;
}

.layout-wrapper.is-menu .content {
  transform: translateX(calc(-1 * var(--side-nav-width)));
}

.top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1.5rem 4%;
  position: relative;
  z-index: 3;
  border-bottom: 1px solid var(--hero-border-color);
  color: var(--white);
}

.logo {
  color: var(--logo-color);
  font-weight: var(--headings-weight);
  text-transform: uppercase;
  text-decoration: none;
  letter-spacing: 1px;
}

.top__item {
  display: flex;
  align-items: center;
  width: 32%;
}

.top__item--right {
  justify-content: flex-end;
}

.menu-toggle {
  background: transparent;
  border: none;
  cursor: pointer;
  padding: 0;
  width: 1.5rem;
  height: 1.5rem;
  position: relative;
  z-index: 2200;
}

.menu-toggle-box {
  width: 1.5rem;
  height: 16px;
  display: inline-block;
  position: relative;
}

.menu-toggle-inner,
.menu-toggle-inner::before,
.menu-toggle-inner::after {
  width: 1.5rem;
  height: 2px;
  background-color: var(--white);
  position: absolute;
  transition: transform 0.15s ease, opacity 0.15s ease, top 0.15s ease, bottom 0.15s ease;
}

.menu-toggle-inner {
  top: 50%;
  transform: translateY(-50%);
  text-indent: -9999px;
}

.menu-toggle-inner::before,
.menu-toggle-inner::after {
  content: '';
  left: 0;
}

.menu-toggle-inner::before {
  top: -6px;
}

.menu-toggle-inner::after {
  bottom: -6px;
}

.menu-toggle.is-active .menu-toggle-inner {
  transform: rotate(45deg);
}

.menu-toggle.is-active .menu-toggle-inner::before {
  top: 0;
  opacity: 0;
}

.menu-toggle.is-active .menu-toggle-inner::after {
  bottom: 0;
  transform: rotate(-90deg);
}

.main {
  display: flex;
  flex-direction: column;
}

.main__left {
  position: relative;
  background: var(--hero-bg);
  color: var(--hero-text-color);
}

.main__right {
  padding: 2rem 4%;
  background: var(--white);
  color: var(--text-color);
}

.main__right-inner {
  width: 100%;
  max-width: none;
}

.footer {
  border-top: 1px solid var(--lighter);
  margin-top: 3rem;
  padding-top: 1.5rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}

.footer__social {
  display: flex;
  align-items: center;
  margin-left: auto;
}

.footer__social .social-icon {
  margin-right: 0.75rem;
  color: var(--gray-2);
  font-size: 1rem;
}

.footer__social .social-icon:last-child {
  margin-right: 0;
}

.footer__social .social-icon:hover {
  color: var(--color);
}

.footer__copyright {
  color: var(--dark);
  font-size: 0.7rem;
  letter-spacing: 1px;
  text-transform: uppercase;
}

@media (max-width: 768px) {
  .footer {
    flex-direction: column;
    align-items: flex-start;
  }

  .footer__social {
    margin-left: 0;
  }
}

@media (max-width: 991.98px) {
  .layout-wrapper.is-menu {
    transform: none;
  }

  .menu {
    width: 100%;
    max-width: 100%;
  }
}

@media (min-width: 992px) {
  .layout-wrapper {
    height: 100vh;
    overflow: hidden;
  }

  .content {
    height: 100vh;
    overflow: hidden;
  }

  .top {
    position: fixed;
    width: 60%;
    right: 0;
    padding: 1.5rem 6%;
    background: var(--white);
    border-bottom: 1px solid var(--hero-border-color);
    color: var(--dark);
  }

  .logo {
    color: var(--dark);
  }

  .menu-toggle-inner,
  .menu-toggle-inner::before,
  .menu-toggle-inner::after {
    background-color: var(--black);
  }

  .main {
    min-height: 100vh;
  }

  .main__left {
    position: fixed;
    top: 0;
    left: 0;
    width: 40vw;
    height: 100vh;
  }

  .main__right {
    margin-left: 40vw;
    padding: calc(var(--navbar-height) + 3.75rem) 6% 3rem;
    height: 100vh;
    overflow-y: auto;
  }
}
</style>

