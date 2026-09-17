import {
  expect,
  test as base,
  type Browser,
  type BrowserContext,
  type Page,
  type WebSocketRoute,
} from '@playwright/test';

import { GUEST_TOKEN_STORAGE_KEY } from '../src/realtime/guestToken';
import type { RoomView } from '../src/realtime/messages';
import {
  activeRoomSnapshot,
  completedRoomSnapshot,
  openRoomSnapshot,
} from '../src/test/roomSnapshots';

interface ClientHarness {
  socket: WebSocketRoute;
  frames: Array<Record<string, unknown>>;
}

async function createIsolatedGuestContext(
  browser: Browser,
  hostPage: Page,
  baseURL: string | undefined,
): Promise<BrowserContext> {
  const viewport = hostPage.viewportSize();
  return browser.newContext({
    ...(baseURL === undefined ? {} : { baseURL }),
    ...(viewport === null ? {} : { viewport }),
  });
}

const test = base.extend<{ guestPage: Page }>({
  guestPage: async ({ baseURL, browser, page }, provide) => {
    const context = await createIsolatedGuestContext(browser, page, baseURL);
    try {
      const guestPage = await context.newPage();
      await provide(guestPage);
    } finally {
      await context.close();
    }
  },
});

test.describe.configure({ timeout: 45_000 });

async function expectIndependentGuestTokens(
  hostPage: Page,
  guestPage: Page,
): Promise<void> {
  const [hostToken, guestToken] = await Promise.all(
    [hostPage, guestPage].map((client) =>
      client.evaluate(
        (storageKey) => localStorage.getItem(storageKey),
        GUEST_TOKEN_STORAGE_KEY,
      ),
    ),
  );
  expect(hostToken !== null).toBe(true);
  expect(guestToken !== null).toBe(true);
  expect(hostToken === guestToken).toBe(false);
}

function seatingSnapshot(state: 'unseated' | 'requested' | 'seated'): RoomView {
  const snapshot = openRoomSnapshot();
  snapshot.room.members = [
    {
      guest_id: 'guest_host_private',
      nickname: 'Mara',
      status: 'seated',
      is_host: true,
      stack: 10_000,
    },
    {
      guest_id: 'guest_alice_private',
      nickname: 'Alice',
      status: state === 'seated' ? 'seated' : 'in_room',
      is_host: false,
      stack: state === 'seated' ? 10_000 : null,
    },
  ];
  snapshot.room.host_guest_id = 'guest_host_private';
  snapshot.room.seats = [
    {
      seat_index: 0,
      guest_id: 'guest_host_private',
      nickname: 'Mara',
      stack: 10_000,
    },
    state === 'seated'
      ? {
          seat_index: 1,
          guest_id: 'guest_alice_private',
          nickname: 'Alice',
          stack: 10_000,
        }
      : { seat_index: 1, guest_id: null, nickname: null, stack: null },
    { seat_index: 2, guest_id: null, nickname: null, stack: null },
    { seat_index: 3, guest_id: null, nickname: null, stack: null },
    { seat_index: 4, guest_id: null, nickname: null, stack: null },
    { seat_index: 5, guest_id: null, nickname: null, stack: null },
  ];
  snapshot.room.seat_requests =
    state === 'requested'
      ? [
          {
            guest_id: 'guest_alice_private',
            nickname: 'Alice',
            seat_index: 1,
          },
        ]
      : [];
  return snapshot;
}

function activeViews(
  actor: 'guest_host_private' | 'guest_alice_private',
  actionSequence: number,
): { host: RoomView; guest: RoomView } {
  const base = activeRoomSnapshot();
  base.room.host_guest_id = 'guest_host_private';
  base.room.members = seatingSnapshot('seated').room.members;
  base.room.seats = seatingSnapshot('seated').room.seats;
  base.next_hand_number = 17;
  base.active_hand!.hand_number = 16;
  base.active_hand!.action_sequence = actionSequence;
  base.active_hand!.current_actor = actor;
  base.active_hand!.legal_actions.actor = actor;
  base.active_hand!.players[0]!.guest_id = 'guest_host_private';
  base.active_hand!.players[0]!.nickname = 'Mara';
  base.active_hand!.players[0]!.status = 'active';
  base.active_hand!.players[1]!.guest_id = 'guest_alice_private';
  base.active_hand!.players[1]!.nickname = 'Alice';
  base.active_hand!.players[1]!.status = 'active';

  const host = structuredClone(base);
  host.active_hand!.players[0]!.hole_cards = [
    { rank: 'king', suit: 'spades' },
    { rank: 'queen', suit: 'spades' },
  ];
  host.active_hand!.players[1]!.hole_cards = null;

  const guest = structuredClone(base);
  guest.active_hand!.players[0]!.hole_cards = null;
  guest.active_hand!.players[1]!.hole_cards = [
    { rank: 'ace', suit: 'diamonds' },
    { rank: 'ace', suit: 'clubs' },
  ];
  return { host, guest };
}

