export type TaskStatus = "pending" | "running" | "completed" | "failed";

export interface Task {
  id: string;
  type: string;
  status: TaskStatus;
  progress: number;
  message: string | null;
}

export interface TaskProgressEvent {
  taskId: string;
  status: TaskStatus;
  progress: number;
  message: string | null;
}
