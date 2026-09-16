import { expect, test, type Page, type WebSocketRoute } from '@playwright/test';

import {
  activeRoomSnapshot,
  openRoomSnapshot,
} from '../src/test/roomSnapshots';

interface SocketHarness {
  sockets: WebSocketRoute[];
  frames: Array<Record<string, unknown>>;
}

async function mockSockets(page: Page): Promise<SocketHarness> {
  const sockets: WebSocketRoute[] = [];
  const frames: Array<Record<string, unknown>> = [];
  await page.routeWebSocket(/\/ws\/rooms\//, (socket) => {
    sockets.push(socket);
    socket.onMessage((message) => {
      if (typeof message === 'string') {
        frames.push(JSON.parse(message) as Record<string, unknown>);
      }
    });
  });
  return { sockets, frames };
}

async function fillJoin(page: Page, password?: string) {
  const form = page
    .getByRole('heading', { name: 'Join a room' })
    .locator('xpath=ancestor::form');
  await form.getByRole('textbox', { name: 'Room code' }).fill(' abcdefgh ');
  await form.getByRole('textbox', { name: 'Nickname' }).fill('Mara');
  if (password !== undefined) {
    await form.getByLabel('Password if required').fill(password);
  }
  await form.getByRole('button', { name: 'Join room' }).click();
}

test('creates once and waits for authoritative state before becoming live', async ({
  page,
}) => {
  const harness = await mockSockets(page);
  let postCount = 0;
  let createBody: Record<string, unknown> | null = null;
  await page.route('http://127.0.0.1:8000/rooms', async (route) => {
    postCount += 1;
    createBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({ room_code: 'ABCDEFGH' }),
    });
  });
  await page.goto('/');
  const form = page
    .getByRole('heading', { name: 'Create a room' })
    .locator('xpath=ancestor::form');
  await form.getByRole('textbox', { name: 'Nickname' }).fill('Mara');
  await form.getByRole('textbox', { name: 'Room name' }).fill('Friday Night');
  await form.evaluate((element: HTMLFormElement) => {
    element.requestSubmit();
    element.requestSubmit();
  });

  await expect.poll(() => postCount).toBe(1);
  await expect.poll(() => harness.sockets.length).toBe(1);
  await expect.poll(() => harness.frames.length).toBe(1);
  const socket = harness.sockets[0]!;
  const frame = harness.frames[0]!;
  expect(socket.url()).toBe('ws://127.0.0.1:8000/ws/rooms/ABCDEFGH');
  expect(socket.url()).not.toContain(String(createBody?.guest_token));
  expect(frame.type).toBe('connect');
  expect(Object.keys(frame).sort()).toEqual(['guest_token', 'type']);
  expect(frame.guest_token === createBody?.guest_token).toBe(true);

  socket.send(
    JSON.stringify({
      type: 'connected',
      guest_id: 'guest_host',
      room_code: 'ABCDEFGH',
    }),
  );
  await expect(
    page.getByText('Connected. Syncing the authoritative table…'),
  ).toBeVisible();
  await expect(page.getByText('Table live')).toBeHidden();

  socket.send(JSON.stringify({ type: 'state', snapshot: openRoomSnapshot() }));
  await expect(page.getByText('Table live')).toBeVisible();
  await expect(page.getByText('Friday Night')).toBeVisible();
});

