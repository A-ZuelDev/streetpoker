import type { BackendUrls } from '../api/backendUrls';
import {
  connectFrameSchema,
  serverMessageSchema,
  type ConnectFrame,
  type ServerMessage,
} from './messages';
import { protocolSessionError, SessionError } from './sessionError';

export interface RoomSocketEvents {
  onMessage(message: ServerMessage): void;
  onFailure(error: SessionError): void;
  onClose(): void;
}

export type WebSocketFactory = (url: string) => WebSocket;

export interface RoomSocketOptions {
  urls: BackendUrls;
  roomCode: string;
  connectFrame: ConnectFrame;
  events: RoomSocketEvents;
  webSocketFactory?: WebSocketFactory;
}

export type RoomSocketFactory = (options: RoomSocketOptions) => RoomSocket;

export class RoomSocket {
  readonly url: string;
  private readonly socket: WebSocket;
  private readonly events: RoomSocketEvents;
  private connectFrame: ConnectFrame | null;
  private closed = false;
  private failureReported = false;

  constructor(options: RoomSocketOptions) {
    this.url = options.urls.roomWebSocketUrl(options.roomCode);
    this.events = options.events;
    this.connectFrame = connectFrameSchema.parse(options.connectFrame);
    const factory =
      options.webSocketFactory ?? ((url: string) => new WebSocket(url));
    this.socket = factory(this.url);
    this.socket.addEventListener('open', this.handleOpen);
    this.socket.addEventListener('message', this.handleMessage);
    this.socket.addEventListener('error', this.handleError);
    this.socket.addEventListener('close', this.handleClose);
  }

  close(): void {
    if (this.closed) {
      return;
    }
    this.closed = true;
    this.connectFrame = null;
    this.removeListeners();
    if (
      this.socket.readyState === WebSocket.CONNECTING ||
      this.socket.readyState === WebSocket.OPEN
    ) {
      this.socket.close(1000, 'client close');
    }
  }

  private readonly handleOpen = () => {
    if (this.closed || this.connectFrame === null) {
      return;
    }
    const serialized = JSON.stringify(this.connectFrame);
    this.connectFrame = null;
    this.socket.send(serialized);
  };

  private readonly handleMessage = (event: MessageEvent<unknown>) => {
    if (this.closed) {
      return;
    }
    if (typeof event.data !== 'string') {
      this.failProtocol();
      return;
    }

    let value: unknown;
    try {
      value = JSON.parse(event.data) as unknown;
    } catch {
      this.failProtocol();
      return;
    }
    const parsed = serverMessageSchema.safeParse(value);
    if (!parsed.success) {
      this.failProtocol();
      return;
    }
    this.events.onMessage(parsed.data);
  };

  private readonly handleError = () => {
    if (!this.closed && !this.failureReported) {
      this.failureReported = true;
      this.events.onFailure(
        new SessionError({
          source: 'network',
          code: 'socket_error',
          message: 'The room connection was interrupted.',
        }),
      );
    }
  };

  private readonly handleClose = () => {
    if (this.closed) {
      return;
    }
    this.closed = true;
    this.connectFrame = null;
    this.removeListeners();
    this.events.onClose();
  };

  private failProtocol(): void {
    if (!this.failureReported) {
      this.failureReported = true;
      this.events.onFailure(protocolSessionError());
    }
    this.closed = true;
    this.connectFrame = null;
    this.removeListeners();
    if (
      this.socket.readyState === WebSocket.CONNECTING ||
      this.socket.readyState === WebSocket.OPEN
    ) {
      this.socket.close(1002, 'invalid server message');
    }
  }

  private removeListeners(): void {
    this.socket.removeEventListener('open', this.handleOpen);
    this.socket.removeEventListener('message', this.handleMessage);
    this.socket.removeEventListener('error', this.handleError);
    this.socket.removeEventListener('close', this.handleClose);
  }
}
