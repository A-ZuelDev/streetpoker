import { expect, test, type Page, type WebSocketRoute } from '@playwright/test';

import {
  activeRoomSnapshot,
  completedRoomSnapshot,
  openRoomSnapshot,
} from '../src/test/roomSnapshots';
import type { RoomView } from '../src/realtime/messages';

interface RoomHarness {
  socket: WebSocketRoute;
  frames: Array<Record<string, unknown>>;
}

function unseatedGuestSnapshot(approvalRequired = true): RoomView {
  const snapshot = openRoomSnapshot();
  snapshot.room.settings.seating_approval_required = approvalRequired;
  snapshot.room.members[1]!.status = 'in_room';
  snapshot.room.members[1]!.stack = null;
  snapshot.room.seats[1] = {
    seat_index: 1,
    guest_id: null,
    nickname: null,
    stack: null,
  };
  return snapshot;
}

function requestedSnapshot(): RoomView {
  const snapshot = openRoomSnapshot();
  snapshot.room.members.push({
    guest_id: 'guest_ember_private',
    nickname: 'Ember',
    status: 'in_room',
    is_host: false,
    stack: null,
  });
  snapshot.room.seat_requests = [
    {
      guest_id: 'guest_ember_private',
      nickname: 'Ember',
      seat_index: 2,
    },
  ];
  return snapshot;
}

async function openRoom(
  page: Page,
  snapshot: RoomView,
  guestId: string,
): Promise<RoomHarness> {
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
  await page.goto('/');
  const form = page
    .getByRole('heading', { name: 'Join a room' })
    .locator('xpath=ancestor::form');
  await form.getByRole('textbox', { name: 'Room code' }).fill('ABCDEFGH');
  await form.getByRole('textbox', { name: 'Nickname' }).fill('Player');
  await form.getByRole('button', { name: 'Join room' }).click();
  await expect.poll(() => sockets.length).toBe(1);
  const socket = sockets[0]!;
  socket.send(
    JSON.stringify({
      type: 'connected',
      guest_id: guestId,
      room_code: 'ABCDEFGH',
    }),
  );
  socket.send(JSON.stringify({ type: 'state', snapshot }));
  await expect(page.getByText('Table live')).toBeVisible();
  await expect.poll(() => frames.length).toBe(1);
  return { socket, frames };
}

function commands(harness: RoomHarness) {
  return harness.frames.filter((frame) => frame.type !== 'connect');
}

async function acknowledgeAndState(harness: RoomHarness, snapshot: RoomView) {
  const command = commands(harness).at(-1)!;
  harness.socket.send(
    JSON.stringify({ type: 'command_ack', command_id: command.command_id }),
  );
  harness.socket.send(JSON.stringify({ type: 'state', snapshot }));
}

test('guest requests one specific seat without optimistic seating', async ({
  page,
}) => {
  const snapshot = unseatedGuestSnapshot();
  const harness = await openRoom(page, snapshot, 'guest_alice');
  await page.getByRole('button', { name: 'Request seat 1' }).click();
  await expect.poll(() => commands(harness).length).toBe(1);
  expect(commands(harness)[0]).toEqual({
    type: 'request_seat',
    command_id: expect.any(String),
    seat_index: 0,
  });
  await expect(
    page.getByRole('button', { name: 'Request seat 1' }),
  ).toBeVisible();
});

test('approval-disabled take seat uses request_seat', async ({ page }) => {
  const harness = await openRoom(
    page,
    unseatedGuestSnapshot(false),
    'guest_alice',
  );
  await page.getByRole('button', { name: 'Take seat 1' }).click();
  await expect.poll(() => commands(harness).length).toBe(1);
  expect(commands(harness)[0]).toMatchObject({
    type: 'request_seat',
    seat_index: 0,
  });
});

