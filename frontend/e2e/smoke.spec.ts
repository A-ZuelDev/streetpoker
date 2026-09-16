import {
  expect,
  test,
  type Locator,
  type Page,
  type WebSocketRoute,
} from '@playwright/test';

import { activeRoomSnapshot } from '../src/test/roomSnapshots';

async function requiredBox(locator: Locator, label: string) {
  const box = await locator.boundingBox();

  if (box === null) {
    throw new Error(`${label} does not have a visible bounding box`);
  }

  return box;
}

async function expectContributionsInsideFelt(page: Page) {
  const feltBox = await requiredBox(
    page.getByTestId('poker-felt'),
    'Poker felt',
  );
  const contributions = page.locator('[data-seat-contribution]');
  const contributionCount = await contributions.count();
  const safetyMargin = 4;

  expect(contributionCount).toBe(5);

  for (let index = 0; index < contributionCount; index += 1) {
    const contributionBox = await requiredBox(
      contributions.nth(index),
      `Contribution ${index + 1}`,
    );

    expect(contributionBox.x).toBeGreaterThanOrEqual(feltBox.x + safetyMargin);
    expect(contributionBox.y).toBeGreaterThanOrEqual(feltBox.y + safetyMargin);
    expect(contributionBox.x + contributionBox.width).toBeLessThanOrEqual(
      feltBox.x + feltBox.width - safetyMargin,
    );
    expect(contributionBox.y + contributionBox.height).toBeLessThanOrEqual(
      feltBox.y + feltBox.height - safetyMargin,
    );
  }

  const heroContributionBox = await requiredBox(
    page.getByLabel('Mara current contribution 250'),
    'Hero contribution',
  );
  const heroCardsBox = await requiredBox(
    page.getByTestId('hero-hole-cards'),
    'Hero hole cards',
  );
  const heroCardsOverlapContribution =
    heroContributionBox.x < heroCardsBox.x + heroCardsBox.width &&
    heroContributionBox.x + heroContributionBox.width > heroCardsBox.x &&
    heroContributionBox.y < heroCardsBox.y + heroCardsBox.height &&
    heroContributionBox.y + heroContributionBox.height > heroCardsBox.y;

  expect(heroCardsOverlapContribution).toBe(false);
  expect(
    heroContributionBox.y + heroContributionBox.height,
  ).toBeLessThanOrEqual(heroCardsBox.y);
}

async function expectHeroCardsTuckedBehindPod(page: Page) {
  const heroCards = page.getByTestId('hero-hole-cards');
  const heroPod = page.getByTestId('hero-player-pod');
  const actionBar = page.getByRole('region', { name: 'Table actions' });
  const heroCardsBox = await requiredBox(heroCards, 'Hero hole cards');
  const heroPodBox = await requiredBox(heroPod, 'Hero player pod');
  const actionBarBox = await requiredBox(actionBar, 'Action bar');

  expect(heroCardsBox.y).toBeLessThan(heroPodBox.y);
  expect(heroCardsBox.y + heroCardsBox.height).toBeGreaterThan(heroPodBox.y);
  expect(heroCardsBox.y + heroCardsBox.height).toBeLessThan(actionBarBox.y);

  const cardLayer = await heroCards.evaluate((element) =>
    Number.parseInt(window.getComputedStyle(element).zIndex, 10),
  );
  const podLayer = await heroPod.evaluate((element) =>
    Number.parseInt(window.getComputedStyle(element).zIndex, 10),
  );

  expect(podLayer).toBeGreaterThan(cardLayer);
}

