export const API = import.meta.env.VITE_API_BASE || "http://localhost:8000";

async function get(path) {
  const res = await fetch(`${API}${path}`);
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

export const getHealth = () => get("/health");
export const getStats = () => get("/stats");
export const getDocuments = () => get("/documents");
export const getRelations = (type) =>
  get(`/relations${type ? `?type=${encodeURIComponent(type)}` : ""}`);
export const getFactRelations = (id) => get(`/facts/${id}/relations`);

export function getFacts({ docId, verified, grounding, q } = {}) {
  const p = new URLSearchParams();
  if (docId) p.set("doc_id", docId);
  if (verified !== undefined && verified !== null) p.set("verified", verified);
  if (grounding) p.set("grounding", grounding);
  if (q) p.set("q", q);
  return get(`/facts?${p}`);
}

/**
 * Upload a PDF and consume the pipeline's NDJSON progress stream.
 *
 * EventSource is GET-only and cannot carry a file body, so the stream is read
 * off the POST response directly. Events surface as they happen rather than
 * arriving in one batch at the end.
 */
export async function uploadPdf(file, onEvent) {
  const body = new FormData();
  body.append("file", file);

  const res = await fetch(`${API}/documents`, { method: "POST", body });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  if (!res.body) throw new Error("Streaming is not supported by this browser");

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let nl;
    while ((nl = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, nl).trim();
      buffer = buffer.slice(nl + 1);
      if (line) onEvent(JSON.parse(line));
    }
  }
  const tail = buffer.trim();
  if (tail) onEvent(JSON.parse(tail));
}