test('host approves with the internal request target', async ({ page }) => {
  const harness = await openRoom(page, requestedSnapshot(), 'guest_host');
  await page.getByRole('button', { name: 'Approve' }).click();
  await expect.poll(() => commands(harness).length).toBe(1);
  expect(commands(harness)[0]).toMatchObject({
    type: 'approve_seat',
    target_guest_id: 'guest_ember_private',
  });
  await expect(page.locator('body')).not.toContainText('guest_ember_private');
});

test('host rejects without optimistic request removal', async ({ page }) => {
  const harness = await openRoom(page, requestedSnapshot(), 'guest_host');
  await page.getByRole('button', { name: 'Reject' }).click();
  await expect.poll(() => commands(harness).length).toBe(1);
  expect(commands(harness)[0]).toMatchObject({ type: 'reject_seat' });
  await expect(page.getByText('Ember').first()).toBeVisible();
});

test('seated guest stands without optimistic seat clearing', async ({
  page,
}) => {
  const harness = await openRoom(page, openRoomSnapshot(), 'guest_alice');
  await page.getByRole('button', { name: 'Stand' }).click();
  await expect.poll(() => commands(harness).length).toBe(1);
  expect(commands(harness)[0]).toMatchObject({ type: 'stand' });
  await expect(page.getByLabel('Seat 2: Alice')).toBeVisible();
});

test('leave ACK returns to entry with a local terminal notice', async ({
  page,
}) => {
  const harness = await openRoom(page, openRoomSnapshot(), 'guest_alice');
  await page.getByRole('button', { name: 'Leave room' }).click();
  await page.getByRole('button', { name: 'Confirm leave room' }).click();
  await expect.poll(() => commands(harness).length).toBe(1);
  harness.socket.send(
    JSON.stringify({
      type: 'command_ack',
      command_id: commands(harness)[0]!.command_id,
    }),
  );
  await expect(page.getByText(/You left the room\./)).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'Join a room' }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Reconnect' })).toBeHidden();
});

test('host kicks a target while the target classifies its exact close', async ({
  page,
}) => {
  const host = await openRoom(page, openRoomSnapshot(), 'guest_host');
  await page.getByRole('button', { name: 'Kick Alice' }).click();
  await page.getByRole('button', { name: 'Confirm kick Alice' }).click();
  await expect.poll(() => commands(host).length).toBe(1);
  expect(commands(host)[0]).toMatchObject({
    type: 'kick',
    target_guest_id: 'guest_alice',
  });

  const targetPage = await page.context().newPage();
  const target = await openRoom(targetPage, openRoomSnapshot(), 'guest_alice');
  await target.socket.close({ code: 1008, reason: 'removed from room' });
  await expect(
    targetPage.getByText(/You were removed from the room\./),
  ).toBeVisible();
  await targetPage.close();
});

test('host starts with exact authoritative next_hand_number', async ({
  page,
}) => {
  const snapshot = openRoomSnapshot();
  snapshot.next_hand_number = 41;
  const harness = await openRoom(page, snapshot, 'guest_host');
  await page.getByRole('button', { name: 'Start hand' }).click();
  await expect.poll(() => commands(harness).length).toBe(1);
  expect(commands(harness)[0]).toEqual({
    type: 'start_hand',
    command_id: expect.any(String),
    hand_number: 41,
  });
});

test('next hand uses the new authoritative number after settlement', async ({
  page,
}) => {
  const first = openRoomSnapshot();
  const harness = await openRoom(page, first, 'guest_host');
  await page.getByRole('button', { name: 'Start hand' }).click();
  await acknowledgeAndState(harness, activeRoomSnapshot());
  harness.socket.send(
    JSON.stringify({ type: 'state', snapshot: completedRoomSnapshot() }),
  );
  await page.getByRole('button', { name: 'Start hand' }).click();
  await expect.poll(() => commands(harness).length).toBe(2);
  expect(commands(harness)[1]).toMatchObject({
    type: 'start_hand',
    hand_number: 2,
  });
});

