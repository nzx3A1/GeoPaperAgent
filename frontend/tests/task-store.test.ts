import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";

import { useTaskStore } from "@/stores/task";

describe("task store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("tracks the active task", () => {
    const store = useTaskStore();
    store.upsertTask({
      id: "task-1",
      type: "paper_reader",
      status: "running",
      progress: 25,
      message: "Reading paper",
    });
    store.setActiveTask("task-1");

    expect(store.activeTask?.progress).toBe(25);
  });
});