test('joins over a credential-free canonical socket URL as an unseated viewer', async ({
  page,
}) => {
  const harness = await mockSockets(page);
  let roomPosts = 0;
  await page.route('http://127.0.0.1:8000/rooms', async (route) => {
    roomPosts += 1;
    await route.abort();
  });
  await page.goto('/');
  await fillJoin(page, 'private-password');

  await expect.poll(() => harness.frames.length).toBe(1);
  expect(roomPosts).toBe(0);
  expect(harness.sockets[0]!.url()).toBe(
    'ws://127.0.0.1:8000/ws/rooms/ABCDEFGH',
  );
  expect(harness.sockets[0]!.url()).not.toContain('Mara');
  expect(harness.sockets[0]!.url()).not.toContain('private-password');
  expect(harness.frames[0]).toMatchObject({
    type: 'connect',
    nickname: 'Mara',
    password: 'private-password',
  });

  harness.sockets[0]!.send(
    JSON.stringify({
      type: 'connected',
      guest_id: 'guest_alice',
      room_code: 'ABCDEFGH',
    }),
  );
  const unseatedSnapshot = openRoomSnapshot();
  unseatedSnapshot.room.members[1]!.status = 'in_room';
  unseatedSnapshot.room.members[1]!.stack = null;
  unseatedSnapshot.room.seats[1] = {
    seat_index: 1,
    guest_id: null,
    nickname: null,
    stack: null,
  };
  harness.sockets[0]!.send(
    JSON.stringify({ type: 'state', snapshot: unseatedSnapshot }),
  );
  await expect(page.getByText('Not seated')).toBeVisible();
  await expect(page.getByText('Watching the table')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Chat' })).toBeHidden();
  await expect(page.getByRole('button', { name: 'Start hand' })).toBeHidden();
});

test('keeps stale state until an explicit reconnect receives fresh state', async ({
  page,
}) => {
  const harness = await mockSockets(page);
  await page.goto('/');
  await fillJoin(page);
  await expect.poll(() => harness.sockets.length).toBe(1);
  const first = harness.sockets[0]!;
  first.send(
    JSON.stringify({
      type: 'connected',
      guest_id: 'guest_host',
      room_code: 'ABCDEFGH',
    }),
  );
  first.send(
    JSON.stringify({ type: 'state', snapshot: openRoomSnapshot('Stale room') }),
  );
  await expect(page.getByText('Table live')).toBeVisible();

  await first.close({ code: 1012 });
  await expect(page.getByText('Disconnected · stale table')).toBeVisible();
  await expect(page.getByText('Stale room')).toBeVisible();
  await page.waitForTimeout(150);
  expect(harness.sockets).toHaveLength(1);

  await page.getByRole('button', { name: 'Reconnect' }).click();
  await expect.poll(() => harness.sockets.length).toBe(2);
  await expect.poll(() => harness.frames.length).toBe(2);
  const second = harness.sockets[1]!;
  expect(second.url()).toBe(first.url());
  expect(
    harness.frames[1]!.guest_token === harness.frames[0]!.guest_token,
  ).toBe(true);
  expect(Object.keys(harness.frames[1]!).sort()).toEqual([
    'guest_token',
    'type',
  ]);

  second.send(
    JSON.stringify({
      type: 'connected',
      guest_id: 'guest_host',
      room_code: 'ABCDEFGH',
    }),
  );
  await expect(
    page.getByText('Connected. Waiting for a fresh table update.'),
  ).toBeVisible();
  await expect(page.getByText('Table live')).toBeHidden();
  await expect(page.getByText('Stale room')).toBeVisible();

  second.send(
    JSON.stringify({
      type: 'state',
      snapshot: activeRoomSnapshot('Fresh room'),
    }),
  );
  await expect(page.getByText('Table live')).toBeVisible();
  await expect(page.getByText('Fresh room')).toBeVisible();
  await expect(page.getByText('Stale room')).toBeHidden();
});

test('corrects a wrong password and clears the stale entry error', async ({
  page,
}) => {
  const harness = await mockSockets(page);
  await page.goto('/');
  await fillJoin(page, 'private-password');
  await expect.poll(() => harness.sockets.length).toBe(1);

  harness.sockets[0]!.send(
    JSON.stringify({
      type: 'connection_error',
      code: 'wrong_room_password',
      message: 'private server detail',
    }),
  );

  await expect(
    page.getByText('The room password was not accepted.'),
  ).toBeVisible();
  await expect(page.locator('body')).not.toContainText('private server detail');
  await expect(page.locator('body')).not.toContainText('private-password');

  await fillJoin(page, 'correct-password');
  await expect.poll(() => harness.sockets.length).toBe(2);
  const accepted = harness.sockets[1]!;
  accepted.send(
    JSON.stringify({
      type: 'connected',
      guest_id: 'guest_host',
      room_code: 'ABCDEFGH',
    }),
  );
  accepted.send(
    JSON.stringify({ type: 'state', snapshot: openRoomSnapshot() }),
  );
  await expect(page.getByText('Table live')).toBeVisible();
  await expect(
    page.getByText('The room password was not accepted.'),
  ).toBeHidden();
  await expect(page.locator('body')).not.toContainText('correct-password');
});