test('host sends an exact public settings patch', async ({ page }) => {
  const harness = await openRoom(page, openRoomSnapshot(), 'guest_host');
  await page
    .getByRole('button', { name: 'Expand Room settings section' })
    .click();
  await page.getByRole('textbox', { name: 'Room name' }).fill('Saturday Night');
  await page.getByRole('button', { name: 'Save settings' }).click();
  await expect.poll(() => commands(harness).length).toBe(1);
  expect(commands(harness)[0]).toEqual({
    type: 'update_settings',
    command_id: expect.any(String),
    room_name: 'Saturday Night',
  });
  await expect(page.getByLabel('Room summary')).toContainText('Friday Night');
});

test('password set and remove stay out of rendered and stored room state', async ({
  page,
}) => {
  const snapshot = openRoomSnapshot();
  const harness = await openRoom(page, snapshot, 'guest_host');
  await page
    .getByRole('button', { name: 'Expand Room settings section' })
    .click();
  await page.getByRole('combobox', { name: 'Password' }).selectOption('set');
  await page.getByLabel('New password').fill('private-password');
  await page.getByRole('button', { name: 'Save settings' }).click();
  await expect.poll(() => commands(harness).length).toBe(1);
  expect(commands(harness)[0]).toMatchObject({
    type: 'update_settings',
    password: 'private-password',
  });
  await expect(page.locator('body')).not.toContainText('private-password');

  const protectedState = openRoomSnapshot();
  protectedState.room.settings.password_protected = true;
  await acknowledgeAndState(harness, protectedState);
  await page.getByRole('combobox', { name: 'Password' }).selectOption('remove');
  await page.getByRole('button', { name: 'Save settings' }).click();
  await expect.poll(() => commands(harness).length).toBe(2);
  expect(commands(harness)[1]).toMatchObject({ password: null });
});

test('authoritative closed state exits before socket close', async ({
  page,
}) => {
  const harness = await openRoom(page, openRoomSnapshot(), 'guest_host');
  await page.getByRole('button', { name: 'Close room' }).click();
  await page.getByRole('button', { name: 'Confirm close room' }).click();
  await expect.poll(() => commands(harness).length).toBe(1);
  harness.socket.send(
    JSON.stringify({
      type: 'command_ack',
      command_id: commands(harness)[0]!.command_id,
    }),
  );
  const closed = openRoomSnapshot();
  closed.room.status = 'closed';
  harness.socket.send(JSON.stringify({ type: 'state', snapshot: closed }));
  await expect(page.getByText(/This room was closed\./)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Reconnect' })).toBeHidden();
});

test('non-host sees no host controls or other guests requests', async ({
  page,
}) => {
  const snapshot = requestedSnapshot();
  const harness = await openRoom(page, snapshot, 'guest_alice');
  await expect(
    page.getByRole('heading', { name: 'Host controls' }),
  ).toBeHidden();
  await expect(
    page.getByRole('heading', { name: 'Room settings' }),
  ).toBeHidden();
  await expect(page.getByRole('button', { name: 'Approve' })).toBeHidden();
  expect(commands(harness)).toEqual([]);
});

test('rapid duplicate room activation sends once', async ({ page }) => {
  const harness = await openRoom(page, unseatedGuestSnapshot(), 'guest_alice');
  await page
    .getByRole('button', { name: 'Request seat 1' })
    .evaluate((button) => {
      (button as HTMLButtonElement).click();
      (button as HTMLButtonElement).click();
    });
  await expect.poll(() => commands(harness).length).toBe(1);
});

test('matching room error uses fixed local feedback and unlocks controls', async ({
  page,
}) => {
  const harness = await openRoom(page, unseatedGuestSnapshot(), 'guest_alice');
  await page.getByRole('button', { name: 'Request seat 1' }).click();
  await expect.poll(() => commands(harness).length).toBe(1);
  harness.socket.send(
    JSON.stringify({
      type: 'command_error',
      command_id: commands(harness)[0]!.command_id,
      code: 'seat_occupied',
      message: 'private guest detail',
    }),
  );
  await expect(
    page.getByText('That seat is no longer available.'),
  ).toBeVisible();
  await expect(page.locator('body')).not.toContainText('private guest detail');
  await expect(
    page.getByRole('button', { name: 'Request seat 1' }),
  ).toBeEnabled();
});

test('room state broadcast updates seats and request presentation', async ({
  page,
}) => {
  const initial = unseatedGuestSnapshot();
  const harness = await openRoom(page, initial, 'guest_alice');
  const requested = structuredClone(initial);
  requested.room.seat_requests = [
    { guest_id: 'guest_alice', nickname: 'Alice', seat_index: 0 },
  ];
  harness.socket.send(JSON.stringify({ type: 'state', snapshot: requested }));
  await expect(page.getByText('Waiting for host')).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Seat 1 requested' }),
  ).toBeDisabled();
});

