import type { StoredFailure } from "./types";

export const API_URL: string = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, init);
  if (!response.ok) {
    throw new Error(`${init?.method ?? "GET"} ${path} failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

export function listFailures(): Promise<StoredFailure[]> {
  return request<StoredFailure[]>("/failures");
}

export async function startCheckout(): Promise<string> {
  const { url } = await request<{ url: string }>("/checkout", { method: "POST" });
  return url;
}
