import http from "./http";
import type { Task, TaskProgressEvent } from "@/types/task";
import { openEventStream } from "@/utils/sse";

export async function getTask(taskId: string): Promise<Task> {
  const response = await http.get<Task>(`/tasks/${taskId}`);
  return response.data;
}

export function subscribeToTask(taskId: string, onMessage: (event: TaskProgressEvent) => void): Promise<void> {
  const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";
  return openEventStream(`${baseUrl}/tasks/${taskId}/events`, onMessage);
}