test('loads the active poker shell without horizontal overflow', async ({
  page,
}, testInfo) => {
  await page.goto('/?demo=active');

  await expect(
    page.getByRole('heading', { name: 'StreetPoker' }),
  ).toBeVisible();
  await expect(page.getByText('Demo table')).toBeVisible();
  await expect(page.getByText('Backend online')).toBeVisible();
  await expect(
    page.getByRole('region', { name: 'Six-max poker table' }),
  ).toBeVisible();
  await expect(page.getByLabel('Seat 1: Mara')).toBeVisible();
  await expect(
    page.getByRole('region', { name: 'Table actions' }),
  ).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Members' })).toBeVisible();
  await expect(
    page.getByRole('region', { name: 'Chat preview' }),
  ).toBeVisible();

  const stage = page.getByRole('region', { name: 'Six-max poker table' });
  const openStageWidth = await stage.evaluate(
    (element) => element.getBoundingClientRect().width,
  );

  await expectContributionsInsideFelt(page);
  await expectHeroCardsTuckedBehindPod(page);

  const pageWidth = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(pageWidth.scrollWidth).toBeLessThanOrEqual(pageWidth.clientWidth + 1);

  await page.screenshot({
    path: testInfo.outputPath('active-panel-open.png'),
    fullPage: true,
  });

  await page.getByRole('button', { name: 'Collapse Members section' }).click();
  await expect(
    page.getByRole('button', { name: 'Expand Members section' }),
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Expand Seat requests section' }),
  ).toBeVisible();
  await page
    .getByRole('button', { name: 'Expand Seat requests section' })
    .click();
  await expect(page.getByText('No pending requests')).toBeVisible();
  await page
    .getByRole('button', { name: 'Collapse Seat requests section' })
    .click();
  await expect(
    page.getByRole('button', { name: 'Expand Seat requests section' }),
  ).toBeVisible();

  await page
    .getByRole('button', { name: 'Expand Host controls section' })
    .click();
  await expect(
    page.getByRole('button', { name: 'Room settings' }),
  ).toBeVisible();
  await page
    .getByRole('button', { name: 'Collapse Host controls section' })
    .click();
  await expect(
    page.getByRole('button', { name: 'Expand Host controls section' }),
  ).toBeVisible();

  await page
    .getByRole('button', { name: 'Collapse Chat preview section' })
    .click();
  await expect(
    page.getByRole('textbox', { name: 'Chat message' }),
  ).toBeHidden();
  await page
    .getByRole('button', { name: 'Expand Chat preview section' })
    .click();
  await expect(
    page.getByRole('textbox', { name: 'Chat message' }),
  ).toBeVisible();

  await page.screenshot({
    path: testInfo.outputPath('active-sections-collapsed-chat-open.png'),
    fullPage: true,
  });

  await page.getByRole('button', { name: 'Collapse room panel' }).click();
  await expect(
    page.getByRole('button', { name: 'Expand room panel' }),
  ).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Members' })).toBeHidden();
  await expect
    .poll(() =>
      stage.evaluate((element) => element.getBoundingClientRect().width),
    )
    .toBeGreaterThan(openStageWidth + 100);

  const collapsedPageWidth = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(collapsedPageWidth.scrollWidth).toBeLessThanOrEqual(
    collapsedPageWidth.clientWidth + 1,
  );
  await expectContributionsInsideFelt(page);
  await expectHeroCardsTuckedBehindPod(page);

  await page.screenshot({
    path: testInfo.outputPath('active-panel-closed.png'),
    fullPage: true,
  });
});