function settlementSnapshot(): RoomView {
  const snapshot = completedRoomSnapshot();
  snapshot.room.host_guest_id = 'guest_host_private';
  snapshot.room.members = seatingSnapshot('seated').room.members.map(
    (member, index) => ({ ...member, stack: index === 0 ? 10_250 : 9_750 }),
  );
  snapshot.room.seats = seatingSnapshot('seated').room.seats.map(
    (seat, index) =>
      seat.guest_id === null
        ? seat
        : { ...seat, stack: index === 0 ? 10_250 : 9_750 },
  );
  snapshot.next_hand_number = 17;
  snapshot.last_hand!.hand_number = 16;
  snapshot.last_hand!.players[0]!.guest_id = 'completed-host-private-id';
  snapshot.last_hand!.players[0]!.nickname = 'Mara';
  snapshot.last_hand!.players[0]!.total_award = 350;
  snapshot.last_hand!.players[0]!.final_stack = 10_250;
  snapshot.last_hand!.players[1]!.guest_id = 'completed-guest-private-id';
  snapshot.last_hand!.players[1]!.nickname = 'Alice';
  snapshot.last_hand!.players[1]!.final_stack = 9_750;
  snapshot.last_hand!.pots[0]!.amount = 350;
  snapshot.last_hand!.pots[0]!.winners[0]!.guest_id =
    'completed-host-private-id';
  snapshot.last_hand!.pots[0]!.winners[0]!.chips = 350;
  return snapshot;
}

