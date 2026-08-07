<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import http from "@/api/http";

type HealthState = "checking" | "ready" | "unavailable";

const healthState = ref<HealthState>("checking");

const healthLabel = computed(() => {
  const labels: Record<HealthState, string> = {
    checking: "Checking backend",
    ready: "Backend ready",
    unavailable: "Backend unavailable",
  };
  return labels[healthState.value];
});

onMounted(async () => {
  try {
    await http.get("/health");
    healthState.value = "ready";
  } catch {
    healthState.value = "unavailable";
  }
});
</script>

<template>
  <section class="page">
    <div class="hero">
      <div class="page-heading">
        <span class="eyebrow">Scientific knowledge workspace</span>
        <h1>Turn papers into traceable research evidence.</h1>
        <p>
          Read literature, investigate topics, extract structured data, and monitor long-running agent tasks from one workspace.
        </p>
      </div>

      <div class="health" :data-state="healthState">
        <span />
        {{ healthLabel }}
      </div>
    </div>

    <div class="workflow-grid">
      <article class="surface">
        <span>01</span>
        <h2>Paper reader</h2>
        <p>Inspect documents alongside cited evidence and structured annotations.</p>
      </article>
      <article class="surface">
        <span>02</span>
        <h2>Topic research</h2>
        <p>Coordinate literature discovery and preserve the path from question to source.</p>
      </article>
      <article class="surface">
        <span>03</span>
        <h2>Data extraction</h2>
        <p>Transform tables and claims into reusable datasets with provenance.</p>
      </article>
    </div>
  </section>
</template>

<style scoped>
.hero {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 32px;
}

.eyebrow {
  color: #2457d6;
  font-size: 0.78rem;
  font-weight: 800;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.health {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 9px;
  padding: 10px 14px;
  border: 1px solid #dce3ee;
  border-radius: 999px;
  color: #58667f;
  background: #ffffff;
  font-size: 0.875rem;
  font-weight: 700;
}

.health span {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #d59b28;
}

.health[data-state="ready"] span {
  background: #1e9a61;
}

.health[data-state="unavailable"] span {
  background: #d34f4f;
}

.workflow-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px;
}

.workflow-grid article {
  min-height: 220px;
  padding: 26px;
}

.workflow-grid span {
  color: #2457d6;
  font-size: 0.8rem;
  font-weight: 800;
  letter-spacing: 0.12em;
}

.workflow-grid h2 {
  margin: 42px 0 10px;
  font-size: 1.25rem;
}

.workflow-grid p {
  margin: 0;
  color: #68758c;
  line-height: 1.6;
}

@media (max-width: 760px) {
  .hero {
    align-items: start;
    flex-direction: column;
  }

  .workflow-grid {
    grid-template-columns: 1fr;
  }
}
</style>
