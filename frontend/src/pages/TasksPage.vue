<script setup lang="ts">
import { storeToRefs } from "pinia";

import { useTaskStore } from "@/stores/task";

const taskStore = useTaskStore();
const { tasks } = storeToRefs(taskStore);
</script>

<template>
  <section class="page">
    <div class="page-heading">
      <h1>Tasks</h1>
      <p>Monitor background parsing, research, and extraction jobs.</p>
    </div>

    <div class="surface task-list">
      <p v-if="Object.keys(tasks).length === 0">No tasks have been started.</p>
      <article v-for="task in tasks" v-else :key="task.id">
        <strong>{{ task.type }}</strong>
        <span>{{ task.status }}</span>
      </article>
    </div>
  </section>
</template>

<style scoped>
.task-list {
  padding: 24px;
}

.task-list p {
  margin: 0;
  color: #68758c;
}

.task-list article {
  display: flex;
  justify-content: space-between;
  padding: 14px 0;
  border-bottom: 1px solid #e7ebf2;
}
</style>