test('unknown close remains disconnected and explicitly reconnectable', async ({
  page,
}) => {
  const harness = await openRoom(page, openRoomSnapshot(), 'guest_host');
  await harness.socket.close({ code: 1008, reason: 'removed from room later' });
  await expect(page.getByText('Disconnected · stale table')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Reconnect' })).toBeVisible();
});

test('connecting and syncing stale snapshots expose no enabled room commands', async ({
  page,
}) => {
  const harness = await openRoom(page, unseatedGuestSnapshot(), 'guest_alice');
  await harness.socket.close({ code: 1012 });
  await page.getByRole('button', { name: 'Reconnect' }).click();
  await expect(
    page.getByRole('button', { name: 'Request seat 1' }),
  ).toBeDisabled();
  await expect(
    page.getByText(/Connecting\.|Connected\. Waiting/),
  ).toBeVisible();
});

test('poker pending blocks otherwise legal room controls', async ({ page }) => {
  const snapshot = activeRoomSnapshot();
  snapshot.room.members.push({
    guest_id: 'guest_observer_private',
    nickname: 'Observer',
    status: 'in_room',
    is_host: false,
    stack: null,
  });
  const harness = await openRoom(page, snapshot, 'guest_host');
  await page.getByRole('button', { name: 'Call 50' }).click();
  await expect.poll(() => commands(harness).length).toBe(1);
  await expect(
    page.getByRole('button', { name: 'Kick Observer' }),
  ).toBeDisabled();
});

test('room pending blocks poker controls', async ({ page }) => {
  const snapshot = activeRoomSnapshot();
  snapshot.room.members.push({
    guest_id: 'guest_observer_private',
    nickname: 'Observer',
    status: 'in_room',
    is_host: false,
    stack: null,
  });
  snapshot.room.seat_requests = [
    { guest_id: 'guest_observer_private', nickname: 'Observer', seat_index: 4 },
  ];
  const harness = await openRoom(page, snapshot, 'guest_host');
  await page.getByRole('button', { name: 'Reject' }).click();
  await expect.poll(() => commands(harness).length).toBe(1);
  await expect(page.getByRole('button', { name: 'Fold' })).toBeDisabled();
});

test('active hand keeps safe settings and reject available while locking mutations', async ({
  page,
}) => {
  const snapshot = activeRoomSnapshot();
  snapshot.room.members.push({
    guest_id: 'guest_observer_private',
    nickname: 'Observer',
    status: 'in_room',
    is_host: false,
    stack: null,
  });
  snapshot.room.seat_requests = [
    { guest_id: 'guest_observer_private', nickname: 'Observer', seat_index: 4 },
  ];
  await openRoom(page, snapshot, 'guest_host');
  await expect(page.getByRole('button', { name: 'Approve' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Reject' })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Close room' })).toBeDisabled();
  await page
    .getByRole('button', { name: 'Expand Room settings section' })
    .click();
  await expect(page.getByRole('textbox', { name: 'Room name' })).toBeEnabled();
  await expect(
    page.getByRole('spinbutton', { name: 'Small blind' }),
  ).toBeDisabled();
});
