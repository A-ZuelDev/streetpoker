import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppProviders } from '../../app/providers';
import { activeRoomSnapshot, openRoomSnapshot } from '../../test/roomSnapshots';
import { LiveRoomSession } from './LiveRoomSession';

class FakeBrowserSocket extends EventTarget {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static readonly instances: FakeBrowserSocket[] = [];

  readyState = FakeBrowserSocket.CONNECTING;
  readonly sent: string[] = [];
  readonly closes: number[] = [];

  constructor(readonly url: string) {
    super();
    FakeBrowserSocket.instances.push(this);
  }

  send(value: string) {
    this.sent.push(value);
  }

  close(code = 1000) {
    this.closes.push(code);
    this.readyState = FakeBrowserSocket.CLOSED;
  }

  serverOpen() {
    this.readyState = FakeBrowserSocket.OPEN;
    this.dispatchEvent(new Event('open'));
  }

  serverMessage(message: unknown) {
    this.dispatchEvent(
      new MessageEvent('message', { data: JSON.stringify(message) }),
    );
  }

  serverClose() {
    this.readyState = FakeBrowserSocket.CLOSED;
    this.dispatchEvent(new CloseEvent('close'));
  }
}

function renderSession() {
  return render(
    <AppProviders>
      <LiveRoomSession />
    </AppProviders>,
  );
}

function formFor(name: string): HTMLFormElement {
  const heading = screen.getByRole('heading', { name });
  const form = heading.closest('form');
  if (form === null) {
    throw new Error('Expected a session form.');
  }
  return form;
}

function connectAndState(
  socket: FakeBrowserSocket,
  snapshot = openRoomSnapshot(),
) {
  socket.serverOpen();
  socket.serverMessage({
    type: 'connected',
    guest_id: 'guest_host',
    room_code: 'ABCDEFGH',
  });
  socket.serverMessage({ type: 'state', snapshot });
}

