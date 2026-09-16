import { expect, test, type Page, type WebSocketRoute } from '@playwright/test';

import { activeRoomSnapshot } from '../src/test/roomSnapshots';
import type { RoomView } from '../src/realtime/messages';

interface ActionHarness {
  socket: WebSocketRoute;
  frames: Array<Record<string, unknown>>;
}

function checkBetSnapshot(): RoomView {
  const snapshot = activeRoomSnapshot();
  snapshot.active_hand!.legal_actions.kinds = ['fold', 'check', 'bet'];
  snapshot.active_hand!.legal_actions.call = null;
  snapshot.active_hand!.legal_actions.raise_to = null;
  snapshot.active_hand!.legal_actions.bet_to = {
    minimum_full_to: 200,
    maximum_to: 1_000,
    short_all_in_to: null,
  };
  return snapshot;
}

async function openActionRoom(
  page: Page,
  snapshot = activeRoomSnapshot(),
  guestId = 'guest_host',
): Promise<ActionHarness> {
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
  await form.getByRole('textbox', { name: 'Nickname' }).fill('Mara');
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

function pokerFrames(harness: ActionHarness) {
  return harness.frames.filter((frame) => frame.type !== 'connect');
}

test('FOLD sends one exact fold command', async ({ page }) => {
  const harness = await openActionRoom(page);
  await page.getByRole('button', { name: 'Fold' }).click();
  await expect.poll(() => pokerFrames(harness).length).toBe(1);
  expect(pokerFrames(harness)[0]).toEqual({
    type: 'fold',
    command_id: expect.any(String),
    hand_number: 1,
    expected_action_sequence: 2,
  });
});

test('CHECK sends once with current versions and does not mutate the table', async ({
  page,
}) => {
  const harness = await openActionRoom(page, checkBetSnapshot());
  const pot = page.getByLabel('Pot 250');
  const before = await pot.textContent();
  await page.getByRole('button', { name: 'Check' }).click();
  await expect.poll(() => pokerFrames(harness).length).toBe(1);
  const command = pokerFrames(harness)[0]!;
  expect(command).toEqual({
    type: 'check',
    command_id: expect.any(String),
    hand_number: 1,
    expected_action_sequence: 2,
  });
  expect(String(command.command_id)).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
  );
  await expect(pot).toHaveText(before ?? '');
  await expect(page.getByRole('button', { name: 'Check' })).toBeDisabled();
});

test('CALL labels from server chips and sends one exact call command', async ({
  page,
}) => {
  const snapshot = activeRoomSnapshot();
  snapshot.active_hand!.legal_actions.call!.chips = 37;
  snapshot.active_hand!.legal_actions.amount_to_call = 999;
  const harness = await openActionRoom(page, snapshot);
  await page.getByRole('button', { name: 'Call 37' }).click();
  await expect.poll(() => pokerFrames(harness).length).toBe(1);
  expect(pokerFrames(harness)[0]).toEqual({
    type: 'call',
    command_id: expect.any(String),
    hand_number: 1,
    expected_action_sequence: 2,
  });
});

test('BET sends the selected total-to rather than a delta', async ({
  page,
}) => {
  const harness = await openActionRoom(page, checkBetSnapshot());
  await page.getByRole('spinbutton', { name: 'Bet total' }).fill('650');
  await page.getByRole('button', { name: 'Bet 650' }).click();
  await expect.poll(() => pokerFrames(harness).length).toBe(1);
  expect(pokerFrames(harness)[0]).toMatchObject({ type: 'bet_to', total: 650 });
});

test('RAISE sends the selected total-to rather than a delta', async ({
  page,
}) => {
  const harness = await openActionRoom(page);
  await page.getByRole('spinbutton', { name: 'Raise total' }).fill('500');
  await page.getByRole('button', { name: 'Raise 500' }).click();
  await expect.poll(() => pokerFrames(harness).length).toBe(1);
  expect(pokerFrames(harness)[0]).toMatchObject({
    type: 'raise_to',
    total: 500,
  });
});