test('loads the open table demo and host room state', async ({
  page,
}, testInfo) => {
  await page.goto('/?demo=open');

  await expect(page.getByText('Table ready')).toBeVisible();
  await expect(page.getByText('Backend online')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Start hand' })).toBeVisible();
  await expect(
    page
      .getByRole('region', { name: 'Seat requests' })
      .getByText('Ember', { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Approve' })).toBeVisible();
  await expect(
    page.getByRole('region', { name: 'Chat preview' }),
  ).toBeVisible();
  await expect(
    page.getByRole('textbox', { name: 'Chat message' }),
  ).toBeVisible();

  const pageWidth = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(pageWidth.scrollWidth).toBeLessThanOrEqual(pageWidth.clientWidth + 1);

  await page.screenshot({
    path: testInfo.outputPath('open-room-demo.png'),
    fullPage: true,
  });
});

test('stacks live notices and keeps action errors below wrapped controls', async ({
  page,
}) => {
  await page.addInitScript(() => {
    const originalSetItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key === 'streetpoker.guest-token.v1') {
        throw new DOMException('Storage blocked', 'SecurityError');
      }
      return originalSetItem.call(this, key, value);
    };
  });
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
  const snapshot = activeRoomSnapshot();
  snapshot.room.members.push({
    guest_id: 'guest_observer_private',
    nickname: 'Observer',
    status: 'in_room',
    is_host: false,
    stack: null,
  });
  socket.send(
    JSON.stringify({
      type: 'connected',
      guest_id: 'guest_host',
      room_code: 'ABCDEFGH',
    }),
  );
  socket.send(JSON.stringify({ type: 'state', snapshot }));
  await expect(page.getByText('Table live')).toBeVisible();
  await expect(
    page.getByText(
      'Your browser session will not persist after this page closes.',
    ),
  ).toBeVisible();

  await page.getByRole('button', { name: 'Call 50' }).click();
  await expect.poll(() => frames.length).toBe(2);
  socket.send(
    JSON.stringify({
      type: 'command_error',
      command_id: frames[1]!.command_id,
      code: 'illegal_call',
      message: 'private action detail',
    }),
  );
  const feedback = page.locator('.action-feedback');
  await expect(feedback).toHaveText('Calling is not available now.');
  expect(
    await feedback.evaluate((element) => getComputedStyle(element).position),
  ).toBe('static');
  const feedbackBox = await requiredBox(feedback, 'Action feedback');
  const actionButtons = page.locator(
    '.action-bar > .action-button, .action-buttons',
  );
  for (let index = 0; index < (await actionButtons.count()); index += 1) {
    const controlsBox = await requiredBox(
      actionButtons.nth(index),
      `Action controls ${index + 1}`,
    );
    expect(feedbackBox.y).toBeGreaterThanOrEqual(
      controlsBox.y + controlsBox.height - 1,
    );
  }

  await page.getByRole('button', { name: 'Kick Observer' }).click();
  await page.getByRole('button', { name: 'Confirm kick Observer' }).click();
  await expect.poll(() => frames.length).toBe(3);
  socket.send(
    JSON.stringify({
      type: 'command_error',
      command_id: frames[2]!.command_id,
      code: 'member_not_found',
      message: 'private member detail',
    }),
  );
  await expect(
    page.getByText('That room member is no longer available.'),
  ).toBeVisible();
  await socket.close({ code: 1012 });
  await expect(
    page.getByText('Disconnected. The displayed table is stale.'),
  ).toBeVisible();

  const noticeBoxes = await page
    .locator('.table-notices .session-banner')
    .evaluateAll((elements) =>
      elements.map((element) => {
        const box = element.getBoundingClientRect();
        return { top: box.top, bottom: box.bottom };
      }),
    );
  expect(noticeBoxes).toHaveLength(3);
  for (let index = 1; index < noticeBoxes.length; index += 1) {
    expect(noticeBoxes[index]!.top).toBeGreaterThanOrEqual(
      noticeBoxes[index - 1]!.bottom,
    );
  }
  await expect(page.locator('body')).not.toContainText('private action detail');
  await expect(page.locator('body')).not.toContainText('private member detail');
});

test('keeps expanded room sections separated under vertical pressure', async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.goto('/?demo=open');
  await expect(page.getByText('Backend online')).toBeVisible();

  await page
    .getByRole('button', { name: 'Expand Host controls section' })
    .click();

  const geometry = await page.evaluate(() => {
    const panelBody = document.querySelector<HTMLElement>('.room-panel__body');
    const chatSection =
      document.querySelector<HTMLElement>('.room-panel__chat');
    const sendButton = document.querySelector<HTMLElement>(
      '.chat-compose button',
    );
    const hostSection = document.querySelector<HTMLElement>(
      '.room-panel__host-controls',
    );
    const sections = Array.from(
      document.querySelectorAll<HTMLElement>('.room-panel__section'),
    );

    if (
      panelBody === null ||
      chatSection === null ||
      sendButton === null ||
      hostSection === null
    ) {
      throw new Error('Expected expanded room panel content');
    }

    const chatBox = chatSection.getBoundingClientRect();
    const sendBox = sendButton.getBoundingClientRect();
    const hostBox = hostSection.getBoundingClientRect();

    return {
      chatBottom: chatBox.bottom,
      sendBottom: sendBox.bottom,
      hostTop: hostBox.top,
      overflowY: window.getComputedStyle(panelBody).overflowY,
      panelClientHeight: panelBody.clientHeight,
      panelScrollHeight: panelBody.scrollHeight,
      sectionsFitContents: sections.every(
        (section) => section.clientHeight + 1 >= section.scrollHeight,
      ),
    };
  });

  expect(geometry.chatBottom).toBeLessThanOrEqual(geometry.hostTop + 1);
  expect(geometry.sendBottom).toBeLessThanOrEqual(geometry.hostTop + 1);
  expect(geometry.sectionsFitContents).toBe(true);
  expect(geometry.overflowY).toBe('auto');
  expect(geometry.panelScrollHeight).toBeGreaterThan(
    geometry.panelClientHeight,
  );

  const hostControls = page.locator('.room-panel__host-controls');
  await hostControls.scrollIntoViewIfNeeded();
  await expect(
    page.getByRole('button', { name: 'Room settings' }),
  ).toBeVisible();

  await page.screenshot({
    path: testInfo.outputPath('expanded-room-sections-1366x768.png'),
    fullPage: true,
  });
});

test('uses the full stage width under the 1120px overlay breakpoint', async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto('/?demo=active');
  await expect(page.getByText('Backend online')).toBeVisible();

  const stage = page.getByRole('region', { name: 'Six-max poker table' });
  const panel = page.getByRole('complementary', { name: 'Room tools' });
  const actionBar = page.getByRole('region', { name: 'Table actions' });
  const stageBox = await requiredBox(stage, 'Poker stage');
  await expect(
    page.getByRole('button', { name: 'Expand room panel' }),
  ).toBeVisible();
  const closedPanelBox = await requiredBox(panel, 'Closed room panel');
  const actionBarBox = await requiredBox(actionBar, 'Action bar');

  const viewport = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));

  expect(stageBox.width).toBeGreaterThanOrEqual(viewport.clientWidth - 1);
  expect(closedPanelBox.width).toBeLessThanOrEqual(45);
  expect(actionBarBox.width).toBeGreaterThanOrEqual(viewport.clientWidth - 1);
  expect(closedPanelBox.y + closedPanelBox.height).toBeLessThanOrEqual(
    actionBarBox.y + 1,
  );
  expect(viewport.scrollWidth).toBeLessThanOrEqual(viewport.clientWidth + 1);

  await page.screenshot({
    path: testInfo.outputPath('panel-overlay-default-closed-1024x768.png'),
    fullPage: true,
  });

  await page.getByRole('button', { name: 'Expand room panel' }).click();
  const openPanelBox = await requiredBox(panel, 'Open room panel');
  expect(openPanelBox.x).toBeLessThan(stageBox.x + stageBox.width);
  expect(openPanelBox.y + openPanelBox.height).toBeLessThanOrEqual(
    actionBarBox.y + 1,
  );
  await page.getByRole('button', { name: 'Collapse room panel' }).click();
  await expect(
    page.getByRole('button', { name: 'Expand room panel' }),
  ).toBeVisible();
  await expect
    .poll(() =>
      panel.evaluate((element) => element.getBoundingClientRect().width),
    )
    .toBeLessThanOrEqual(45);

  for (const position of ['upper-right', 'lower-right']) {
    const seatBox = await requiredBox(
      page.locator(`.table-seat--${position}`),
      `${position} seat`,
    );
    expect(seatBox.x).toBeGreaterThanOrEqual(stageBox.x);
    expect(seatBox.x + seatBox.width).toBeLessThanOrEqual(
      stageBox.x + stageBox.width + 1,
    );
  }

  await expect(page.getByRole('button', { name: 'Fold' })).toBeVisible();

  await page.screenshot({
    path: testInfo.outputPath('panel-overlay-closed-1024x768.png'),
    fullPage: true,
  });
});

