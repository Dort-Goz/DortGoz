import type { Event, OperatorMessage } from "../types/events";

interface SyncReset {
  kind: "sync_reset";
  oldest_seq: number;
  latest_seq: number;
}

export class EventSequence {
  private lastSeq = 0;

  get current(): number { return this.lastSeq; }

  accept(event: Event): boolean {
    if (event.seq > 0 && event.seq <= this.lastSeq) return false;
    if (event.seq > 0) this.lastSeq = event.seq;
    return true;
  }

  reset(oldestSeq: number): void {
    this.lastSeq = Math.max(0, oldestSeq - 1);
  }
}

export type ConnectionState = "connecting" | "open" | "reconnecting" | "closed";

export const RECONNECT_BASE_MS = 1500;
export const RECONNECT_MAX_MS = 30000;

export function reconnectDelay(attempt: number): number {
  return Math.min(RECONNECT_BASE_MS * 2 ** Math.max(0, attempt), RECONNECT_MAX_MS);
}

export interface DortgozSocketOptions {
  onState?: (state: ConnectionState) => void;
  url?: string;
  createSocket?: (url: string) => WebSocket;
  schedule?: (fn: () => void, ms: number) => number;
  cancel?: (handle: number) => void;
}

export class DortgozSocket {
  private ws: WebSocket | null = null;
  private url: string;
  private handler: (e: Event) => void;
  private resetHandler: () => void;
  private stateHandler: (state: ConnectionState) => void;
  private createSocket: (url: string) => WebSocket;
  private schedule: (fn: () => void, ms: number) => number;
  private cancel: (handle: number) => void;
  private sequence = new EventSequence();
  private closed = false;
  private state: ConnectionState = "closed";
  private attempt = 0;
  private retryTimer: number | null = null;
  private pending: OperatorMessage[] = [];

  constructor(
    handler: (e: Event) => void,
    resetHandler: () => void = () => undefined,
    options: DortgozSocketOptions = {},
  ) {
    this.url = options.url
      ?? `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
    this.handler = handler;
    this.resetHandler = resetHandler;
    this.stateHandler = options.onState ?? (() => undefined);
    this.createSocket = options.createSocket ?? ((url) => new WebSocket(url));
    this.schedule = options.schedule
      ?? ((fn, ms) => setTimeout(fn, ms) as unknown as number);
    this.cancel = options.cancel ?? ((handle) => clearTimeout(handle));
    this.setState("connecting");
    this.connect();
  }

  get connection(): ConnectionState { return this.state; }

  private setState(next: ConnectionState) {
    if (this.state === next) return;
    this.state = next;
    this.stateHandler(next);
  }

  private connect() {
    const socket = this.createSocket(this.url);
    this.ws = socket;
    socket.onopen = () => {
      if (this.ws !== socket) return;
      this.attempt = 0;
      this.setState("open");
      socket.send(JSON.stringify({ kind: "sync", from_seq: this.sequence.current }));
      for (const msg of this.pending.splice(0)) socket.send(JSON.stringify(msg));
    };
    socket.onmessage = (msg) => {
      if (this.ws !== socket) return;
      const incoming = JSON.parse(msg.data) as Event | SyncReset;
      if ("kind" in incoming && incoming.kind === "sync_reset") {
        this.sequence.reset(incoming.oldest_seq);
        this.resetHandler();
        return;
      }
      if (this.sequence.accept(incoming as Event)) this.handler(incoming as Event);
    };
    socket.onclose = () => {
      if (this.ws !== socket) return;
      if (this.closed) {
        this.setState("closed");
        return;
      }
      this.setState("reconnecting");
      this.scheduleReconnect();
    };
  }

  private scheduleReconnect() {
    if (this.retryTimer !== null) this.cancel(this.retryTimer);
    const delay = reconnectDelay(this.attempt);
    this.attempt += 1;
    this.retryTimer = this.schedule(() => {
      this.retryTimer = null;
      if (!this.closed) this.connect();
    }, delay);
  }

  send(msg: OperatorMessage) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
    else this.pending.push(msg);
  }

  close() {
    this.closed = true;
    if (this.retryTimer !== null) {
      this.cancel(this.retryTimer);
      this.retryTimer = null;
    }
    this.setState("closed");
    this.ws?.close();
  }
}
