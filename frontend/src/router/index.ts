import { createRouter, createWebHistory } from "vue-router";

import AppLayout from "@/layouts/AppLayout.vue";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: "/",
      component: AppLayout,
      children: [
        {
          path: "",
          name: "dashboard",
          component: () => import("@/pages/DashboardPage.vue"),
          meta: { title: "Dashboard" },
        },
        {
          path: "projects/:projectId/papers",
          name: "papers",
          component: () => import("@/pages/PapersPage.vue"),
          meta: { title: "Papers" },
        },
        {
          path: "projects/:projectId/reader/:paperId",
          name: "reader",
          component: () => import("@/pages/ReaderPage.vue"),
          meta: { title: "Paper Reader" },
        },
        {
          path: "projects/:projectId/research",
          name: "research",
          component: () => import("@/pages/ResearchPage.vue"),
          meta: { title: "Topic Research" },
        },
        {
          path: "projects/:projectId/extraction",
          name: "extraction",
          component: () => import("@/pages/ExtractionPage.vue"),
          meta: { title: "Data Extraction" },
        },
        {
          path: "tasks",
          name: "tasks",
          component: () => import("@/pages/TasksPage.vue"),
          meta: { title: "Tasks" },
        },
      ],
    },
  ],
});

router.afterEach((to) => {
  document.title = `${String(to.meta.title ?? "Workspace")} | GeoPaperAgent`;
});

export default router;
