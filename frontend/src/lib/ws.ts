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

/** Operatöre gösterilen bağlantı durumu. */
export type ConnectionState = "connecting" | "open" | "reconnecting" | "closed";

export const RECONNECT_BASE_MS = 1500;
export const RECONNECT_MAX_MS = 30000;

/** Üstel geri çekilme: 1.5 s, 3 s, 6 s … üst sınırda sabitlenir.
 *  Sabit gecikme, backend yavaş istemciyi düşürdüğünde sunucuyu
 *  saniyede bir yeniden bağlanma denemesiyle dövüyordu. */
export function reconnectDelay(attempt: number): number {
  const step = RECONNECT_BASE_MS * 2 ** Math.max(0, attempt);
  return Math.min(step, RECONNECT_MAX_MS);
}

export interface DortgozSocketOptions {
  /** Durum değişince çağrılır — üst çubuktaki bağlantı rozetini besler. */
  onState?: (state: ConnectionState) => void;
  /** Testler gerçek soket, adres ve zamanlayıcı yerine sahte verir. */
  url?: string;
  createSocket?: (url: string) => WebSocket;
  schedule?: (fn: () => void, ms: number) => number;
  cancel?: (handle: number) => void;
}

/** Otomatik yeniden bağlanan, tipli WS istemcisi. */
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
  // Henüz soket yok; ilk setState çağrısı "connecting"i operatöre bildirsin.
  private state: ConnectionState = "closed";
  private attempt = 0;
  private retryTimer: number | null = null;
  // Bağlantı kurulmadan gönderilenler SESSİZCE düşüyordu (sayfa açılır açılmaz
  // "demo"ya basmak 4 start_run'ı yutuyordu) → açılana dek kuyrukta bekletilir.
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
    // Kapanan eski soketler geç olay yollayabiliyor; yalnız güncel soket sayılır.
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
      if (this.closed) return;
      this.connect();
    }, delay);
  }

  send(msg: OperatorMessage) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
    else this.pending.push(msg);
  }

  close() {
    this.closed = true;
    // Zamanlayıcı sızıyordu: kapatılan konsol yeniden bağlanıp olay akıtıyordu.
    if (this.retryTimer !== null) {
      this.cancel(this.retryTimer);
      this.retryTimer = null;
    }
    this.setState("closed");
    this.ws?.close();
  }
}
