export interface PreviewFrame {
  feed: string;
  bytes: Uint8Array;
}

const HEADER_END = [13, 10, 13, 10];

function indexOfHeaderEnd(buffer: Uint8Array, from: number): number {
  for (let i = from; i + 3 < buffer.length; i++) {
    if (buffer[i] === HEADER_END[0] && buffer[i + 1] === HEADER_END[1]
      && buffer[i + 2] === HEADER_END[2] && buffer[i + 3] === HEADER_END[3]) {
      return i;
    }
  }
  return -1;
}

export class PreviewStreamParser {
  private buffer = new Uint8Array(0);

  push(chunk: Uint8Array): PreviewFrame[] {
    const merged = new Uint8Array(this.buffer.length + chunk.length);
    merged.set(this.buffer);
    merged.set(chunk, this.buffer.length);
    this.buffer = merged;

    const frames: PreviewFrame[] = [];
    for (;;) {
      const headerEnd = indexOfHeaderEnd(this.buffer, 0);
      if (headerEnd === -1) break;
      const header = new TextDecoder().decode(this.buffer.subarray(0, headerEnd));
      const length = Number(/content-length:\s*(\d+)/i.exec(header)?.[1] ?? NaN);
      const feed = /x-feed:\s*(.+)/i.exec(header)?.[1]?.trim() ?? "";
      if (!Number.isFinite(length) || !feed) {
        this.buffer = this.buffer.subarray(headerEnd + 4);
        continue;
      }
      const start = headerEnd + 4;
      if (this.buffer.length < start + length) break;
      frames.push({ feed, bytes: this.buffer.slice(start, start + length) });
      this.buffer = this.buffer.subarray(start + length);
    }
    return frames;
  }
}

export function startPreviewStream(
  onFrame: (feed: string, url: string) => void,
  signal: AbortSignal,
  onState?: (open: boolean) => void,
): void {
  const run = async () => {
    let backoff = 1000;
    while (!signal.aborted) {
      try {
        const response = await fetch("/api/live/preview", { signal });
        if (!response.ok || !response.body) throw new Error(String(response.status));
        onState?.(true);
        backoff = 1000;
        const reader = response.body.getReader();
        const parser = new PreviewStreamParser();
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          for (const frame of parser.push(value)) {
            onFrame(
              frame.feed,
              URL.createObjectURL(new Blob([frame.bytes as BlobPart], { type: "image/jpeg" })),
            );
          }
        }
      } catch {
        if (signal.aborted) return;
      }
      onState?.(false);
      await new Promise((resolve) => setTimeout(resolve, backoff));
      backoff = Math.min(backoff * 2, 15000);
    }
  };
  void run();
}