beforeEach(() => {
  FakeBrowserSocket.instances.length = 0;
  localStorage.clear();
  vi.stubGlobal('WebSocket', FakeBrowserSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('LiveRoomSession', () => {
  it('creates once, reuses the HTTP token for WS, and waits for state freshness', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => ({ room_code: 'ABCDEFGH' }),
    });
    vi.stubGlobal('fetch', fetchMock);
    renderSession();
    const form = formFor('Create a room');
    fireEvent.change(within(form).getByRole('textbox', { name: 'Nickname' }), {
      target: { value: 'Mara' },
    });
    fireEvent.change(within(form).getByRole('textbox', { name: 'Room name' }), {
      target: { value: 'Friday Night' },
    });

    fireEvent.submit(form);
    fireEvent.submit(form);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    await waitFor(() => expect(FakeBrowserSocket.instances).toHaveLength(1));
    const requestBody = JSON.parse(
      String((fetchMock.mock.calls[0]![1] as RequestInit).body),
    ) as { guest_token: string };
    const socket = FakeBrowserSocket.instances[0]!;
    expect(socket.url).toBe('ws://127.0.0.1:8000/ws/rooms/ABCDEFGH');
    expect(socket.url).not.toContain(requestBody.guest_token);

    socket.serverOpen();
    const connectFrame = JSON.parse(socket.sent[0]!) as Record<string, unknown>;
    expect(connectFrame).toEqual({
      type: 'connect',
      guest_token: requestBody.guest_token,
    });

    socket.serverMessage({
      type: 'connected',
      guest_id: 'guest_host',
      room_code: 'ABCDEFGH',
    });
    expect(
      await screen.findByText('Connected. Syncing the authoritative table…'),
    ).toBeInTheDocument();
    expect(screen.queryByText('Table live')).toBeNull();

    socket.serverMessage({ type: 'state', snapshot: openRoomSnapshot() });
    expect(await screen.findByText('Table live')).toBeInTheDocument();
    expect(screen.getByText('Friday Night')).toBeInTheDocument();
  });

  it('joins without HTTP, canonicalizes the route, and renders an unseated viewer', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    renderSession();
    const form = formFor('Join a room');
    fireEvent.change(within(form).getByRole('textbox', { name: 'Room code' }), {
      target: { value: ' abcdefgh ' },
    });
    fireEvent.change(within(form).getByRole('textbox', { name: 'Nickname' }), {
      target: { value: 'Mara' },
    });
    fireEvent.change(within(form).getByLabelText('Password if required'), {
      target: { value: 'private-password' },
    });
    fireEvent.submit(form);

    expect(fetchMock).not.toHaveBeenCalled();
    expect(FakeBrowserSocket.instances).toHaveLength(1);
    const socket = FakeBrowserSocket.instances[0]!;
    expect(socket.url).toBe('ws://127.0.0.1:8000/ws/rooms/ABCDEFGH');
    expect(socket.url).not.toContain('Mara');
    expect(socket.url).not.toContain('private-password');
    const unseated = openRoomSnapshot();
    unseated.room.members[1]!.status = 'in_room';
    unseated.room.members[1]!.stack = null;
    unseated.room.seats[1] = {
      seat_index: 1,
      guest_id: null,
      nickname: null,
      stack: null,
    };
    socket.serverOpen();
    socket.serverMessage({
      type: 'connected',
      guest_id: 'guest_alice',
      room_code: 'ABCDEFGH',
    });
    socket.serverMessage({ type: 'state', snapshot: unseated });

    expect(await screen.findByText('Not seated')).toBeInTheDocument();
    expect(screen.getByText('Watching the table')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Chat' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Start hand' })).toBeNull();
  });

  it('retains stale state, never auto-reconnects, then refreshes explicitly', async () => {
    renderSession();
    const form = formFor('Join a room');
    fireEvent.change(within(form).getByRole('textbox', { name: 'Room code' }), {
      target: { value: 'ABCDEFGH' },
    });
    fireEvent.change(within(form).getByRole('textbox', { name: 'Nickname' }), {
      target: { value: 'Mara' },
    });
    fireEvent.submit(form);
    const first = FakeBrowserSocket.instances[0]!;
    connectAndState(first, openRoomSnapshot('Stale room'));
    expect(await screen.findByText('Table live')).toBeInTheDocument();

    first.serverClose();
    expect(
      await screen.findByText('Disconnected · stale table'),
    ).toBeInTheDocument();
    expect(screen.getByText('Stale room')).toBeInTheDocument();
    expect(FakeBrowserSocket.instances).toHaveLength(1);

    fireEvent.click(screen.getByRole('button', { name: 'Reconnect' }));
    expect(FakeBrowserSocket.instances).toHaveLength(2);
    const second = FakeBrowserSocket.instances[1]!;
    second.serverOpen();
    const originalFrame = JSON.parse(first.sent[0]!) as { guest_token: string };
    expect(JSON.parse(second.sent[0]!)).toEqual({
      type: 'connect',
      guest_token: originalFrame.guest_token,
    });
    second.serverMessage({
      type: 'connected',
      guest_id: 'guest_host',
      room_code: 'ABCDEFGH',
    });
    expect(
      await screen.findByText('Connected. Waiting for a fresh table update.'),
    ).toBeInTheDocument();
    expect(screen.getByText('Stale room')).toBeInTheDocument();
    expect(screen.queryByText('Table live')).toBeNull();

    second.serverMessage({
      type: 'state',
      snapshot: activeRoomSnapshot('Fresh room'),
    });
    expect(await screen.findByText('Table live')).toBeInTheDocument();
    expect(screen.getByText('Fresh room')).toBeInTheDocument();
    expect(screen.queryByText('Stale room')).toBeNull();
  });

  it('requires fresh state before restoring the turn countdown after reconnect', async () => {
    renderSession();
    const form = formFor('Join a room');
    fireEvent.change(within(form).getByRole('textbox', { name: 'Room code' }), {
      target: { value: 'ABCDEFGH' },
    });
    fireEvent.change(within(form).getByRole('textbox', { name: 'Nickname' }), {
      target: { value: 'Mara' },
    });
    fireEvent.submit(form);

    const snapshot = activeRoomSnapshot();
    snapshot.active_hand!.action_deadline_unix_ms = Date.now() + 30_000;
    const first = FakeBrowserSocket.instances[0]!;
    connectAndState(first, snapshot);
    expect(await screen.findByRole('timer')).toBeInTheDocument();

    first.serverClose();
    expect(
      await screen.findByText('Waiting for fresh state'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('timer')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Fold' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Reconnect' }));
    const second = FakeBrowserSocket.instances[1]!;
    second.serverOpen();
    second.serverMessage({
      type: 'connected',
      guest_id: 'guest_host',
      room_code: 'ABCDEFGH',
    });
    expect(
      await screen.findByText('Connected. Waiting for a fresh table update.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('timer')).toBeNull();
    expect(screen.getByText('Waiting for fresh state')).toBeInTheDocument();

    second.serverMessage({ type: 'state', snapshot });
    expect(await screen.findByRole('timer')).toBeInTheDocument();
    expect(screen.queryByText('Waiting for fresh state')).toBeNull();
    expect(second.sent).toHaveLength(1);
  });

  it('offers reconnect after membership confirmation even before the first state', async () => {
    renderSession();
    const form = formFor('Join a room');
    fireEvent.change(within(form).getByRole('textbox', { name: 'Room code' }), {
      target: { value: 'ABCDEFGH' },
    });
    fireEvent.change(within(form).getByRole('textbox', { name: 'Nickname' }), {
      target: { value: 'Mara' },
    });
    fireEvent.submit(form);
    const first = FakeBrowserSocket.instances[0]!;
    first.serverOpen();
    first.serverMessage({
      type: 'connected',
      guest_id: 'guest_host',
      room_code: 'ABCDEFGH',
    });
    first.serverClose();

    expect(
      await screen.findByText(
        'Disconnected before the table finished syncing.',
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Reconnect' }));
    expect(FakeBrowserSocket.instances).toHaveLength(2);
  });

  it('surfaces a safe password error without rendering the secret', async () => {
    renderSession();
    const form = formFor('Join a room');
    fireEvent.change(within(form).getByRole('textbox', { name: 'Room code' }), {
      target: { value: 'ABCDEFGH' },
    });
    fireEvent.change(within(form).getByRole('textbox', { name: 'Nickname' }), {
      target: { value: 'Mara' },
    });
    fireEvent.change(within(form).getByLabelText('Password if required'), {
      target: { value: 'private-password' },
    });
    fireEvent.submit(form);
    const socket = FakeBrowserSocket.instances[0]!;
    socket.serverOpen();
    socket.serverMessage({
      type: 'connection_error',
      code: 'wrong_room_password',
      message: 'private server detail',
    });

    expect(
      await screen.findByText('The room password was not accepted.'),
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('private-password');
    expect(document.body.textContent).not.toContain('private server detail');
  });

  it('clears a wrong-password error when a corrected join succeeds', async () => {
    renderSession();
    let form = formFor('Join a room');
    fireEvent.change(within(form).getByRole('textbox', { name: 'Room code' }), {
      target: { value: 'abcdefgh' },
    });
    fireEvent.change(within(form).getByRole('textbox', { name: 'Nickname' }), {
      target: { value: 'Mara' },
    });
    fireEvent.change(within(form).getByLabelText('Password if required'), {
      target: { value: 'wrong-secret' },
    });
    fireEvent.submit(form);
    const rejected = FakeBrowserSocket.instances[0]!;
    rejected.serverOpen();
    rejected.serverMessage({
      type: 'connection_error',
      code: 'wrong_room_password',
      message: 'private rejected detail',
    });
    expect(
      await screen.findByText('The room password was not accepted.'),
    ).toBeInTheDocument();

    form = formFor('Join a room');
    const password = form.elements.namedItem('join-password');
    if (!(password instanceof HTMLInputElement)) {
      throw new Error('Expected the join password input.');
    }
    fireEvent.change(password, { target: { value: 'correct-secret' } });
    fireEvent.submit(form);
    const accepted = FakeBrowserSocket.instances[1]!;
    connectAndState(accepted);

    expect(await screen.findByText('Table live')).toBeInTheDocument();
    expect(
      screen.queryByText('The room password was not accepted.'),
    ).toBeNull();
    expect(document.body.textContent).not.toContain('wrong-secret');
    expect(document.body.textContent).not.toContain('correct-secret');
    expect(document.body.textContent).not.toContain('private rejected detail');
  });

  it('sends one live action, retains pending through ack, and maps errors safely', async () => {
    renderSession();
    const form = formFor('Join a room');
    fireEvent.change(within(form).getByRole('textbox', { name: 'Room code' }), {
      target: { value: 'ABCDEFGH' },
    });
    fireEvent.change(within(form).getByRole('textbox', { name: 'Nickname' }), {
      target: { value: 'Mara' },
    });
    fireEvent.submit(form);
    const socket = FakeBrowserSocket.instances[0]!;
    connectAndState(socket, activeRoomSnapshot());
    const call = await screen.findByRole('button', { name: 'Call 50' });
    const potBefore = screen.getByLabelText('Pot 250').textContent;

    fireEvent.click(call);
    fireEvent.click(call);

    expect(socket.sent).toHaveLength(2);
    const command = JSON.parse(socket.sent[1]!) as Record<string, unknown>;
    expect(command).toEqual({
      type: 'call',
      command_id: expect.any(String),
      hand_number: 1,
      expected_action_sequence: 2,
    });
    expect(String(command.command_id)).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );
    expect(screen.getByLabelText('Pot 250').textContent).toBe(potBefore);
    expect(call).toBeDisabled();

    socket.serverMessage({
      type: 'command_ack',
      command_id: command.command_id,
    });
    expect(call).toBeDisabled();
    socket.serverMessage({
      type: 'command_error',
      command_id: command.command_id,
      code: 'illegal_call',
      message: 'private player path',
    });
    expect(
      await screen.findByText('Calling is not available now.'),
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('private player path');
    expect(screen.getByLabelText('Pot 250').textContent).toBe(potBefore);
  });
});