test('SHORT ALL-IN exposes and sends only the discrete total', async ({
  page,
}) => {
  const snapshot = activeRoomSnapshot();
  snapshot.active_hand!.legal_actions.raise_to = {
    minimum_full_to: 200,
    maximum_to: 150,
    short_all_in_to: 150,
  };
  const harness = await openActionRoom(page, snapshot);
  await expect(page.getByRole('slider')).toBeHidden();
  await expect(page.getByRole('button', { name: /Min/ })).toBeHidden();
  await expect(
    page.getByRole('spinbutton', { name: 'Raise total' }),
  ).toHaveValue('150');
  await page.getByRole('button', { name: 'Raise all-in 150' }).click();
  await expect.poll(() => pokerFrames(harness).length).toBe(1);
  expect(pokerFrames(harness)[0]).toMatchObject({
    type: 'raise_to',
    total: 150,
  });
});

test('RAPID DOUBLE ACTION sends only one command before rerender', async ({
  page,
}) => {
  const harness = await openActionRoom(page);
  await page.getByRole('button', { name: 'Call 50' }).evaluate((button) => {
    (button as HTMLButtonElement).click();
    (button as HTMLButtonElement).click();
  });
  await expect.poll(() => pokerFrames(harness).length).toBe(1);
});

test('STATE ADVANCEMENT resolves pending and late ack causes no resend', async ({
  page,
}) => {
  const harness = await openActionRoom(page);
  await page.getByRole('button', { name: 'Call 50' }).click();
  await expect.poll(() => pokerFrames(harness).length).toBe(1);
  const commandId = pokerFrames(harness)[0]!.command_id;
  const advanced = activeRoomSnapshot();
  advanced.active_hand!.action_sequence = 3;
  advanced.active_hand!.current_actor = 'guest_alice';
  advanced.active_hand!.legal_actions.actor = 'guest_alice';
  harness.socket.send(JSON.stringify({ type: 'state', snapshot: advanced }));
  await expect(page.getByText('Waiting for Alice')).toBeVisible();
  harness.socket.send(
    JSON.stringify({ type: 'command_ack', command_id: commandId }),
  );
  await expect(page.getByRole('button', { name: 'Call 50' })).toBeHidden();
  expect(pokerFrames(harness)).toHaveLength(1);
});

test('MATCHING COMMAND ERROR clears on legal retry and authoritative success', async ({
  page,
}) => {
  const harness = await openActionRoom(page);
  const pot = page.getByLabel('Pot 250');
  const before = await pot.textContent();
  await page.getByRole('button', { name: 'Call 50' }).click();
  await expect.poll(() => pokerFrames(harness).length).toBe(1);
  harness.socket.send(
    JSON.stringify({
      type: 'command_error',
      command_id: pokerFrames(harness)[0]!.command_id,
      code: 'illegal_call',
      message: 'private server detail',
    }),
  );
  await expect(page.getByText('Calling is not available now.')).toBeVisible();
  await expect(page.locator('body')).not.toContainText('private server detail');
  await expect(page.getByRole('button', { name: 'Call 50' })).toBeEnabled();
  await expect(pot).toHaveText(before ?? '');

  await page.getByRole('button', { name: 'Call 50' }).click();
  await expect.poll(() => pokerFrames(harness).length).toBe(2);
  await expect(page.getByText('Calling is not available now.')).toBeHidden();
  await expect(pot).toHaveText(before ?? '');

  const advanced = activeRoomSnapshot();
  advanced.active_hand!.action_sequence = 3;
  advanced.active_hand!.current_actor = 'guest_alice';
  advanced.active_hand!.legal_actions.actor = 'guest_alice';
  harness.socket.send(JSON.stringify({ type: 'state', snapshot: advanced }));
  await expect(page.getByText('Waiting for Alice')).toBeVisible();
  await expect(page.getByText('Calling is not available now.')).toBeHidden();
});

test('NON-ACTOR has no action controls and sends nothing', async ({ page }) => {
  const harness = await openActionRoom(
    page,
    activeRoomSnapshot(),
    'guest_alice',
  );
  await expect(page.getByText('Waiting for Mara')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Fold' })).toBeHidden();
  expect(pokerFrames(harness)).toEqual([]);
});

test('DISCONNECT keeps stale table visible with no actionable controls', async ({
  page,
}) => {
  const harness = await openActionRoom(page);
  await harness.socket.close({ code: 1012 });
  await expect(page.getByText('Disconnected · stale table')).toBeVisible();
  await expect(page.getByLabel('Pot 250')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Fold' })).toBeHidden();
  expect(pokerFrames(harness)).toEqual([]);
});
