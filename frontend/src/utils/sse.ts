import { fetchEventSource } from "@microsoft/fetch-event-source";

export async function openEventStream<T>(url: string, onMessage: (event: T) => void): Promise<void> {
  await fetchEventSource(url, {
    headers: {
      Accept: "text/event-stream",
    },
    onmessage(message) {
      onMessage(JSON.parse(message.data) as T);
    },
  });
}
