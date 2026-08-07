import { defineStore } from "pinia";

import type { Task } from "@/types/task";

export const useTaskStore = defineStore("tasks", {
  state: () => ({
    tasks: {} as Record<string, Task>,
    activeTaskId: null as string | null,
  }),
  getters: {
    activeTask(state): Task | null {
      return state.activeTaskId ? (state.tasks[state.activeTaskId] ?? null) : null;
    },
  },
  actions: {
    upsertTask(task: Task) {
      this.tasks[task.id] = task;
    },
    setActiveTask(taskId: string | null) {
      this.activeTaskId = taskId;
    },
  },
});