test('keeps the overlay above a wrapped action bar', async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 900, height: 768 });
  await page.goto('/?demo=active');
  await expect(page.getByText('Backend online')).toBeVisible();

  const panel = page.getByRole('complementary', { name: 'Room tools' });
  const actionBar = page.getByRole('region', { name: 'Table actions' });
  await page.getByRole('button', { name: 'Expand room panel' }).click();
  const panelBox = await requiredBox(panel, 'Room panel');
  const actionBarBox = await requiredBox(actionBar, 'Wrapped action bar');

  expect(panelBox.y + panelBox.height).toBeLessThanOrEqual(actionBarBox.y + 1);

  const amount = page.getByRole('spinbutton', {
    name: 'Bet or raise amount',
  });
  await expect(amount).toBeVisible();
  await expect(
    page.getByRole('slider', { name: 'Bet or raise amount slider' }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Min 1,200' }).click();
  await expect(amount).toHaveValue('1200');
  await page.getByRole('button', { name: 'Max 8,100' }).click();
  await expect(amount).toHaveValue('8100');
  await expect(page.getByRole('button', { name: 'Raise 8,100' })).toBeVisible();

  const viewport = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(viewport.scrollWidth).toBeLessThanOrEqual(viewport.clientWidth + 1);

  await page.screenshot({
    path: testInfo.outputPath('wrapped-action-overlay-900x768.png'),
    fullPage: true,
  });
});

