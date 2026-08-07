<script setup lang="ts">
import { RouterLink, RouterView } from "vue-router";
</script>

<template>
  <div class="shell">
    <header class="topbar">
      <RouterLink class="brand" to="/">
        <span class="brand-mark">GP</span>
        <span>
          <strong>GeoPaperAgent</strong>
          <small>Research workspace</small>
        </span>
      </RouterLink>

      <nav aria-label="Primary navigation">
        <RouterLink to="/">Dashboard</RouterLink>
        <RouterLink to="/tasks">Tasks</RouterLink>
      </nav>
    </header>

    <main>
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.shell {
  min-height: 100vh;
}

.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  min-height: 72px;
  padding: 12px clamp(20px, 5vw, 64px);
  border-bottom: 1px solid #dce3ee;
  background: rgb(255 255 255 / 92%);
  backdrop-filter: blur(14px);
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brand-mark {
  display: grid;
  width: 40px;
  height: 40px;
  place-items: center;
  border-radius: 12px;
  color: #ffffff;
  background: #2457d6;
  font-weight: 800;
}

.brand strong,
.brand small {
  display: block;
}

.brand small {
  margin-top: 2px;
  color: #71809a;
}

nav {
  display: flex;
  align-items: center;
  gap: 8px;
}

nav a {
  padding: 9px 14px;
  border-radius: 10px;
  color: #53617a;
  font-weight: 600;
}

nav a.router-link-exact-active {
  color: #1d48b5;
  background: #eaf0ff;
}

main {
  width: min(1180px, calc(100% - 40px));
  margin: 0 auto;
  padding: clamp(40px, 7vw, 80px) 0;
}

@media (max-width: 560px) {
  .brand small {
    display: none;
  }

  .topbar {
    padding-inline: 16px;
  }

  nav a {
    padding-inline: 10px;
  }
}
</style>