async function openClient(
  page: Page,
  snapshot: RoomView,
  guestId: string,
  nickname: string,
): Promise<ClientHarness> {
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
  await form.getByRole('textbox', { name: 'Nickname' }).fill(nickname);
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

function commands(harness: ClientHarness) {
  return harness.frames.filter((frame) => frame.type !== 'connect');
}

async function sendAck(harness: ClientHarness) {
  const command = commands(harness).at(-1)!;
  harness.socket.send(
    JSON.stringify({ type: 'command_ack', command_id: command.command_id }),
  );
}

test('host and guest converge through authoritative seat approval', async ({
  guestPage,
  page,
}) => {
  const initial = seatingSnapshot('unseated');
  const host = await openClient(page, initial, 'guest_host_private', 'Mara');
  const guest = await openClient(
    guestPage,
    initial,
    'guest_alice_private',
    'Alice',
  );
  await expectIndependentGuestTokens(page, guestPage);

  await guestPage.getByRole('button', { name: 'Request seat 2' }).click();
  await expect.poll(() => commands(guest).length).toBe(1);
  await expect(guestPage.getByText('Requesting seat 2…')).toBeVisible();
  await expect(guestPage.getByLabel('Seat 2: Alice')).toBeHidden();
  await sendAck(guest);
  await expect(
    guestPage.getByText('Waiting for the table to update…'),
  ).toBeVisible();

  const requested = seatingSnapshot('requested');
  host.socket.send(JSON.stringify({ type: 'state', snapshot: requested }));
  guest.socket.send(JSON.stringify({ type: 'state', snapshot: requested }));
  await expect(page.getByText('Alice').first()).toBeVisible();
  await expect(
    guestPage.getByRole('button', { name: 'Seat 2 requested' }),
  ).toBeDisabled();

  await page.getByRole('button', { name: 'Approve' }).click();
  await expect.poll(() => commands(host).length).toBe(1);
  expect(commands(host)[0]).toMatchObject({
    type: 'approve_seat',
    target_guest_id: 'guest_alice_private',
  });
  await expect(page.getByRole('button', { name: 'Approve' })).toBeVisible();

  const seated = seatingSnapshot('seated');
  host.socket.send(JSON.stringify({ type: 'state', snapshot: seated }));
  guest.socket.send(JSON.stringify({ type: 'state', snapshot: seated }));
  await expect(page.getByLabel('Seat 2: Alice')).toBeVisible();
  await expect(guestPage.getByLabel('Seat 2: Alice')).toContainText(
    'You · Seat 2',
  );
  await expect(
    guestPage.getByRole('heading', { name: 'Host controls' }),
  ).toBeHidden();
  await expect(
    page.getByRole('heading', { name: 'Host controls' }),
  ).toBeVisible();

  for (const client of [page, guestPage]) {
    await expect(client.locator('body')).not.toContainText(
      'guest_host_private',
    );
    await expect(client.locator('body')).not.toContainText(
      'guest_alice_private',
    );
  }
});

test('one hand settles safely and uses the authoritative next hand number', async ({
  guestPage,
  page,
}) => {
  const initial = seatingSnapshot('seated');
  initial.next_hand_number = 16;
  const host = await openClient(page, initial, 'guest_host_private', 'Mara');
  const guest = await openClient(
    guestPage,
    initial,
    'guest_alice_private',
    'Alice',
  );
  await expectIndependentGuestTokens(page, guestPage);

  await page
    .getByRole('region', { name: 'Table actions' })
    .getByRole('button', { name: 'Start hand' })
    .click();
  await expect.poll(() => commands(host).length).toBe(1);
  expect(commands(host)[0]).toMatchObject({
    type: 'start_hand',
    hand_number: 16,
  });
  await expect(
    page.getByLabel('Table notices').getByText('Starting hand…'),
  ).toBeVisible();
  await expect(page.getByLabel('Pot 0')).toBeVisible();
  await sendAck(host);
  await expect(
    page
      .getByLabel('Table notices')
      .getByText('Waiting for the table to update…'),
  ).toBeVisible();

  const firstAction = activeViews('guest_host_private', 2);
  host.socket.send(
    JSON.stringify({ type: 'state', snapshot: firstAction.host }),
  );
  guest.socket.send(
    JSON.stringify({ type: 'state', snapshot: firstAction.guest }),
  );
  await expect(page.getByRole('img', { name: 'K of spades' })).toBeVisible();
  await expect(page.getByRole('img', { name: 'A of diamonds' })).toBeHidden();
  await expect(
    guestPage.getByRole('img', { name: 'A of diamonds' }),
  ).toBeVisible();
  await expect(
    guestPage.getByRole('img', { name: 'K of spades' }),
  ).toBeHidden();

  const potBefore = await page.getByLabel('Pot 250').textContent();
  await page.getByRole('button', { name: 'Call 50' }).click();
  await expect.poll(() => commands(host).length).toBe(2);
  await expect(page.getByLabel('Pot 250')).toHaveText(potBefore ?? '');

  const secondAction = activeViews('guest_alice_private', 3);
  host.socket.send(
    JSON.stringify({ type: 'state', snapshot: secondAction.host }),
  );
  guest.socket.send(
    JSON.stringify({ type: 'state', snapshot: secondAction.guest }),
  );
  await expect(page.getByText('Waiting for Alice')).toBeVisible();
  await guestPage.getByRole('button', { name: 'Fold' }).click();
  await expect.poll(() => commands(guest).length).toBe(1);

  const settlement = settlementSnapshot();
  host.socket.send(JSON.stringify({ type: 'state', snapshot: settlement }));
  guest.socket.send(JSON.stringify({ type: 'state', snapshot: settlement }));
  await expect(
    page.getByRole('heading', { name: 'Hand 16 complete' }),
  ).toBeVisible();
  await expect(page.getByText('+350')).toBeVisible();
  await expect(page.getByText('Stacks updated')).toBeVisible();
  await expect(
    guestPage.getByText('Waiting for host to start the next hand'),
  ).toBeVisible();
  for (const client of [page, guestPage]) {
    await expect(client.locator('body')).not.toContainText(
      'completed-host-private-id',
    );
    await expect(client.locator('body')).not.toContainText(
      'completed-guest-private-id',
    );
    await expect(client.getByRole('img', { name: 'K of spades' })).toBeHidden();
    await expect(
      client.getByRole('img', { name: 'A of diamonds' }),
    ).toBeHidden();
  }

  await page
    .getByRole('region', { name: 'Table actions' })
    .getByRole('button', { name: 'Start next hand' })
    .click();
  await expect.poll(() => commands(host).length).toBe(3);
  expect(commands(host)[2]).toMatchObject({
    type: 'start_hand',
    hand_number: 17,
  });
});