test('keeps critical table content reachable at phone width', async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 448, height: 800 });
  await page.goto('/?demo=active');
  await expect(page.getByText('Backend online')).toBeVisible();

  const panel = page.getByRole('complementary', { name: 'Room tools' });
  await expect(
    page.getByRole('button', { name: 'Expand room panel' }),
  ).toBeVisible();
  await expect
    .poll(() =>
      panel.evaluate((element) => element.getBoundingClientRect().width),
    )
    .toBeLessThanOrEqual(45);

  const criticalControls = [
    page.getByRole('button', { name: 'Fold' }),
    page.getByRole('button', { name: 'Call 350' }),
    page.getByRole('spinbutton', { name: 'Bet or raise amount' }),
    page.getByRole('slider', { name: 'Bet or raise amount slider' }),
    page.getByRole('button', { name: 'Min 1,200' }),
    page.getByRole('button', { name: 'Max 8,100' }),
    page.getByRole('button', { name: 'Raise 2,400' }),
  ];

  for (const control of criticalControls) {
    await expect(control).toBeVisible();
    await expect(control).toBeInViewport();
  }

  await page.getByRole('button', { name: 'Min 1,200' }).click();
  await expect(
    page.getByRole('spinbutton', { name: 'Bet or raise amount' }),
  ).toHaveValue('1200');
  await expect(page.getByRole('button', { name: 'Raise 1,200' })).toBeVisible();
  await expect(page.getByTestId('hero-player-pod')).toBeInViewport();

  const stageBox = await requiredBox(
    page.getByRole('region', { name: 'Six-max poker table' }),
    'Phone-width poker stage',
  );
  for (const position of ['upper-right', 'lower-right']) {
    const seatBox = await requiredBox(
      page.locator(`.table-seat--${position}`),
      `${position} phone-width seat`,
    );
    expect(seatBox.x).toBeGreaterThanOrEqual(stageBox.x);
    expect(seatBox.x + seatBox.width).toBeLessThanOrEqual(
      stageBox.x + stageBox.width + 1,
    );
  }

  const viewport = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(viewport.scrollWidth).toBeLessThanOrEqual(viewport.clientWidth + 1);

  await page.screenshot({
    path: testInfo.outputPath('safe-phone-fallback-448x800.png'),
    fullPage: true,
  });
});

