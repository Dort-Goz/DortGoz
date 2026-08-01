import type { Event, OperatorMessage } from "../types/events";

/** Otomatik yeniden bağlanan, tipli WS istemcisi. */
export class DortgozSocket {
  private ws: WebSocket | null = null;
  private url: string;
  private handler: (e: Event) => void;
  private closed = false;

  constructor(handler: (e: Event) => void) {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.url = `${proto}://${location.host}/ws`;
    this.handler = handler;
    this.connect();
  }

  private connect() {
    this.ws = new WebSocket(this.url);
    this.ws.onmessage = (msg) => this.handler(JSON.parse(msg.data) as Event);
    this.ws.onclose = () => {
      if (!this.closed) setTimeout(() => this.connect(), 1500);
    };
  }

  send(msg: OperatorMessage) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  }

  close() {
    this.closed = true;
    this.ws?.close();
  }
}