test('keeps the hero and board clear at short phone sizes', async ({
  page,
}, testInfo) => {
  for (const viewport of [
    { width: 375, height: 667 },
    { width: 320, height: 568 },
  ]) {
    await page.setViewportSize(viewport);
    await page.goto('/?demo=active');
    await expect(page.getByText('Backend online')).toBeVisible();

    const panel = page.getByRole('complementary', { name: 'Room tools' });
    await expect(
      page.getByRole('button', { name: 'Expand room panel' }),
    ).toBeVisible();
    await expect
      .poll(() =>
        panel.evaluate((element) => element.getBoundingClientRect().width),
      )
      .toBeLessThanOrEqual(45);

    const stage = page.getByRole('region', { name: 'Six-max poker table' });
    const actionBar = page.getByRole('region', { name: 'Table actions' });
    const heroPod = page.getByTestId('hero-player-pod');
    const heroCards = page.getByTestId('hero-hole-cards');
    const stageBox = await requiredBox(stage, 'Short phone poker stage');
    const actionBarBox = await requiredBox(actionBar, 'Short phone action bar');
    const heroPodBox = await requiredBox(heroPod, 'Short phone hero pod');
    const heroCardsBox = await requiredBox(heroCards, 'Short phone hero cards');
    const heroContributionBox = await requiredBox(
      page.getByLabel('Mara current contribution 250'),
      'Short phone hero contribution',
    );

    expect(heroPodBox.y + heroPodBox.height).toBeLessThanOrEqual(
      actionBarBox.y + 1,
    );
    expect(heroCardsBox.y).toBeLessThan(heroPodBox.y);
    expect(heroCardsBox.y + heroCardsBox.height).toBeLessThanOrEqual(
      actionBarBox.y + 1,
    );
    await expect(heroPod).toBeInViewport();
    await expect(heroCards).toBeInViewport();
    const heroCardsOverlapContribution =
      heroContributionBox.x < heroCardsBox.x + heroCardsBox.width &&
      heroContributionBox.x + heroContributionBox.width > heroCardsBox.x &&
      heroContributionBox.y < heroCardsBox.y + heroCardsBox.height &&
      heroContributionBox.y + heroContributionBox.height > heroCardsBox.y;
    expect(heroCardsOverlapContribution).toBe(false);

    for (const control of [
      page.getByRole('button', { name: 'Fold' }),
      page.getByRole('button', { name: 'Call 350' }),
      page.getByRole('button', { name: 'Raise 2,400' }),
    ]) {
      await expect(control).toBeVisible();
      await expect(control).toBeInViewport();
    }

    if (viewport.width === 320) {
      const board = page.getByLabel('Community board');
      const boardBox = await requiredBox(board, 'Narrow community board');
      const boardCards = board.locator('.playing-card');
      const streetBox = await requiredBox(
        page.locator('.table-center__street'),
        'Narrow table street',
      );
      const potBox = await requiredBox(
        page.locator('.pot-display'),
        'Narrow table pot',
      );

      expect(await boardCards.count()).toBe(5);
      expect(boardBox.x).toBeGreaterThanOrEqual(stageBox.x);
      expect(boardBox.x + boardBox.width).toBeLessThanOrEqual(
        stageBox.x + stageBox.width + 1,
      );
      for (const position of ['upper-left', 'top-center', 'upper-right']) {
        const seatBox = await requiredBox(
          page.locator(`.table-seat--${position}`),
          `${position} narrow seat`,
        );
        expect(seatBox.y + seatBox.height).toBeLessThanOrEqual(boardBox.y + 1);
      }
      expect(streetBox.y + streetBox.height).toBeLessThanOrEqual(boardBox.y);
      expect(boardBox.y + boardBox.height).toBeLessThanOrEqual(potBox.y);
    }

    const viewportWidth = await page.evaluate(() => ({
      clientWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
    }));
    expect(viewportWidth.scrollWidth).toBeLessThanOrEqual(
      viewportWidth.clientWidth + 1,
    );

    await page.screenshot({
      path: testInfo.outputPath(
        `short-phone-${viewport.width}x${viewport.height}.png`,
      ),
      fullPage: true,
    });
  }
});
